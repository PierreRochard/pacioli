from fabric.context_managers import shell_env
import os
import sys

from fabric.api import cd, env, get, put
from fabric.operations import run
from boto3.session import Session

from aws_config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_KEY_PAIR_NAME
from aws_config import AWS_SSH_PRIVATE_KEY_FILE, GITHUB_SSH_PRIVATE_KEY_FILE, DOMAIN_NAME
from aws_config import mx1, mx5, mx5b, mx10, mx10b, cname_name, cname_value, txt
from aws_config import admin_email, admin_password

from pacioli.settings import PROD_PG_USERNAME, PROD_PG_PASSWORD
print(os.path.dirname(os.path.realpath(__file__)))

purpose = 'pacioli-deployment'

session = Session(aws_access_key_id=AWS_ACCESS_KEY_ID,
                  aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                  region_name=AWS_REGION)

ec2_client = session.client('ec2')
ec2_resource = session.resource('ec2')

rds_client = session.client('rds')

route53_client = session.client('route53')
if not os.path.isfile(AWS_SSH_PRIVATE_KEY_FILE):
    key = ec2_client.create_key_pair(KeyName=AWS_KEY_PAIR_NAME)
    with open(AWS_SSH_PRIVATE_KEY_FILE, 'w') as f:
        f.write(key['KeyMaterial'])
    os.chmod(AWS_SSH_PRIVATE_KEY_FILE, 400)

instances = ec2_resource.instances.all()

pacioli_instance = [instance for instance in instances if instance.state['Name'] != 'terminated'
                    and instance.tags[0]['Value'] == purpose]

if pacioli_instance and len(pacioli_instance) == 1:
    env.hosts = pacioli_instance[0].public_dns_name

pacioli_instance = pacioli_instance[0]

env.user = 'ec2-user'
env.key_filename = AWS_SSH_PRIVATE_KEY_FILE
env.port = 8910

subnets = ec2_client.describe_subnets()['Subnets']
availability_zones = {subnet['SubnetId']: subnet['AvailabilityZone'] for subnet in subnets}

instance_availability_zones = {instance.public_dns_name: availability_zones[instance.subnet_id] for instance in
                               instances
                               if instance.state['Name'] != 'terminated' and instance.tags[0]['Value'] == purpose}


def start_ec2():
    ami_id = 'ami-60b6c60a'
    instance_type = 't2.micro'
    instance = ec2_resource.create_instances(ImageId=ami_id,
                                             MinCount=1,
                                             MaxCount=1,
                                             KeyName=AWS_KEY_PAIR_NAME,
                                             InstanceType=instance_type,
                                             )[0]

    security_group = ec2_resource.SecurityGroup(instance.security_groups[0]['GroupId'])
    ssh_permission = [permission for permission in security_group.ip_permissions
                      if 'ToPort' in permission and permission['ToPort'] == 8910]
    if not ssh_permission:
        security_group.authorize_ingress(IpProtocol='tcp', FromPort=8910, ToPort=8910, CidrIp='0.0.0.0/0')
        security_group.authorize_ingress(IpProtocol='tcp', FromPort=80, ToPort=80, CidrIp='0.0.0.0/0')
    instance.create_tags(Tags=[{'Key': 'Purpose', 'Value': purpose}])


def start_rds():
    response = rds_client.create_db_instance(DBName='postgres',
                                             DBInstanceIdentifier='pacioli',
                                             AllocatedStorage=6,
                                             DBInstanceClass='db.t2.micro',
                                             Engine='postgres',
                                             MasterUsername=PROD_PG_USERNAME,
                                             MasterUserPassword=PROD_PG_PASSWORD,
                                             AvailabilityZone=instance_availability_zones[env.host_string],
                                             Port=58217,
                                             MultiAZ=False,
                                             EngineVersion='9.4.1',
                                             AutoMinorVersionUpgrade=True,
                                             PubliclyAccessible=True,
                                             Tags=[{'Key': 'Purpose', 'Value': purpose}])


def start_dns():
    vpc = ec2_client.describe_vpcs()['Vpcs'][0]
    hosted_zone = route53_client.create_hosted_zone(Name=DOMAIN_NAME,
                                                    CallerReference=purpose + '3')
    print(hosted_zone['DelegationSet']['NameServers'])


