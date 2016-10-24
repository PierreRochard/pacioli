from __future__ import print_function

from datetime import datetime, date

from dateutil.tz import tzlocal
from flask import current_app
from ofxtools import OFXClient
from ofxtools.Client import CcAcct, BankAcct
from ofxtools.ofxalchemy import OFXParser, DBSession
from sqlalchemy import func, create_engine
from sqlalchemy.orm.exc import NoResultFound

from pacioli import db
from pacioli.models import (Subaccounts, Mappings, JournalEntries,
                            Connections, ConnectionResponses, Transactions,
                            AccountsFrom, CreditCardAccounts, BankAccounts)


def fix_ofx_file(ofx_file_path):
    old_file = open(ofx_file_path, 'r')
    if 'verisightprod' not in old_file.read():
        return ofx_file_path
    old_file.seek(0)
    new_file = open(ofx_file_path + ' fixed.ofx', 'w')
    for line in old_file.readlines():
        if line.startswith('</SECID>'):
            continue
        elif line.startswith('<TICKER>'):
            new_file.write(line)
            new_file.write('</SECID>\n')
        elif line.startswith('<HELDINACCT>'):
            new_file.write('</SECID>\n')
            new_file.write(line)
        else:
            new_file.write(line)
    old_file.close()
    new_file.close()
    return ofx_file_path + ' fixed.ofx'


def sync_ofx():
    for connection in (db.session.query(Connections)
                       .filter(Connections.source == 'ofx')
                       .all()):
        sync_ofx_connection(connection)

    for account in (db.session.query(AccountsFrom)
                    .filter(AccountsFrom.name.is_(None))
                    .all()):
        account.name = ''
        db.session.commit()


def sync_ofx_connection(connection):
    if connection.type in ['Checking', 'Savings']:
        filter_boolean = BankAccounts.acctid == connection.account_number
        account = BankAcct(connection.routing_number,
                           connection.account_number,
                           connection.type)
    elif connection.type == 'Credit Card':
        filter_boolean = CreditCardAccounts.acctid == connection.account_number
        account = CcAcct(connection.account_number)
    else:
        raise Exception('Unrecognized account/'
                        'connection type: {0}'.format(connection.type))

    start, = (db.session.query(Transactions.date)
              .filter(filter_boolean)
              .order_by(Transactions.date.desc())
              .first())
    if start:
        start = start.date()
        end = date.today()
    else:
        start = None
        end = None


    # start = start.join(AccountsFrom,
    #                    Transactions.account_id == AccountsFrom.id)

    # elif connection.type == 'Credit Card':
    #     start = db.session.query(Transactions.date)
    #     start = start.join(AccountsFrom, Transactions.account_id == AccountsFrom.id)
    #     start = start.filter(CreditCardAccounts.acctid == connection.account_number)
    #     start = start.order_by(Transactions.date.desc())
    #     start, = start.first()
    #     if start:
    #         start = start.date()
    #         end = date.today()
    #     else:
    #         start = None
    #         end = None

    ofx_client = OFXClient(connection.url, connection.org, connection.fid)

    if start and end:
        statement_request = ofx_client.statement_request(connection.user,
                                                         connection.password,
                                                         connection.clientuid,
                                                         [account],
                                                         dtstart=start,
                                                         dtend=end)
    else:
        statement_request = ofx_client.statement_request(connection.user,
                                                         connection.password,
                                                         connection.clientuid,
                                                         [account])
    response = ofx_client.download(statement_request)

    new_response = ConnectionResponses()
    new_response.connection_id = connection.id
    new_response.connected_at = datetime.now(tzlocal())
    new_response.response = response.read()
    db.session.add(new_response)
    db.session.commit()

    response.seek(0)
    parser = OFXParser()
    engine = create_engine(current_app.config['SQLALCHEMY_DATABASE_URI'],
                           echo=False)
    DBSession.configure(bind=engine)
    parser.parse(response)
    parser.instantiate()
    DBSession.commit()
    connection.synced_at = datetime.now(tzlocal())
    db.session.commit()


def apply_all_mappings():
    for mapping_id, in (db.session.query(Mappings.id)
                        .filter(Mappings.source == 'ofx')
                        .all()):
        apply_single_ofx_mapping(mapping_id)


def apply_single_ofx_mapping(mapping_id):
    mapping = db.session.query(Mappings).filter(Mappings.id == mapping_id).one()
    matched_transactions = (db.session.query(Transactions)
                            .outerjoin(JournalEntries, JournalEntries.transaction_id == Transactions.id)
                            .filter(JournalEntries.transaction_id.is_(None))
                            .filter(func.lower(Transactions.description).like('%' + '%'.join(mapping.keyword.lower().split()) + '%'))
                            .order_by(Transactions.date.desc()).all())
    for transaction in matched_transactions:
        new_journal_entry = JournalEntries()
        new_journal_entry.transaction_id = transaction.id
        new_journal_entry.transaction_source = 'ofx'
        new_journal_entry.timestamp = transaction.date
        if transaction.amount > 0:
            new_journal_entry.debit_subaccount = transaction.account
            try:
                db.session.query(Subaccounts).filter(Subaccounts.name == mapping.positive_credit_subaccount_id).one()
            except NoResultFound:
                new_subaccount = Subaccounts()
                new_subaccount.name = mapping.positive_credit_subaccount_id
                new_subaccount.parent = 'Discretionary Costs'
                db.session.add(new_subaccount)
                db.session.commit()
            new_journal_entry.credit_subaccount = mapping.positive_credit_subaccount_id
        elif transaction.amount < 0:
            new_journal_entry.credit_subaccount = transaction.account
            try:
                db.session.query(Subaccounts).filter(Subaccounts.name == mapping.negative_debit_subaccount_id).one()
            except NoResultFound:
                new_subaccount = Subaccounts()
                new_subaccount.name = mapping.negative_debit_subaccount_id
                new_subaccount.parent = 'Discretionary Costs'
                db.session.add(new_subaccount)
                db.session.commit()
            new_journal_entry.debit_subaccount = mapping.negative_debit_subaccount_id

        else:
            raise Exception()
        new_journal_entry.functional_amount = abs(transaction.amount)
        new_journal_entry.functional_currency = 'USD'
        new_journal_entry.source_amount = abs(transaction.amount)
        new_journal_entry.source_currency = 'USD'
        db.session.add(new_journal_entry)
        db.session.commit()
