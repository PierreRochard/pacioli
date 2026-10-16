"""Reconcile PostgreSQL's derived balances against an independent journal oracle."""

from datetime import datetime, timezone
from decimal import Decimal
import random
import re
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pacioli.extensions import db, run_sql
from pacioli.models import BankFeedAccounts, JournalEntries, TrialBalances


INTERVALS = ('YYYY', 'YYYY-Q', 'YYYY-MM', 'YYYY-WW', 'YYYY-MM-DD')
FIELDS = ('debit_balance', 'credit_balance', 'net_balance',
          'debit_changes', 'credit_changes', 'net_changes')


def utc(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def period_key(timestamp, interval):
    """Reference labels; PostgreSQL WW counts seven-day blocks from Jan 1."""
    day = timestamp.astimezone(timezone.utc)
    year = str(day.year)
    if interval == 'YYYY':
        return year
    if interval == 'YYYY-Q':
        return '{}-{}'.format(year, (day.month - 1) // 3 + 1)
    if interval == 'YYYY-MM':
        return '{}-{:02d}'.format(year, day.month)
    if interval == 'YYYY-WW':
        return '{}-{:02d}'.format(year, (day.timetuple().tm_yday - 1) // 7 + 1)
    if interval == 'YYYY-MM-DD':
        return day.strftime('%Y-%m-%d')
    raise AssertionError(interval)


def post(when, debit, credit, amount, **kwargs):
    amount = Decimal(str(amount))
    entry = JournalEntries(timestamp=when, debit_subaccount=debit,
                           credit_subaccount=credit, functional_amount=amount,
                           functional_currency='USD', source_amount=amount,
                           source_currency='USD', **kwargs)
    db.session.add(entry)
    db.session.commit()
    return entry


def balance_snapshot():
    db.session.expire_all()
    return {
        (row.subaccount, row.period_interval, row.period):
        tuple(getattr(row, field) for field in FIELDS)
        for row in db.session.query(TrialBalances).all()
    }


def assert_ledger_matches_journals(intervals=INTERVALS):
    """Check all stored metrics and the as-of balance for every active account."""
    db.session.expire_all()
    entries = db.session.query(JournalEntries).all()
    rows = balance_snapshot()

    for interval in intervals:
        periods = {period_key(entry.timestamp, interval) for entry in entries}
        assert {period for _, name, period in rows if name == interval} == periods
        for period in periods:
            expected_total = Decimal(0)
            stored_total = Decimal(0)
            active_accounts = {account for entry in entries
                               if period_key(entry.timestamp, interval) <= period
                               for account in (entry.debit_subaccount,
                                               entry.credit_subaccount)}
            assert {name for name, label, key in rows
                    if label == interval and key == period} == active_accounts
            for account in active_accounts:
                debit_balance = sum((entry.functional_amount for entry in entries
                                     if entry.debit_subaccount == account and
                                     period_key(entry.timestamp, interval) <= period), Decimal(0))
                credit_balance = sum((entry.functional_amount for entry in entries
                                      if entry.credit_subaccount == account and
                                      period_key(entry.timestamp, interval) <= period), Decimal(0))
                debit_changes = sum((entry.functional_amount for entry in entries
                                     if entry.debit_subaccount == account and
                                     period_key(entry.timestamp, interval) == period), Decimal(0))
                credit_changes = sum((entry.functional_amount for entry in entries
                                      if entry.credit_subaccount == account and
                                      period_key(entry.timestamp, interval) == period), Decimal(0))
                expected = (debit_balance, credit_balance,
                            debit_balance - credit_balance,
                            debit_changes, credit_changes,
                            debit_changes - credit_changes)
                key = (account, interval, period)
                assert rows[key] == expected, '{}: {} != {}'.format(key, rows[key], expected)
                stored_total += rows[key][2]
                expected_total += expected[2]
            assert expected_total == 0, (interval, period, expected_total)
            assert stored_total == 0, (interval, period, stored_total)


@pytest.mark.parametrize('interval', INTERVALS)
def test_postings_and_carry_forward_match_independent_oracle(clean_ledger, interval):
    post(utc(2032, 12, 31), 'Rent', 'Accounts Payable', '100.01')
    post(utc(2033, 1, 1), 'Chase Checking', 'Salary', '40.02')
    post(utc(2033, 1, 9), 'Accounts Payable', 'Chase Checking', '25.03')
    post(utc(2033, 2, 1), 'Chase Checking', 'Chase Checking', '0.04')
    assert_ledger_matches_journals((interval,))


@pytest.mark.parametrize('when,expected', [
    (utc(2044, 2, 29), ('2044', '2044-1', '2044-02', '2044-09', '2044-02-29')),
    (utc(2044, 3, 31), ('2044', '2044-1', '2044-03', '2044-13', '2044-03-31')),
    (utc(2044, 4, 1), ('2044', '2044-2', '2044-04', '2044-14', '2044-04-01')),
    (utc(2044, 12, 31), ('2044', '2044-4', '2044-12', '2044-53', '2044-12-31')),
    (utc(2045, 1, 1), ('2045', '2045-1', '2045-01', '2045-01', '2045-01-01')),
])
def test_period_labels_cover_leap_quarter_week_and_year_boundaries(when, expected):
    assert tuple(period_key(when, interval) for interval in INTERVALS) == expected


def test_backdated_changes_and_deletion_reconcile_every_period(clean_ledger):
    first = post(utc(2034, 1, 10), 'Rent', 'Accounts Payable', '100')
    second = post(utc(2034, 3, 10), 'Chase Checking', 'Salary', '40')
    assert_ledger_matches_journals()

    older = post(utc(2033, 12, 15), 'Groceries', 'Chase Checking', '7.25')
    assert_ledger_matches_journals()

    first.functional_amount = Decimal('120.05')
    first.source_amount = Decimal('120.05')
    db.session.commit()
    assert_ledger_matches_journals()

    first.debit_subaccount = 'Groceries'
    first.credit_subaccount = 'Chase Checking'
    db.session.commit()
    assert_ledger_matches_journals()

    first.timestamp = utc(2034, 4, 3)
    db.session.commit()
    assert_ledger_matches_journals()

    first.timestamp = utc(2033, 11, 3)
    db.session.commit()
    assert_ledger_matches_journals()

    db.session.delete(older)
    db.session.commit()
    assert_ledger_matches_journals()
    db.session.delete(second)
    db.session.commit()
    assert_ledger_matches_journals()
    db.session.delete(first)
    db.session.commit()
    assert_ledger_matches_journals()


@pytest.mark.parametrize('invalid', [
    {'functional_amount': Decimal('-0.01')},
    {'source_amount': Decimal('-0.01')},
    {'debit_subaccount': 'No such account'},
    {'credit_subaccount': None},
    {'timestamp': None},
])
def test_invalid_posting_rolls_back_without_changing_balances(clean_ledger, invalid):
    post(utc(2037, 5, 1), 'Rent', 'Accounts Payable', '5')
    before = balance_snapshot()
    values = dict(timestamp=utc(2037, 5, 2), debit_subaccount='Rent',
                  credit_subaccount='Accounts Payable',
                  functional_amount=Decimal('2'), functional_currency='USD',
                  source_amount=Decimal('2'), source_currency='USD')
    values.update(invalid)
    with pytest.raises(IntegrityError):
        db.session.add(JournalEntries(**values))
        db.session.commit()
    db.session.rollback()
    assert balance_snapshot() == before
    assert db.session.query(JournalEntries).count() == 1


def test_duplicate_source_transaction_cannot_double_post(clean_ledger):
    post(utc(2037, 6, 1), 'Rent', 'Accounts Payable', '9',
         transaction_source='manual-test', transaction_id='bank-1')
    before = balance_snapshot()
    with pytest.raises(IntegrityError):
        post(utc(2037, 6, 2), 'Rent', 'Accounts Payable', '9',
             transaction_source='manual-test', transaction_id='bank-1')
    db.session.rollback()
    assert balance_snapshot() == before
    assert db.session.query(JournalEntries).count() == 1


def test_balances_use_functional_amount_and_keep_gross_same_account_activity(clean_ledger):
    post(utc(2037, 7, 1), 'Chase Checking', 'Chase Checking', '2.00')
    entry = post(utc(2037, 7, 2), 'Rent', 'Accounts Payable', '80')
    entry.source_amount = Decimal('100')
    entry.source_currency = 'EUR'
    entry.conversion_rate = Decimal('0.80')
    entry.conversion_source = 'fixture-rate'
    entry.conversion_date = utc(2037, 7, 2).date()
    db.session.commit()
    assert_ledger_matches_journals()
    checking = balance_snapshot()[('Chase Checking', 'YYYY-MM', '2037-07')]
    assert checking == (Decimal('2.00'), Decimal('2.00'), Decimal(0),
                        Decimal('2.00'), Decimal('2.00'), Decimal(0))
    assert balance_snapshot()[('Rent', 'YYYY-MM', '2037-07')][0] == Decimal('80')


def test_zero_amount_entry_is_rejected_without_derived_rows(clean_ledger):
    with pytest.raises(IntegrityError):
        post(utc(2037, 8, 1), 'Rent', 'Accounts Payable', '0')
    db.session.rollback()
    assert db.session.query(JournalEntries).count() == 0
    assert balance_snapshot() == {}


def test_refresh_rebuilds_exact_amounts_and_is_idempotent(clean_ledger, client, admin_user):
    post(utc(2039, 1, 10), 'Rent', 'Accounts Payable', '100.01')
    post(utc(2039, 2, 10), 'Chase Checking', 'Salary', '40.02')
    before = balance_snapshot()
    assert_ledger_matches_journals()
    client.post('/login/', data=admin_user)
    endpoint = '/trialbalances/refresh_subaccounts/'
    assert client.post(endpoint).status_code == 302
    assert_ledger_matches_journals()
    assert balance_snapshot() == before
    first_refresh = balance_snapshot()
    assert client.post(endpoint).status_code == 302
    assert balance_snapshot() == first_refresh


def test_failed_refresh_rolls_back_the_truncate(
        clean_ledger, client, admin_user, monkeypatch):
    post(utc(2039, 3, 10), 'Rent', 'Accounts Payable', '9')
    before = balance_snapshot()
    client.post('/login/', data=admin_user)
    original_execute = db.session.execute

    def fail_after_truncate(statement, *args, **kwargs):
        if 'SELECT bookkeeping.update_trial_balance' in str(statement):
            raise RuntimeError('injected rebuild failure')
        return original_execute(statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(db.session, 'execute', fail_after_truncate)
        with pytest.raises(RuntimeError, match='injected rebuild failure'):
            client.post('/trialbalances/refresh_subaccounts/')
    db.session.remove()
    assert balance_snapshot() == before
    assert_ledger_matches_journals()


def test_financial_statements_and_drilldowns_reconcile_to_journal(
        clean_ledger, app, client, admin_user):
    from flask import request
    from pacioli.extensions import admin
    from pacioli.functions.accounting_report import statement

    january = post(utc(2040, 1, 10), 'Rent', 'Accounts Payable', '100')
    february = post(utc(2040, 2, 10), 'Chase Checking', 'Salary', '40')
    assert_ledger_matches_journals(('YYYY-MM',))
    journal_view = next(view for view in admin._views if view.endpoint == 'journalentries')
    report = statement(db.session, 'YYYY-MM', '2040-02')
    assert dict(report['income_rows']) == {'Salary': Decimal('40')}
    assert dict(report['balance_rows']) == {
        'Accounts Payable': Decimal('-100'), 'Chase Checking': Decimal('40')}
    assert (report['assets'], report['liabilities'], report['equity'],
            report['equation_difference']) == (
                Decimal('40'), Decimal('100'), Decimal('-60'), Decimal('0'))
    with app.test_request_context('/journalentries/Rent/YYYY-MM/2040-01/'):
        request.view_args = {'subaccount': 'Rent', 'period_interval': 'YYYY-MM',
                             'period': '2040-01'}
        assert [row.id for row in journal_view.get_query().all()] == [january.id]
        assert journal_view.get_count_query().scalar() == 1
    with app.test_request_context('/journalentries/Accounts Payable/YYYY-MM/2040-02/balance-sheet/'):
        request.view_args = {'subaccount': 'Accounts Payable',
                             'period_interval': 'YYYY-MM', 'period': '2040-02',
                             'cumulative': 'balance-sheet'}
        assert [row.id for row in journal_view.get_query().all()] == [january.id]
        assert journal_view.get_count_query().scalar() == 1
    with app.test_request_context('/journalentries/Salary/YYYY-MM/2040-02/'):
        request.view_args = {'subaccount': 'Salary', 'period_interval': 'YYYY-MM',
                             'period': '2040-02'}
        assert [row.id for row in journal_view.get_query().all()] == [february.id]

    client.post('/login/', data=admin_user)
    for path, label in (
        ('/income-statements/YYYY-MM/2040-02/', b'Net Income'),
        ('/balance-sheet/YYYY-MM/2040-02/', b'Net Equity'),
        ('/journalentries/Rent/YYYY-MM/2040-01/', b'Rent'),
        ('/journalentries/Accounts%20Payable/YYYY-MM/2040-02/balance-sheet/',
         b'Accounts Payable'),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert label in response.data, path
    assert re.search(rb'<strong>Net Income</strong>:\s*40\.00', client.get(
        '/income-statements/YYYY-MM/2040-02/').data)
    assert re.search(rb'<strong>Net Equity</strong>:\s*\(60\.00\)', client.get(
        '/balance-sheet/YYYY-MM/2040-02/').data)


def test_statement_categories_partition_the_trial_balance(clean_ledger, app):
    from pacioli.functions.accounting_report import statement
    from pacioli.models import Accounts, Classifications, Subaccounts

    suffix = uuid4().hex[:8]
    categories = ('Capital Contributions', 'Capital Distributions', 'Gains', 'Losses')
    names = {}
    for category in categories:
        classification = 'Class {} {}'.format(category, suffix)
        account = 'Account {} {}'.format(category, suffix)
        subaccount = 'Subaccount {} {}'.format(category, suffix)
        db.session.add(Classifications(name=classification, parent=category))
        db.session.add(Accounts(name=account, parent=classification))
        db.session.add(Subaccounts(name=subaccount, parent=account))
        names[category] = subaccount
    db.session.commit()

    post(utc(2042, 5, 1), 'Chase Checking', names['Capital Contributions'], '100')
    post(utc(2042, 5, 2), names['Capital Distributions'], 'Chase Checking', '20')
    post(utc(2042, 5, 3), 'Chase Checking', names['Gains'], '5')
    post(utc(2042, 5, 4), names['Losses'], 'Chase Checking', '3')
    assert_ledger_matches_journals(('YYYY-MM',))

    report = statement(db.session, 'YYYY-MM', '2042-05')
    assert dict(report['income_rows']) == {
        names['Gains']: Decimal('5'), names['Losses']: Decimal('-3')}
    assert dict(report['balance_rows']) == {'Chase Checking': Decimal('82'),
                            names['Capital Contributions']: Decimal('-100'),
                            names['Capital Distributions']: Decimal('20')}
    assert report['equation_difference'] == 0


def test_empty_ledger_default_statements_render(clean_ledger, client, admin_user):
    client.post('/login/', data=admin_user)
    for path in ('/income-statements/', '/balance-sheet/', '/journalentries/'):
        assert client.get(path).status_code == 200, path


def test_fixed_seed_mixed_journal_edits_match_oracle(clean_ledger):
    """A reproducible small state-machine check catches interaction errors."""
    generator = random.Random(20260923)
    accounts = ('Rent', 'Groceries', 'Chase Checking',
                'Accounts Payable', 'Salary')
    active = []
    for _ in range(20):
        operation = generator.choice(('insert', 'insert', 'update', 'delete'))
        if operation == 'insert' or not active:
            entry = post(utc(2041 + generator.randrange(2),
                             1 + generator.randrange(4),
                             1 + generator.randrange(27)),
                         *generator.sample(accounts, 2),
                         str(generator.randrange(1, 100)) + '.01')
            active.append(entry)
        elif operation == 'update':
            entry = generator.choice(active)
            entry.debit_subaccount, entry.credit_subaccount = generator.sample(accounts, 2)
            entry.functional_amount = Decimal(generator.randrange(1, 100)) / 100
            entry.source_amount = entry.functional_amount
            entry.timestamp = utc(2041 + generator.randrange(2),
                                  1 + generator.randrange(4),
                                  1 + generator.randrange(27))
            db.session.commit()
        else:
            entry = generator.choice(active)
            active.remove(entry)
            db.session.delete(entry)
            db.session.commit()
        assert_ledger_matches_journals()


def test_periods_are_utc_even_when_database_session_uses_another_timezone(clean_ledger):
    sql = text('''INSERT INTO bookkeeping.journal_entries
                  (timestamp, debit_subaccount, credit_subaccount,
                   functional_amount, functional_currency, source_amount,
                   source_currency)
                  VALUES (:timestamp, 'Rent', 'Accounts Payable', 1, 'USD', 1, 'USD')''')
    connection = db.engine.connect()
    try:
        with connection.begin():
            connection.exec_driver_sql("SET LOCAL TIME ZONE 'America/Chicago'")
            connection.execute(sql, {'timestamp': utc(2045, 1, 1, 0, 30)})
    finally:
        connection.close()
    db.session.remove()
    assert_ledger_matches_journals()
    assert ('Rent', 'YYYY-MM-DD', '2045-01-01') in balance_snapshot()
    assert ('Rent', 'YYYY', '2044') not in balance_snapshot()


def test_amazon_mapping_reconciles_refund_and_is_idempotent(clean_ledger):
    from pacioli.functions import amazon_functions
    from pacioli.models import Mappings

    suffix = uuid4().hex[:10]
    report = (
        'Order Date,Order ID,Title,Shipment Date,Item Total,Currency\n'
        '2046-01-10,ORDER-{key},{key} purchase,2046-01-12,12.34,USD\n'
        '2046-01-10,ORDER-{key},{key} refund,2046-02-01,(5.00),USD\n'
    ).format(key=suffix)
    assert amazon_functions.import_amazon_csv(report) == 2
    mapping = Mappings(source='amazon', keyword=suffix,
                       positive_debit_subaccount_id='Groceries',
                       positive_credit_subaccount_id='Amazon Payable',
                       negative_debit_subaccount_id='Amazon Payable',
                       negative_credit_subaccount_id='Groceries')
    db.session.add(mapping)
    db.session.commit()
    db.session.execute(text("SET LOCAL TIME ZONE 'Pacific/Auckland'"))
    amazon_functions.apply_single_amazon_mapping(mapping.id)
    entries = db.session.query(JournalEntries).all()
    assert len(entries) == 2
    assert {entry.timestamp for entry in entries} == {
        utc(2046, 1, 12, 0), utc(2046, 2, 1, 0)}
    assert_ledger_matches_journals()
    before = balance_snapshot()
    amazon_functions.apply_single_amazon_mapping(mapping.id)
    assert db.session.query(JournalEntries).count() == 2
    assert balance_snapshot() == before


def test_ofx_positive_and_negative_mappings_reconcile_and_do_not_repost(clean_ledger):
    from pacioli.functions import ofx_functions
    from pacioli.models import Mappings

    suffix = uuid4().hex[:10]
    account_id = run_sql(text(
        "INSERT INTO ofx.acctfrom (subclass, name) "
        "VALUES ('bankacctfrom', 'Chase Checking') RETURNING id"
    )).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    db.session.commit()
    for identifier, amount, posted in (
        ('credit', Decimal('30'), utc(2047, 1, 10)),
        ('debit', Decimal('-12'), utc(2047, 2, 10)),
    ):
        run_sql(text('''INSERT INTO ofx.stmttrn
            (fitid, acctfrom_id, trntype, dtposted, trnamt, name)
            VALUES (:fitid, :account_id, 'OTHER', :posted, :amount, :name)'''),
            {'fitid': suffix + identifier, 'account_id': account_id,
             'posted': posted, 'amount': amount, 'name': suffix + ' expense'})
    mapping = Mappings(source='ofx', keyword=suffix,
                       positive_credit_subaccount_id='Salary',
                       negative_debit_subaccount_id='Rent')
    db.session.add(mapping)
    db.session.commit()
    ofx_functions.apply_single_ofx_mapping(mapping.id)
    entries = db.session.query(JournalEntries).all()
    assert {(entry.debit_subaccount, entry.credit_subaccount,
             entry.functional_amount) for entry in entries} == {
                 ('Chase Checking', 'Salary', Decimal('30')),
                 ('Rent', 'Chase Checking', Decimal('12')),
             }
    assert_ledger_matches_journals()
    before = balance_snapshot()
    ofx_functions.apply_single_ofx_mapping(mapping.id)
    assert db.session.query(JournalEntries).count() == 2
    assert balance_snapshot() == before


def test_ofx_mapping_failure_rolls_back_earlier_postings(clean_ledger):
    from pacioli.functions import ofx_functions
    from pacioli.models import Mappings

    suffix = uuid4().hex[:10]
    account_id = run_sql(text(
        "INSERT INTO ofx.acctfrom (subclass, name) "
        "VALUES ('bankacctfrom', 'Chase Checking') RETURNING id"
    )).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    db.session.commit()
    for identifier, amount, posted in (
        ('valid', Decimal('9'), utc(2048, 2, 2)),
        ('zero', Decimal('0'), utc(2048, 2, 1)),
    ):
        run_sql(text('''INSERT INTO ofx.stmttrn
            (fitid, acctfrom_id, trntype, dtposted, trnamt, name)
            VALUES (:fitid, :account_id, 'OTHER', :posted, :amount, :name)'''),
            {'fitid': suffix + identifier, 'account_id': account_id,
             'posted': posted, 'amount': amount, 'name': suffix + ' expense'})
    mapping = Mappings(source='ofx', keyword=suffix,
                       positive_credit_subaccount_id='Salary',
                       negative_debit_subaccount_id='Rent')
    db.session.add(mapping)
    db.session.commit()
    with pytest.raises(ValueError, match='zero-amount'):
        ofx_functions.apply_single_ofx_mapping(mapping.id)
    assert db.session.query(JournalEntries).count() == 0
    assert balance_snapshot() == {}


def test_two_database_connections_preserve_both_postings(clean_ledger):
    engine = db.engine
    first = engine.connect()
    transaction = first.begin()
    second_started = Event()
    errors = []

    sql = text('''INSERT INTO bookkeeping.journal_entries
                  (timestamp, debit_subaccount, credit_subaccount,
                   functional_amount, functional_currency, source_amount,
                   source_currency)
                  VALUES (:timestamp, :debit, :credit, :amount, 'USD', :amount, 'USD')''')
    try:
        first.execute(sql, dict(timestamp=utc(2038, 1, 1), debit='Rent',
                                credit='Accounts Payable', amount=Decimal('11')))

        def post_second():
            connection = engine.connect()
            try:
                second_started.set()
                with connection.begin():
                    connection.execute(sql, dict(timestamp=utc(2038, 1, 2),
                                                 debit='Groceries', credit='Accounts Payable',
                                                 amount=Decimal('13')))
            except Exception as error:
                errors.append(error)
            finally:
                connection.close()

        worker = Thread(target=post_second)
        worker.start()
        assert second_started.wait(5)
        transaction.commit()
        worker.join(10)
        assert not worker.is_alive(), 'second posting remained blocked'
        assert not errors, errors
    finally:
        if transaction.is_active:
            transaction.rollback()
        first.close()
    db.session.remove()
    assert db.session.query(JournalEntries).count() == 2
    assert_ledger_matches_journals()