def allocate_ip():
    allocation_id = ec2_client.allocate_address()['AllocationId']
    response = ec2_client.associate_address(InstanceId=pacioli_instance.id,
                                            AllocationId=allocation_id,
                                            PrivateIpAddress=pacioli_instance.private_ip_address)
    print(response)


def create_dns_records():
    hosted_zone_id = route53_client.list_hosted_zones()['HostedZones'][0]['Id']
    public_ip_address = ec2_client.describe_addresses()['Addresses'][0]['PublicIp']
    response = route53_client.change_resource_record_sets(
        HostedZoneId=hosted_zone_id,
        ChangeBatch={
            'Changes': [{'Action': 'CREATE',
                         'ResourceRecordSet': {'Name': DOMAIN_NAME + '.',
                                               'Type': 'A',
                                               'TTL': 900,
                                               'ResourceRecords': [{'Value': public_ip_address}]}},
                        {'Action': 'CREATE',
                         'ResourceRecordSet': {'Name': 'www.' + DOMAIN_NAME + '.',
                                               'Type': 'CNAME',
                                               'TTL': 900,
                                               'ResourceRecords': [{'Value': public_ip_address}]}},
                        {'Action': 'CREATE',
                         'ResourceRecordSet': {'Name': DOMAIN_NAME + '.',
                                               'Type': 'MX',
                                               'TTL': 3600,
                                               'ResourceRecords': [{'Value': mx1}]}}
                        ]})
    print(response)


def install():
    # APP
    packages = 'gcc git python python-pip python-setuptools python-devel postgresql94-devel libxslt-devel libxml-devel'
    run('sudo yum -y install ' + packages)
    put(GITHUB_SSH_PRIVATE_KEY_FILE, GITHUB_SSH_PRIVATE_KEY_FILE, use_sudo=True)
    run('sudo chmod 400 {0}'.format(GITHUB_SSH_PRIVATE_KEY_FILE))
    run("ssh-agent bash -c 'ssh-add {0}; git clone git@github.com:PierreRochard/pacioli.git'".format(
        GITHUB_SSH_PRIVATE_KEY_FILE))
    run('sudo pip install -r /home/ec2-user/pacioli/instance-requirements.txt')

    run('git clone https://github.com/PierreRochard/ofxtools')
    with cd('/home/ec2-user/ofxtools/'):
        run('sudo python setup.py install')

    put('pacioli/settings.py', '/home/ec2-user/pacioli/pacioli/settings.py')
    run('mkdir ~/pacioli/logs/')

    run('git clone https://github.com/PierreRochard/flask-security')
    with cd('flask-security'):
        run('git checkout develop')
        run('sudo python setup.py install')

    run('git clone https://github.com/PierreRochard/flask-admin')
    with cd('flask-admin'):
        run('sudo python setup.py install')

    # SUPERVISORD
    run('sudo easy_install supervisor')
    put('configuration_files/supervisord.conf', '/etc/supervisord.conf', use_sudo=True)
    run('supervisord -c /etc/supervisord.conf')
    run('supervisorctl reread')
    run('supervisorctl update')
    run('supervisorctl restart pacioli')

    # GUNICORN
    run('sudo pip install gunicorn')
    put('configuration_files/gunicorn_configuration.py', '~/pacioli/gunicorn_configuration.py')

    # NGINX
    run('mkdir ~/pacioli/logs/nginx/')
    run('sudo yum -y install nginx')
    run('sudo mkdir /etc/nginx/sites-available')
    put('configuration_files/nginx.conf', '/etc/nginx/nginx.conf', use_sudo=True)
    put('configuration_files/pacioli-nginx', '/etc/nginx/sites-available/pacioli', use_sudo=True)
    run('sudo /etc/init.d/nginx start')


def install_cbtools():
    with cd('/home/ec2-user/cbtools/'):
        put('plugins/cb_config.py', '/home/ec2-user/cbtools/config.py')
    run('git clone https://github.com/PierreRochard/cbtools')
    run('chmod +x /home/ec2-user/cbtools/cbtools/main.py')


