from datetime import datetime
import sys

from ofxtools import OFXClient
from ofxtools.Client import BankAcct, CcAcct
from ofxtools.ofxalchemy import DBSession, OFXParser, Base
from sqlalchemy import create_engine

sys.path.insert(0, "../pacioli/")

from db_config import PROD_PG_USERNAME, PROD_PG_PASSWORD, PROD_PG_HOST, PROD_PG_PORT
from ofx_config import url, org, fid, bankid, checking, savings, creditcard, user, password


SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD,
                                                                                     PROD_PG_HOST, PROD_PG_PORT)
from sqlalchemy import create_engine
engine = create_engine(SQLALCHEMY_DATABASE_URI, echo=False)
DBSession.configure(autocommit=True, autoflush=True, bind=engine)
client = OFXClient(url, org, fid)

accounts = [BankAcct(bankid, checking, 'checking'), BankAcct(bankid, savings, 'savings'), CcAcct(creditcard)]


def setup():
    Base.metadata.create_all(engine)
    for account in accounts:
        request = client.statement_request(user, password, [account])
        response = client.download(request)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()


def update():

    for account in accounts:
        request = client.statement_request(user, password, [account])
        response = client.download(request)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()



if __name__ == '__main__':
    setup()
