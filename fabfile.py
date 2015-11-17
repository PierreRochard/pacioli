import os
from pprint import pformat
import time

from fabric.api import cd, env, get, put
from fabric.operations import run
from boto3.session import Session
from botocore.exceptions import ClientError

from aws_config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, REGION, KEY_PAIR_NAME


purpose = 'pacioli-deployment'

session = Session(aws_access_key_id=AWS_ACCESS_KEY_ID,
                  aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                  region_name=REGION)

client = session.client('ec2')
ec2 = session.resource('ec2')

private_key_file = os.path.abspath('{0}.pem'.format(KEY_PAIR_NAME))

if not os.path.isfile(private_key_file):
    key = client.create_key_pair(KeyName=KEY_PAIR_NAME)
    with open(private_key_file, 'w') as f:
        f.write(key['KeyMaterial'])
    os.chmod(private_key_file, 400)


instances = ec2.instances.all()

pacioli_instance = [instance for instance in instances if instance.state['Name'] != 'terminated'
                    and instance.tags[0]['Value'] == purpose]

if pacioli_instance and len(pacioli_instance) == 1:
    env.hosts = pacioli_instance[0].public_dns_name

env.user = 'ec2-user'
env.key_filename = private_key_file
env.port = 22


def start():
    ami_id = 'ami-60b6c60a'
    instance_type = 't2.micro'
    instance = ec2.create_instances(ImageId=ami_id,
                                    MinCount=1,
                                    MaxCount=1,
                                    KeyName=KEY_PAIR_NAME,
                                    InstanceType=instance_type,
                                    )[0]

    security_group = ec2.SecurityGroup(instance.security_groups[0]['GroupId'])
    ssh_permission = [permission for permission in security_group.ip_permissions
                      if 'ToPort' in permission and permission['ToPort'] == 22]
    if not ssh_permission:
        security_group.authorize_ingress(IpProtocol='tcp', FromPort=22, ToPort=22, CidrIp='0.0.0.0/0')
    instance.create_tags(Tags=[{'Key': 'Purpose', 'Value': purpose}])

