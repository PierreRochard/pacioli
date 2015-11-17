import os
from pprint import pformat
import time

from fabric.api import cd, env, get, put
from fabric.operations import run
from boto3.session import Session

from aws_config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_KEY_PAIR_NAME
from aws_config import AWS_SSH_PRIVATE_KEY_FILE, GITHUB_SSH_PRIVATE_KEY_FILE


purpose = 'pacioli-deployment'

session = Session(aws_access_key_id=AWS_ACCESS_KEY_ID,
                  aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                  region_name=AWS_REGION)

client = session.client('ec2')
ec2 = session.resource('ec2')


if not os.path.isfile(AWS_SSH_PRIVATE_KEY_FILE):
    key = client.create_key_pair(KeyName=AWS_KEY_PAIR_NAME)
    with open(AWS_SSH_PRIVATE_KEY_FILE, 'w') as f:
        f.write(key['KeyMaterial'])
    os.chmod(AWS_SSH_PRIVATE_KEY_FILE, 400)


instances = ec2.instances.all()

pacioli_instance = [instance for instance in instances if instance.state['Name'] != 'terminated'
                    and instance.tags[0]['Value'] == purpose]

if pacioli_instance and len(pacioli_instance) == 1:
    env.hosts = pacioli_instance[0].public_dns_name

env.user = 'ec2-user'
env.key_filename = AWS_SSH_PRIVATE_KEY_FILE
env.port = 22


def start():
    ami_id = 'ami-60b6c60a'
    instance_type = 't2.micro'
    instance = ec2.create_instances(ImageId=ami_id,
                                    MinCount=1,
                                    MaxCount=1,
                                    KeyName=AWS_KEY_PAIR_NAME,
                                    InstanceType=instance_type,
                                    )[0]

    security_group = ec2.SecurityGroup(instance.security_groups[0]['GroupId'])
    ssh_permission = [permission for permission in security_group.ip_permissions
                      if 'ToPort' in permission and permission['ToPort'] == 22]
    if not ssh_permission:
        security_group.authorize_ingress(IpProtocol='tcp', FromPort=22, ToPort=22, CidrIp='0.0.0.0/0')
        security_group.authorize_ingress(IpProtocol='tcp', FromPort=80, ToPort=80, CidrIp='0.0.0.0/0')
    instance.create_tags(Tags=[{'Key': 'Purpose', 'Value': purpose}])


def install():
    # APP
    run('sudo yum -y install git python34 python34-pip')
    put(GITHUB_SSH_PRIVATE_KEY_FILE, GITHUB_SSH_PRIVATE_KEY_FILE, use_sudo=True)
    run('sudo chmod 400 {0}'.format(GITHUB_SSH_PRIVATE_KEY_FILE))
    run('rm -rf pacioli')
    run("ssh-agent bash -c 'ssh-add {0}; git clone git@github.com:PierreRochard/pacioli.git'".format(
        GITHUB_SSH_PRIVATE_KEY_FILE))
    run('sudo pip-3.4 install -r ~/pacioli/instance-requirements.txt')
    put('pacioli/settings.py', '~/pacioli/settings.py')
    run('mkdir ~/pacioli/logs/')

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
        run("ssh-agent bash -c 'ssh-add {0}; git pull'".format('/home/ec2-user/'+GITHUB_SSH_PRIVATE_KEY_FILE))

    run('sudo rm -f /home/ec2-user/pacioli/logs/supervisord_stdout.log')
    run('sudo rm -f /home/ec2-user/pacioli/logs/gunicorn_error.log')

    # SUPERVISORD
    put('configuration_files/supervisord.conf', '/etc/supervisord.conf', use_sudo=True)
    run('supervisorctl reread')
    run('supervisorctl update')
    run('supervisorctl restart pacioli')

    # GUNICORN
    put('configuration_files/gunicorn_configuration.py', '~/pacioli/gunicorn_configuration.py')


    # NGINX
    put('configuration_files/nginx.conf', '/etc/nginx/nginx.conf', use_sudo=True)
    put('configuration_files/pacioli-nginx', '/etc/nginx/sites-available/pacioli', use_sudo=True)
    run('sudo /etc/init.d/nginx restart')


def ssh():
    print('ssh -i {0} ec2-user@{1}'.format(AWS_SSH_PRIVATE_KEY_FILE, env.host_string))
