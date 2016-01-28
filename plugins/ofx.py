#!/usr/bin/python2

import argparse
from datetime import datetime

from ofxtools import OFXClient
from ofxtools.Client import BankAcct, CcAcct
from ofxtools.ofxalchemy import DBSession, OFXParser, Base
from ofxtools.ofxalchemy.models import STMTTRN

from ofx_config import url, org, fid, bankid_checking, bankid_savings
from ofx_config import checking, user, password, clientuid, savings, creditcard
from ofx_config import PROD_PG_USERNAME, PROD_PG_PASSWORD, PROD_PG_HOST, PROD_PG_PORT


SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD,
                                                                                     PROD_PG_HOST, PROD_PG_PORT)
from sqlalchemy import create_engine
engine = create_engine(SQLALCHEMY_DATABASE_URI, echo=False)
DBSession.configure(autocommit=True, autoflush=True, bind=engine)
client = OFXClient(url, org, fid)

accounts = [BankAcct(bankid_checking, checking, 'checking'),
            BankAcct(bankid_savings, savings, 'savings'),
            CcAcct(creditcard)]


def setup(drop_tables):
    if drop_tables:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    for account in accounts:
        print(account._acct['ACCTTYPE'])
        request = client.statement_request(user, password, clientuid, [account])
        response = client.download(request)
        parser = OFXParser()
        response.seek(0)
        parser.parse(response)
        parser.instantiate()


def update():
    start, = DBSession.query(STMTTRN.dtposted).order_by(STMTTRN.dtposted.desc()).first()
    end = datetime.today()
    for account in accounts:
        print(account._acct['ACCTTYPE'])
        request = client.statement_request(user, password, clientuid, [account], dtstart=start, dtend=end)
        response = client.download(request)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()


if __name__ == '__main__':
    ARGS = argparse.ArgumentParser()
    ARGS.add_argument('-s', action='store_true', dest='setup', default=False)
    ARGS.add_argument('-u', action='store_true', dest='update', default=True)
    ARGS.add_argument('-d', action='store_true', dest='drop_tables', default=False)
    args = ARGS.parse_args()
    if args.setup:
        setup(args.drop_tables)
    if args.update:
        update()