def update_cron():
    with open('remote_cron', 'w') as cron_file:
        cron_file.write('pacioli_ENV=prod\n')
        cron_file.write('30 11 * * * cd /home/ec2-user/pacioli/ && python manage.py update_ofx\n')
    put('remote_cron', 'remote_cron', use_sudo=True)
    run('sudo crontab remote_cron')
    run('sudo rm -f remote_cron')
    os.remove('remote_cron')


def mail():
    run('sudo cat /var/spool/mail/root | tail -n 30')


def install_certs():
    # cat pacio_li.crt pacio_li.ca-bundle > pacio_li_ssl_bundle.crt
    put('configuration_files/pacio_li_ssl_bundle.crt', '/etc/ssl/certs/pacio_li_ssl_bundle.crt', use_sudo=True)
    put('configuration_files/rochard_org_ssl_bundle.crt', '/etc/ssl/certs/rochard_org_ssl_bundle.crt', use_sudo=True)

    put('configuration_files/pacioli_private.key', '/etc/ssl/certs/pacioli_private.key', use_sudo=True)
    put('configuration_files/rochard_private.key', '/etc/ssl/certs/rochard_private.key', use_sudo=True)


def ssh():
    print('ssh -i {0} ec2-user@{1}'.format(AWS_SSH_PRIVATE_KEY_FILE, env.host_string))


def update():
    # APP
    with cd('/home/ec2-user/pacioli/'):
        run('git stash')
        run("ssh-agent bash -c 'ssh-add {0}; git pull'".format('/home/ec2-user/' + GITHUB_SSH_PRIVATE_KEY_FILE))
    put('pacioli/settings.py', '/home/ec2-user/pacioli/pacioli/settings.py')

    run('sudo pip install --upgrade -r /home/ec2-user/pacioli/instance-requirements.txt')

    with cd('ofxtools'):
        run('git pull')
        run('sudo python setup.py install')

    with cd('flask-security'):
        run('git checkout develop')
        run('git pull')
        run('sudo python setup.py install')

    with cd('flask-admin'):
        run('git pull')
        run('sudo python setup.py install')

    with cd('/home/ec2-user/pacioli/'):
        with shell_env(pacioli_ENV='prod'):
            run('python manage.py createdb')

    with cd('/home/ec2-user/pacioli/logs/'):
        run('sudo rm -f *.log')
    with cd('/home/ec2-user/pacioli/logs/nginx/'):
        run('sudo rm -f *.log')

    # GUNICORN
    put('configuration_files/gunicorn_configuration.py', '/home/ec2-user/pacioli/gunicorn_configuration.py')

    # SUPERVISORD
    put('configuration_files/supervisord.conf', '/etc/supervisord.conf', use_sudo=True)
    run('supervisorctl reread')
    run('supervisorctl update')
    run('supervisorctl restart pacioli')

    # NGINX
    put('configuration_files/nginx.conf', '/etc/nginx/nginx.conf', use_sudo=True)
    put('configuration_files/pacioli-nginx', '/etc/nginx/sites-available/pacioli', use_sudo=True)
    put('configuration_files/rochard-nginx', '/etc/nginx/sites-available/rochard', use_sudo=True)
    run('sudo /etc/init.d/nginx restart')


def create_db():
    with cd('/home/ec2-user/pacioli/'):
        with shell_env(pacioli_ENV='prod'):
            run('python manage.py createdb')


def populate_chart_of_accounts():
    with cd('/home/ec2-user/pacioli/'):
        with shell_env(pacioli_ENV='prod'):
            run('python manage.py populate_chart_of_accounts')


def create_admin():
    with cd('/home/ec2-user/pacioli/'):
        with shell_env(pacioli_ENV='prod'):
            run('python manage.py create_admin -e {0} -p {1}'.format(admin_email, admin_password))
            run('python manage.py create_superuser')


def cron():
    run('sudo tail /var/log/cron')


def python_error():
    run('cat ~/pacioli/logs/supervisord_stdout.log | tail')


def nginx_error():
    run('cat /home/ec2-user/pacioli/logs/nginx/*.log | tail')


def update_ofx():
    with cd('/home/ec2-user/pacioli/'):
        with shell_env(pacioli_ENV='prod'):
            run('python manage.py update_ofx')
