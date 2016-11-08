from datetime import datetime, timedelta
from pprint import pformat
import unittest
import uuid

from dateutil.tz import tzlocal
import psycopg2
from decimal import Decimal

from psycopg2._psycopg import ProgrammingError
from sqlalchemy.engine.url import URL

from manage import createdb, populate_chart_of_accounts
from pacioli import create_app
from pacioli.extensions import db
from pacioli.models import (register_views, JournalEntries,
                            TrialBalances, Subaccounts,
                            remove_views_from_metadata)
from pacioli.settings import Config

test_user_password = str(uuid.uuid4()).replace('-', '')


class TestConfig(Config):
    pg_uri = URL(drivername='postgresql+psycopg2',
                 username='test_user',
                 password=test_user_password,
                 host='localhost',
                 port=5432,
                 database='pacioli_test')
    SQLALCHEMY_DATABASE_URI = pg_uri
    # SQLALCHEMY_ECHO = True
    TESTING = True


class TestCase(unittest.TestCase):
    def setUp(self):
        # self.tearDown()
        connection = psycopg2.connect("dbname=postgres")
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("""
            DROP DATABASE IF EXISTS pacioli_test;
            """)

        cursor.execute("""
            DROP ROLE IF EXISTS test_user;
            CREATE ROLE test_user WITH
                NOSUPERUSER
                CREATEDB
                NOCREATEROLE
                NOINHERIT
                LOGIN
                NOREPLICATION
                NOBYPASSRLS
                ENCRYPTED
                PASSWORD %s;
            """, (test_user_password,))
        cursor.close()
        connection.close()

        connection = psycopg2.connect(database='postgres', user='test_user',
                                      password=test_user_password)
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("""
            CREATE DATABASE pacioli_test
              ENCODING 'utf-8'
              TEMPLATE template0;
        """)
        cursor.close()
        connection.close()

        app = create_app(TestConfig)
        app.app_context().push()
        self.app = app.test_client()
        createdb()
        register_views()
        populate_chart_of_accounts()

        import pacioli.views.admin_views
        import pacioli.views.bookkeeping_views
        import pacioli.views.accounting_views
        import pacioli.views.ofx_views
        import pacioli.views.amazon_views
        import pacioli.views.payroll_views

    def tearDown(self):
        remove_views_from_metadata()
        db.session.remove()
        connection = psycopg2.connect("dbname=postgres")
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("""
            SELECT
              pg_terminate_backend(pid)
            FROM
              pg_stat_activity
            WHERE
              pid <> pg_backend_pid()
            AND datname = 'pacioli_test'
            ;
        """)
        try:
            cursor.execute("""
                DROP DATABASE pacioli_test;
                """)
            cursor.execute("""
                DROP ROLE test_user;
                """.format(test_user_password))
        except ProgrammingError:
            pass
        cursor.close()
        connection.close()

    def test_expense_accrual(self):

        today = datetime.now(tzlocal())
        a_month_ago = today - timedelta(days=40)
        expense_account = 'Rent'
        payables_account = 'Accounts Payable'
        cash_account = 'Chase Checking'
        amount = Decimal('100')
        currency = 'USD'

        expense_accrual = JournalEntries()
        expense_accrual.timestamp = a_month_ago
        expense_accrual.debit_subaccount = expense_account
        expense_accrual.credit_subaccount = payables_account
        expense_accrual.functional_amount = amount
        expense_accrual.functional_currency = currency
        expense_accrual.source_amount = amount
        expense_accrual.source_currency = currency
        db.session.add(expense_accrual)
        db.session.commit()

        expense_payment = JournalEntries()
        expense_payment.timestamp = today
        expense_payment.debit_subaccount = payables_account
        expense_payment.credit_subaccount = cash_account
        expense_payment.functional_amount = amount
        expense_payment.functional_currency = currency
        expense_payment.source_amount = amount
        expense_payment.source_currency = currency
        db.session.add(expense_payment)
        db.session.commit()

        prior_month_balance = (
            db.session.query(TrialBalances)
                .filter(TrialBalances.period_interval == 'YYYY-MM')
                .filter(TrialBalances.subaccount == payables_account)
                .order_by(TrialBalances.period.desc()).offset(1).limit(
                1).first()
        )
        print(pformat(prior_month_balance.__dict__))
        assert prior_month_balance.net_balance == Decimal('-100')

        current_month_balance = (
            db.session.query(TrialBalances)
            .filter(TrialBalances.period_interval == 'YYYY-MM')
            .filter(TrialBalances.subaccount == payables_account)
            .order_by(TrialBalances.period.desc()).limit(1).first()
        )
        print(pformat(current_month_balance.__dict__))
        assert current_month_balance.net_balance == Decimal('0')

    def test_income_accrual(self):
        today = datetime.now(tzlocal())
        a_month_ago = today - timedelta(days=40)
        income_account = 'Salary'
        receivables_account = 'Accounts Receivable'
        cash_account = 'Chase Checking'
        amount = Decimal('100')
        currency = 'USD'

        expense_accrual = JournalEntries()
        expense_accrual.timestamp = a_month_ago
        expense_accrual.debit_subaccount = receivables_account
        expense_accrual.credit_subaccount = income_account
        expense_accrual.functional_amount = amount
        expense_accrual.functional_currency = currency
        expense_accrual.source_amount = amount
        expense_accrual.source_currency = currency
        db.session.add(expense_accrual)
        db.session.commit()

        expense_payment = JournalEntries()
        expense_payment.timestamp = today
        expense_payment.debit_subaccount = cash_account
        expense_payment.credit_subaccount = receivables_account
        expense_payment.functional_amount = amount
        expense_payment.functional_currency = currency
        expense_payment.source_amount = amount
        expense_payment.source_currency = currency
        db.session.add(expense_payment)
        db.session.commit()

        prior_month_balance = (
            db.session.query(TrialBalances)
                .filter(TrialBalances.period_interval == 'YYYY-MM')
                .filter(TrialBalances.subaccount == receivables_account)
                .order_by(TrialBalances.period.desc()).offset(1).limit(1).first()
        )
        assert prior_month_balance.net_balance == Decimal('100')

        current_month_balance = (
            db.session.query(TrialBalances)
                .filter(TrialBalances.period_interval == 'YYYY-MM')
                .filter(TrialBalances.subaccount == receivables_account)
                .order_by(TrialBalances.period.desc()).limit(1).first()
        )
        print(pformat(current_month_balance.__dict__))
        assert current_month_balance.net_balance == Decimal('0')

if __name__ == '__main__':
    unittest.main()
