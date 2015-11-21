from fabric.context_managers import shell_env
import os
from pprint import pformat
import sys
import time

from fabric.api import cd, env, get, put
from fabric.operations import run
from boto3.session import Session



from aws_config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_KEY_PAIR_NAME
from aws_config import AWS_SSH_PRIVATE_KEY_FILE, GITHUB_SSH_PRIVATE_KEY_FILE
sys.path.insert(0, "pacioli/")
from db_config import PROD_PG_USERNAME, PROD_PG_PASSWORD

purpose = 'pacioli-deployment'

session = Session(aws_access_key_id=AWS_ACCESS_KEY_ID,
                  aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                  region_name=AWS_REGION)

ec2_client = session.client('ec2')
ec2_resource = session.resource('ec2')

rds_client = session.client('rds')

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

env.user = 'ec2-user'
env.key_filename = AWS_SSH_PRIVATE_KEY_FILE
env.port = 22

subnets = ec2_client.describe_subnets()['Subnets']
availability_zones = {subnet['SubnetId']: subnet['AvailabilityZone'] for subnet in subnets}

instance_availability_zones = {instance.public_dns_name: availability_zones[instance.subnet_id] for instance in instances
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
                      if 'ToPort' in permission and permission['ToPort'] == 22]
    if not ssh_permission:
        security_group.authorize_ingress(IpProtocol='tcp', FromPort=22, ToPort=22, CidrIp='0.0.0.0/0')
        security_group.authorize_ingress(IpProtocol='tcp', FromPort=80, ToPort=80, CidrIp='0.0.0.0/0')
    instance.create_tags(Tags=[{'Key': 'Purpose', 'Value': purpose}])


def start_rds():
    response = rds_client.create_db_instance(
        DBName='postgres',
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
        Tags=[
            {
                'Key': 'Purpose',
                'Value': purpose
            },
        ]
    )
    print(response)


def install():
    # APP
    run('sudo yum -y install gcc git python34 python34-pip python34-setuptools python34-devel postgresql94-devel')
    put(GITHUB_SSH_PRIVATE_KEY_FILE, GITHUB_SSH_PRIVATE_KEY_FILE, use_sudo=True)
    run('sudo chmod 400 {0}'.format(GITHUB_SSH_PRIVATE_KEY_FILE))
    run('rm -rf pacioli')
    run("ssh-agent bash -c 'ssh-add {0}; git clone git@github.com:PierreRochard/pacioli.git'".format(
        GITHUB_SSH_PRIVATE_KEY_FILE))
    run('sudo pip-3.4 install -r /home/ec2-user/pacioli/instance-requirements.txt')
    run('sudo pip-3.4 install psycopg2')
    put('pacioli/settings.py', '/home/ec2-user/pacioli/pacioli/settings.py')
    put('pacioli/db_config.py', '/home/ec2-user/pacioli/pacioli/db_config.py')
    run('mkdir ~/pacioli/logs/')

    run('git clone https://github.com/mattupstate/flask-security')
    with cd('flask-security'):
        run('git checkout develop')
        run('sudo python3 setup.py install')

    # SUPERVISORD
    run('sudo easy_install supervisor')
    put('configuration_files/supervisord.conf', '/etc/supervisord.conf', use_sudo=True)
    run('supervisord -c /etc/supervisord.conf')
    run('supervisorctl reread')
    run('supervisorctl update')
    run('supervisorctl restart pacioli')

    # GUNICORN
    run('sudo pip-3.4 install gunicorn')
    put('configuration_files/gunicorn_configuration.py', '~/pacioli/gunicorn_configuration.py')

    # NGINX
    run('mkdir ~/pacioli/logs/nginx/')
    run('sudo yum -y install nginx')
    run('sudo mkdir /etc/nginx/sites-available')
    put('configuration_files/nginx.conf', '/etc/nginx/nginx.conf', use_sudo=True)
    put('configuration_files/pacioli-nginx', '/etc/nginx/sites-available/pacioli', use_sudo=True)
    run('sudo /etc/init.d/nginx start')


def update():
    # APP
    with cd('/home/ec2-user/pacioli/'):
        run("ssh-agent bash -c 'ssh-add {0}; git pull'".format('/home/ec2-user/' + GITHUB_SSH_PRIVATE_KEY_FILE))
    put('pacioli/settings.py', '/home/ec2-user/pacioli/pacioli/settings.py')
    put('pacioli/db_config.py', '/home/ec2-user/pacioli/pacioli/db_config.py')

    with cd('/home/ec2-user/pacioli/logs/'):
        run('sudo rm -f *.log')
    with cd('/home/ec2-user/pacioli/logs/nginx/'):
        run('sudo rm -f *.log')

    with cd('flask-security'):
        run('git checkout develop')
        run('git pull')
        run('sudo python3 setup.py install')


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
    run('sudo /etc/init.d/nginx restart')


def create_db():
    with cd('/home/ec2-user/pacioli/'):
        with shell_env(pacioli_ENV='prod'):
            run('python3 manage.py createdb')


def install_certs():
    # cat pacio_li.crt pacio_li.ca-bundle > ssl_bundle.crt
    put('configuration_files/ssl_bundle.crt', '/etc/ssl/certs/ssl_bundle.crt', use_sudo=True)
    put('configuration_files/server.key', '/etc/ssl/certs/server.key', use_sudo=True)


def ssh():
    print('ssh -i {0} ec2-user@{1}'.format(AWS_SSH_PRIVATE_KEY_FILE, env.host_string))
