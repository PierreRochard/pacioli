from __future__ import print_function

from datetime import datetime, date

from dateutil.tz import tzlocal
from flask import current_app
from ofxtools import OFXClient
from ofxtools.Client import CcAcct, BankAcct
from ofxtools.ofxalchemy import OFXParser, DBSession
from sqlalchemy import func, create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm.exc import NoResultFound

from pacioli import db
from pacioli.models import (Subaccounts, Mappings, JournalEntries, Connections,
                            ConnectionResponses, Transactions, AccountsFrom, CreditCardAccounts, BankAccounts)


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


def create_ofx_views():
    db.engine.execute("""
    CREATE OR REPLACE VIEW ofx.transactions
      AS SELECT
        concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id) AS id,
        ofx.stmttrn.dtposted AS date,
        ofx.stmttrn.trnamt AS amount,
        concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
        ofx.stmttrn.trntype AS type,
        ofx.acctfrom.name AS account,
        ofx.stmttrn.acctfrom_id AS account_id,
        bookkeeping.journal_entries.id AS journal_entry_id,
        bookkeeping.journal_entries.debit_subaccount AS debit_subaccount,
        bookkeeping.journal_entries.credit_subaccount AS credit_subaccount
      FROM ofx.stmttrn
      LEFT OUTER JOIN bookkeeping.journal_entries
        ON bookkeeping.journal_entries.transaction_id
              = concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id)
          AND bookkeeping.journal_entries.transaction_source = 'ofx'
      JOIN ofx.acctfrom
        ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
      ORDER BY ofx.stmttrn.dtposted DESC;
    """)

    db.engine.execute("""
    CREATE OR REPLACE VIEW ofx.investment_transactions AS SELECT
            ofx.invtran.*,
            ofx.acctfrom.name AS account_name,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf_secinfo.secname
                WHEN 'sellmf' THEN sellmf_secinfo.secname
                WHEN 'reinvest' THEN reinvest_secinfo.secname
            END AS secname,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf_secinfo.ticker
                WHEN 'sellmf' THEN sellmf_secinfo.ticker
                WHEN 'reinvest' THEN reinvest_secinfo.ticker
            END AS ticker,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf.units
                WHEN 'sellmf' THEN sellmf.units
                WHEN 'reinvest' THEN reinvest.units
            END AS units,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf.unitprice
                WHEN 'sellmf' THEN sellmf.unitprice
                WHEN 'reinvest' THEN reinvest.unitprice
            END AS unitprice,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf.total*-1
                WHEN 'sellmf' THEN sellmf.total*-1
                WHEN 'reinvest' THEN reinvest.total*-1
            END AS total
        FROM ofx.invtran
        LEFT OUTER JOIN ofx.buymf ON ofx.buymf.id = ofx.invtran.id
                    and ofx.invtran.subclass = 'buymf'
        LEFT OUTER JOIN ofx.sellmf ON ofx.sellmf.id = ofx.invtran.id
                    and ofx.invtran.subclass = 'sellmf'
        LEFT OUTER JOIN ofx.reinvest ON ofx.reinvest.id = ofx.invtran.id
                    and ofx.invtran.subclass = 'reinvest'
        LEFT OUTER JOIN ofx.secinfo buymf_secinfo
          ON buymf_secinfo.id = ofx.buymf.secinfo_id
        LEFT OUTER JOIN ofx.secinfo sellmf_secinfo
          ON sellmf_secinfo.id = ofx.sellmf.secinfo_id
        LEFT OUTER JOIN ofx.secinfo reinvest_secinfo
          ON reinvest_secinfo.id = ofx.reinvest.secinfo_id
        JOIN ofx.acctfrom ON acctfrom.id = ofx.invtran.acctfrom_id
        ORDER BY ofx.invtran.dttrade DESC;
    """)

    db.engine.execute("""
        CREATE OR REPLACE VIEW ofx.cost_bases AS SELECT
            investment_transactions.secname,
            sum(investment_transactions.units) AS total_units,
            sum(investment_transactions.total) AS cost_basis,
            q1.ticker,
            q1.adjusted_close AS "close",
            q1.adjusted_close
                  * sum(investment_transactions.units) AS market_value,
            (q1.adjusted_close
                  * sum(investment_transactions.units)
                  - sum(investment_transactions.total)) AS pnl,
            (q1.adjusted_close
                  * sum(investment_transactions.units)
                  - sum(investment_transactions.total))
              / sum(investment_transactions.total) AS pnl_percent,
            q2.date AS price_date

        FROM ofx.investment_transactions
        JOIN (SELECT ticker, max(date) AS date
                FROM investments.security_prices
                GROUP BY ticker) AS q2
          ON q2.ticker = investment_transactions.ticker
        JOIN investments.security_prices q1
          ON q1.ticker = ofx.investment_transactions.ticker
            AND q2.date = q1.date
        GROUP BY investment_transactions.secname,
                 q1.ticker,
                 q2.date,
                 q1.adjusted_close
        ORDER BY sum(investment_transactions.total);
    """)


def sync_ofx():
    for connection in db.session.query(Connections).filter(Connections.source == 'ofx').all():
        if connection.type in ['Checking', 'Savings']:
            try:
                start, = (db.session.query(Transactions.date)
                          .join(AccountsFrom, Transactions.account_id == AccountsFrom.id)
                          .filter(BankAccounts.acctid == connection.account_number)
                          .order_by(Transactions.date.desc()).first())
                start = start.date()
                end = date.today()
            except TypeError:
                start = None
                end = None
            account = BankAcct(connection.routing_number, connection.account_number, connection.type)
        elif connection.type == 'Credit Card':
            try:
                start, = (db.session.query(Transactions.date)
                          .join(AccountsFrom, Transactions.account_id == AccountsFrom.id)
                          .join(CreditCardAccounts, CreditCardAccounts.id == AccountsFrom.id)
                          .filter(CreditCardAccounts.acctid == connection.account_number)
                          .order_by(Transactions.date.desc()).first())
                start = start.date()
                end = date.today()
            except TypeError:
                start = None
                end = None
            account = CcAcct(connection.account_number)
        else:
            raise Exception('Unrecognized account/connection type: {0}'.format(connection.type))
        ofx_client = OFXClient(connection.url, connection.org, connection.fid)
        if start and end:
            statement_request = ofx_client.statement_request(connection.user, connection.password, connection.clientuid,
                                                             [account], dtstart=start, dtend=end)
        else:
            statement_request = ofx_client.statement_request(connection.user, connection.password,
                                                             connection.clientuid, [account])
        response = ofx_client.download(statement_request)

        new_response = ConnectionResponses()
        new_response.connection_id = connection.id
        new_response.connected_at = datetime.now(tzlocal())
        new_response.response = response.read()
        db.session.add(new_response)
        db.session.commit()

        response.seek(0)
        parser = OFXParser()
        engine = create_engine(current_app.config['SQLALCHEMY_DATABASE_URI'], echo=False)
        DBSession.configure(bind=engine)
        parser.parse(response)
        parser.instantiate()
        DBSession.commit()
        connection.synced_at = datetime.now(tzlocal())
        db.session.commit()

    for account in db.session.query(AccountsFrom).filter(AccountsFrom.name.is_(None)).all():
        account.name = ''
        db.session.commit()


def apply_all_mappings():
    for mapping_id, in db.session.query(Mappings.id).filter(Mappings.source == 'ofx').all():
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
