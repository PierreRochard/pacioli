#!/usr/bin/python

import argparse
from datetime import datetime, timedelta

from decimal import Decimal

from ofxtools import OFXClient
from ofxtools.Client import BankAcct, CcAcct
from ofxtools.ofxalchemy import DBSession, OFXParser, Base
from ofxtools.ofxalchemy.models import STMTTRN, ACCTFROM
from sqlalchemy import create_engine, func

from ofx_config import url, org, fid, bankid_checking, bankid_savings
from ofx_config import checking, user, password, clientuid, savings, creditcard
from ofx_config import PROD_PG_USERNAME, PROD_PG_PASSWORD, PROD_PG_HOST, PROD_PG_PORT

from utilities import send_email

SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD,
                                                                                 PROD_PG_HOST, PROD_PG_PORT)

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
        request = client.statement_request(user, password, clientuid, [account], dtstart=start, dtend=end)
        response = client.download(request)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()
    new_transactions = (DBSession.query(func.concat(STMTTRN.fitid, STMTTRN.acctfrom_id).label('id'),
                                        STMTTRN.dtposted.label('date'), STMTTRN.trnamt.label('amount'),
                                        func.concat(STMTTRN.name, ' ', STMTTRN.memo).label('description'))
                        .join(ACCTFROM, ACCTFROM.id == STMTTRN.acctfrom_id)
                        .filter(STMTTRN.dtposted > start)
                        .order_by(STMTTRN.fitid.desc()).all())
    html_body = '<table style="border:1px solid black;"><thead><tr>'
    for header in ['ID', 'Date', 'Debit', 'Credit', 'Description']:
        html_body += '<th style="border:1px solid black;">{0}</th>'.format(header)
    html_body += '</tr></thead><tbody>'
    for row in new_transactions:
        html_body += '<tr>'
        for cell in row:
            if isinstance(cell, Decimal):
                if cell > 0:
                    html_body += '<td style="border:1px solid black;">{0:,.2f}</td><td style="border:1px solid black;"></td>'.format(cell)
                else:
                    html_body += '<td style="border:1px solid black;"></td><td style="border:1px solid black;">{0:,.2f}</td>'.format(cell)
            elif isinstance(cell, datetime):
                cell = cell.date()
                html_body += '<td style="border:1px solid black;">{0}</td>'.format(cell)
            else:
                html_body += '<td style="border:1px solid black;">{0}</td>'.format(cell)
        html_body += '</tr>'
    html_body += '</tbody></table>'

    send_email(recipients=['pierre@rochard.org'], subject='New Transactions', html_body=html_body)

if __name__ == '__main__':
    ARGS = argparse.ArgumentParser()
    ARGS.add_argument('-u', action='store_true', dest='update', default=True)
    ARGS.add_argument('-s', action='store_true', dest='setup', default=False)
    ARGS.add_argument('-d', action='store_true', dest='drop_tables', default=False)
    args = ARGS.parse_args()
    if args.setup:
        setup(args.drop_tables)
    if args.update:
        update()
