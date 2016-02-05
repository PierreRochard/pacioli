#!/usr/bin/python3

import argparse
import traceback
import urllib2
from datetime import datetime, date
from decimal import Decimal
from pprint import pformat

import shutil

import os
import sys

from ofxtools import OFXClient
from ofxtools.Client import BankAcct, CcAcct
from ofxtools.ofxalchemy import OFXParser
from ofxtools.ofxalchemy import DBSession as ofx_session
from ofxtools.ofxalchemy import Base as ofx_Base
from ofxtools.ofxalchemy.models import STMTTRN, ACCTFROM
import psycopg2
from sqlalchemy import create_engine, func

# sys.path.append("..")
from manage import make_shell_context
from pacioli.models import JournalEntries
from pacioli.controllers.main import Transactions, AccountsFrom

from ofx_config import url, org, fid, bankid_checking, bankid_savings
from ofx_config import checking, user, password, clientuid, savings, creditcard
from ofx_config import PROD_PG_USERNAME, PROD_PG_PASSWORD, PROD_PG_HOST, PROD_PG_PORT

from utilities import send_email
from xlrd import open_workbook

SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD,
                                                                                 PROD_PG_HOST, PROD_PG_PORT)

engine = create_engine(SQLALCHEMY_DATABASE_URI, echo=False)
ofx_session.configure(autocommit=True, autoflush=True, bind=engine)
ofx_client = OFXClient(url, org, fid)

accounts = [BankAcct(bankid_checking, checking, 'Checking'),
            BankAcct(bankid_savings, savings, 'Savings'),
            CcAcct(creditcard)]


def setup(drop_tables):
    if drop_tables:
        ofx_Base.metadata.drop_all(engine)
    ofx_Base.metadata.create_all(engine)
    for account in accounts:
        request = ofx_client.statement_request(user, password, clientuid, [account])
        response = ofx_client.download(request)
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
    start, = ofx_session.query(STMTTRN.dtposted).order_by(STMTTRN.dtposted.desc()).first()
    start = start.date()
    end = date.today()
    for account in accounts:
        request = ofx_client.statement_request(user, password, clientuid, [account], dtstart=start, dtend=end)
        try:
            response = ofx_client.download(request)
            parser = OFXParser()
            parser.parse(response)
            parser.instantiate()
        except urllib2.HTTPError:
            exc_info = sys.exc_info()
            tb = traceback.format_exception(*exc_info)
            send_email(recipients=['pierre@rochard.org'], subject='New Transactions', text_body=str(tb))
            sys.exit(0)

    # directory = '/Users/Rochard/src/pacioli/configuration_files/data/'
    # old_directory = os.path.join(directory, 'old')
    #
    # files = [ofx_file for ofx_file in os.listdir(directory) if ofx_file.endswith(('.ofx', '.OFX', '.qfx', '.QFX'))]
    # for ofx_file_name in files:
    #     ofx_file_path = os.path.join(directory, ofx_file_name)
    #     parser = OFXParser()
    #     parser.parse(ofx_file_path)
    #     parser.instantiate()
    #     shutil.move(ofx_file_path, os.path.join(old_directory, ofx_file_name))

    new_transactions = (ofx_session.query(func.concat(STMTTRN.fitid, STMTTRN.acctfrom_id).label('id'),
                                        STMTTRN.dtposted.label('date'), STMTTRN.trnamt.label('amount'),
                                        func.concat(STMTTRN.name, ' ', STMTTRN.memo).label('description'))
                        .join(ACCTFROM, ACCTFROM.id == STMTTRN.acctfrom_id)
                        .filter(STMTTRN.dtposted > start)
                        .order_by(STMTTRN.fitid.desc()).all())
    html_body = results_to_table(new_transactions)

    categorize()

    send_email(recipients=['pierre@rochard.org'], subject='New Transactions', html_body=html_body)


def check_for_old():
    end, = ofx_session.query(STMTTRN.dtposted).order_by(STMTTRN.dtposted.asc()).first()
    for account in accounts:
        request = ofx_client.statement_request(user, password, clientuid, [account], dtend=end)
        response = ofx_client.download(request)
        print(response.read())
        response.seek(0)
        parser = OFXParser()
        parser.parse(response)
        parser.instantiate()


def import_ofx():
    directory = '/Users/Rochard/src/pacioli/configuration_files/data/'
    files = [ofx_file for ofx_file in os.listdir(directory) if ofx_file.endswith(('.ofx', '.OFX', '.qfx', '.QFX'))]
    for ofx_file_name in files:
        ofx_file_path = os.path.join(directory, ofx_file_name)
        parser = OFXParser()
        parser.parse(ofx_file_path)
        parser.instantiate()


