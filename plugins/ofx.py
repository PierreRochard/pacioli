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


def results_to_table(query_results):
    html_body = '<table style="border:1px solid black;"><thead><tr>'
    for header in ['ID', 'Date', 'Debit', 'Credit', 'Description']:
        html_body += '<th style="border:1px solid black;">{0}</th>'.format(header)
    html_body += '</tr></thead><tbody>'
    for row in query_results:
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
    return html_body


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
    html_body = results_to_table(new_transactions)

    send_email(recipients=['pierre@rochard.org'], subject='New Transactions', html_body=html_body)


def check_for_old():
    end, = DBSession.query(STMTTRN.dtposted).order_by(STMTTRN.dtposted.asc()).first()
    for account in accounts:
        request = client.statement_request(user, password, clientuid, [account], dtend=end)
        response = client.download(request)
        print(response.read())
        response.seek(0)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()


def import_ofx():
    import os
    directory = '/Users/Rochard/src/pacioli/configuration_files/data/'
    files = [ofx_file for ofx_file in os.listdir(directory) if ofx_file.endswith('.ofx') or ofx_file.endswith('.OFX')]
    for ofx_file_name in files:
        ofx_file_path = os.path.join(directory, ofx_file_name)
        parser = OFXParser()
        parser.parse(ofx_file_path)
        parser.instantiate()


def export():
    export_transactions = (DBSession.query(func.concat(STMTTRN.fitid, STMTTRN.acctfrom_id).label('id'),
                                        STMTTRN.dtposted.label('date'), STMTTRN.trnamt.label('amount'),
                                        func.concat(STMTTRN.name, ' ', STMTTRN.memo).label('description'))
                        .join(ACCTFROM, ACCTFROM.id == STMTTRN.acctfrom_id)
                        .order_by(STMTTRN.fitid.desc()).all())
    html_body = results_to_table(export_transactions)
    with open('export.html', 'w') as html_file:
        html_file.write(html_body)


if __name__ == '__main__':
    ARGS = argparse.ArgumentParser()
    ARGS.add_argument('-u', action='store_true', dest='update', default=False)
    ARGS.add_argument('-s', action='store_true', dest='setup', default=False)
    ARGS.add_argument('-d', action='store_true', dest='drop_tables', default=False)
    ARGS.add_argument('-e', action='store_true', dest='export', default=False)
    ARGS.add_argument('-o', action='store_true', dest='old', default=False)
    ARGS.add_argument('-i', action='store_true', dest='import_ofx', default=False)
    args = ARGS.parse_args()
    if args.setup:
        setup(args.drop_tables)
    if args.update:
        update()
    if args.export:
        export()
    if args.old:
        check_for_old()
    if args.import_ofx:
        import_ofx()