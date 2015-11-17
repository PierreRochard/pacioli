import os
from pprint import pformat
import time

from fabric.api import cd, env, get, put
from fabric.operations import run
from boto3.session import Session
from botocore.exceptions import ClientError

from aws_config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, REGION, KEY_PAIR_NAME