def export():
    export_transactions = (ofx_session.query(func.concat(STMTTRN.fitid, STMTTRN.acctfrom_id).label('id'),
                                        STMTTRN.dtposted.label('date'), STMTTRN.trnamt.label('amount'),
                                        func.concat(STMTTRN.name, ' ', STMTTRN.memo).label('description'))
                        .join(ACCTFROM, ACCTFROM.id == STMTTRN.acctfrom_id)
                        .order_by(STMTTRN.fitid.desc()).all())
    html_body = results_to_table(export_transactions)
    with open('export.html', 'w') as html_file:
        html_file.write(html_body)


def categorize():
    book = open_workbook('ofx_mappings.xlsx')
    sheet = book.sheet_by_index(0)

    keys = [sheet.cell(0, col_index).value for col_index in xrange(sheet.ncols)]

    mappings = []
    for row_index in xrange(1, sheet.nrows):
        d = {keys[col_index]: sheet.cell(row_index, col_index).value
             for col_index in xrange(sheet.ncols)}
        mappings.append(d)
    context = make_shell_context()
    db = context['db']
    app = context['app']
    with app.app_context():
        for mapping in mappings:
            new_transactions = (db.session.query(db.func.concat(Transactions.fitid, Transactions.acctfrom_id).label('id'),
                                                 Transactions.dtposted.label('date'), Transactions.trnamt.label('amount'),
                                                 db.func.concat(Transactions.name, ' ', Transactions.memo).label('description'),
                                                 AccountsFrom.name.label('account'))
                                .outerjoin(JournalEntries, JournalEntries.transaction_id ==
                                           db.func.concat(Transactions.fitid, Transactions.acctfrom_id))
                                .join(AccountsFrom, AccountsFrom.id == Transactions.acctfrom_id)
                                .filter(JournalEntries.transaction_id.is_(None))
                                .filter(func.lower(Transactions.name).like('%' + mapping['pattern'].lower() + '%'))
                                .filter(AccountsFrom.name == mapping['account'])
                                .order_by(Transactions.fitid.desc()).all())
            for transaction in new_transactions:
                new_journal_entry = JournalEntries()
                new_journal_entry.transaction_id = transaction.id
                new_journal_entry.transaction_source = 'ofx'
                new_journal_entry.timestamp = transaction.date
                if transaction.amount > 0:
                    new_journal_entry.debit_subaccount = mapping['positive_debit_account']
                    new_journal_entry.credit_subaccount = mapping['positive_credit_account']
                elif transaction.amount < 0:
                    new_journal_entry.debit_subaccount = mapping['negative_debit_account']
                    new_journal_entry.credit_subaccount = mapping['negative_credit_account']
                else:
                    raise Exception()
                new_journal_entry.functional_amount = transaction.amount
                new_journal_entry.functional_currency = 'USD'
                new_journal_entry.source_amount = transaction.amount
                new_journal_entry.source_currency = 'USD'
                db.session.add(new_journal_entry)
                db.session.commit()
                print(transaction.description)
                print(transaction.account)


def create_view():
    connection = psycopg2.connect(database='pacioli', user=PROD_PG_USERNAME, password=PROD_PG_PASSWORD,
                                  host=PROD_PG_HOST, port=PROD_PG_PORT)
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute("""
    DROP MATERIALIZED VIEW ofx.new_transactions;
    CREATE VIEW ofx.new_transactions AS SELECT concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id) AS id,
            ofx.stmttrn.dtposted AS date,
            ofx.stmttrn.trnamt AS amount,
            concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
            ofx.acctfrom.name AS account
        FROM ofx.stmttrn
        LEFT OUTER JOIN pacioli.journal_entries ON pacioli.journal_entries.transaction_id = concat(ofx.stmttrn.fitid,
                ofx.stmttrn.acctfrom_id)
        JOIN ofx.acctfrom ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
        WHERE pacioli.journal_entries.transaction_id IS NULL ORDER BY ofx.stmttrn.dtposted DESC;
    """)


if __name__ == '__main__':
    ARGS = argparse.ArgumentParser()
    ARGS.add_argument('-u', action='store_true', dest='update', default=False)
    ARGS.add_argument('-s', action='store_true', dest='setup', default=False)
    ARGS.add_argument('-d', action='store_true', dest='drop_tables', default=False)
    ARGS.add_argument('-e', action='store_true', dest='export', default=False)
    ARGS.add_argument('-o', action='store_true', dest='old', default=False)
    ARGS.add_argument('-i', action='store_true', dest='import_ofx', default=False)
    ARGS.add_argument('-c', action='store_true', dest='categorize', default=False)
    ARGS.add_argument('-v', action='store_true', dest='create_view', default=False)
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
    if args.categorize:
        categorize()
    if args.create_view:
        create_view()