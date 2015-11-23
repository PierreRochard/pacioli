#!/usr/bin/python

from datetime import datetime
import os
import sys

from ofxtools import OFXClient
from ofxtools.Client import BankAcct, CcAcct
from ofxtools.ofxalchemy import DBSession, OFXParser, Base
from ofxtools.ofxalchemy.models import STMTTRN

script_path = os.path.realpath(__file__)
pacioli_path = os.path.join(script_path, '../pacioli/')
sys.path.insert(1, pacioli_path)

from db_config import PROD_PG_USERNAME, PROD_PG_PASSWORD, PROD_PG_HOST, PROD_PG_PORT
from ofx_config import url, org, fid, bankid_checking, bankid_savings, checking, savings, creditcard, user, password


SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD,
                                                                                     PROD_PG_HOST, PROD_PG_PORT)
from sqlalchemy import create_engine
engine = create_engine(SQLALCHEMY_DATABASE_URI, echo=False)
DBSession.configure(autocommit=True, autoflush=True, bind=engine)
client = OFXClient(url, org, fid)

accounts = [BankAcct(bankid_checking, checking, 'checking'), BankAcct(bankid_savings, savings, 'savings'), CcAcct(creditcard)]


def setup():
    Base.metadata.create_all(engine)
    for account in accounts:
        request = client.statement_request(user, password, [account])
        response = client.download(request)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()


def update():
    start, = DBSession.query(STMTTRN.dtposted).order_by(STMTTRN.dtposted.desc()).first()
    end = datetime.today()
    for account in accounts:
        request = client.statement_request(user, password, [account], dtstart=start, dtend=end)
        response = client.download(request)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()


if __name__ == '__main__':
    # setup()
    update()
