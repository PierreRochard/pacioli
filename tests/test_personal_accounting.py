"""End-to-end controls for journal-derived personal financial statements."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from pacioli.extensions import db, run_sql
from pacioli.functions.accounting_report import (audit_ledger, bank_balance_differences,
                                                period_bounds, source_differences,
                                                statement)
from pacioli.functions.journal_posting import (post_allocations, post_pair,
                                               reverse_entry)
from pacioli.functions.period_close import close_month, closed_period_differences
from pacioli.functions.source_identity import source_identity
from pacioli.functions.accrual_posting import generate_accruals
from pacioli.models import (Accruals, AmazonItems, BankFeedAccounts, JournalEntries,
                            JournalEntryEvents, Mappings, TrialBalances, ClosedPeriods,
                            Subaccounts)
from pacioli.functions.amazon_functions import apply_single_amazon_mapping


def at(year, month, day):
    return datetime(year, month, day, 12, tzinfo=timezone.utc)


def test_quiet_month_carries_assets_and_equity_and_displays_as_of_statement(
        clean_ledger, client, admin_user):
    post_pair(timestamp=at(2040, 1, 10), debit='Chase Checking',
              credit='Personal Equity', functional_amount='100.00')
    post_pair(timestamp=at(2040, 3, 10), debit='Rent',
              credit='Chase Checking', functional_amount='20.00')
    db.session.commit()

    february = statement(db.session, 'YYYY-MM', '2040-02')
    assert (february['assets'], february['liabilities'], february['equity'],
            february['cash'], february['equation_difference']) == (
                Decimal('100'), Decimal('0'), Decimal('100'),
                Decimal('100'), Decimal('0'))
    assert february['current_income'] == 0
    march = statement(db.session, 'YYYY-MM', '2040-03')
    assert (march['assets'], march['contributions'], march['current_income'],
            march['cumulative_income'], march['equity']) == (
                Decimal('80'), Decimal('100'), Decimal('-20'),
                Decimal('-20'), Decimal('80'))

    client.post('/login/', data=admin_user)
    balance = client.get('/balance-sheet/YYYY-MM/2040-02/')
    income = client.get('/income-statements/YYYY-MM/2040-02/')
    assert balance.status_code == income.status_code == 200
    assert b'<strong>Net Equity</strong>: 100.00' in balance.data
    assert b'<strong>Net Income</strong>: 0.00' in income.data
    assert b'2040-02' in balance.data


def test_periods_are_validated_and_weekly_cutoff_matches_postgres():
    assert period_bounds('YYYY-WW', '2045-01') == (
        datetime(2045, 1, 1, tzinfo=timezone.utc),
        datetime(2045, 1, 8, tzinfo=timezone.utc))
    assert period_bounds('YYYY-Q', '2044-4')[1] == datetime(
        2045, 1, 1, tzinfo=timezone.utc)
    for label in ('2045-00', '2045-14', '2045-1'):
        with pytest.raises(ValueError):
            period_bounds('YYYY-MM', label)


def test_reconciliation_detects_tampered_balance_and_source_amount(clean_ledger):
    item = AmazonItems(item_total=Decimal('5.00'), currency='USD',
                       title='Accounting fixture', shipment_date=date(2041, 1, 1))
    db.session.add(item)
    db.session.flush()
    post_pair(timestamp=at(2041, 1, 1), debit='Rent', credit='Accounts Payable',
              functional_amount='4.00', transaction_source='amazon',
              transaction_id=str(item.id))
    db.session.commit()
    assert source_differences(db.session) == [
        {'source': 'amazon', 'transaction_id': str(item.id),
         'booked': Decimal('4.00'), 'source_amount': Decimal('5.00')}]

    run_sql("UPDATE bookkeeping.trial_balances SET net_balance = net_balance + 1 "
                      "WHERE subaccount = 'Rent' AND period_interval = 'YYYY-MM'")
    db.session.expire_all()
    issues = audit_ledger(db.session)
    assert {'source_amount', 'trial_balance'} <= {issue['kind'] for issue in issues}


@pytest.mark.parametrize('value', [0, '-1', '0.001', float('nan'), 1.5])
def test_posting_service_rejects_invalid_usd_amounts(clean_ledger, value):
    with pytest.raises((TypeError, ValueError)):
        post_pair(timestamp=at(2042, 2, 1), debit='Rent',
                  credit='Chase Checking', functional_amount=value)
    assert db.session.query(JournalEntries).count() == 0


def test_posting_service_requires_aware_timestamp(clean_ledger):
    with pytest.raises(ValueError, match='UTC-aware'):
        post_pair(timestamp=datetime(2042, 2, 1), debit='Rent',
                  credit='Chase Checking', functional_amount='1.00')


def test_self_offset_is_rejected_by_service_and_flagged_if_legacy(clean_ledger):
    with pytest.raises(ValueError, match='must differ'):
        post_pair(timestamp=at(2042, 2, 1), debit='Chase Checking',
                  credit='Chase Checking', functional_amount='1.00')
    entry = JournalEntries(timestamp=at(2042, 2, 1),
                           debit_subaccount='Chase Checking',
                           credit_subaccount='Chase Checking',
                           functional_amount=Decimal('1.00'),
                           functional_currency='USD',
                           source_amount=Decimal('1.00'), source_currency='USD')
    db.session.add(entry)
    db.session.commit()
    issues = audit_ledger(db.session)
    assert [issue['kind'] for issue in issues] == ['self_offset']


@pytest.mark.parametrize('values', [
    {'functional_amount': Decimal('0')},
    {'functional_amount': Decimal('1.001'), 'source_amount': Decimal('1.001')},
    {'source_currency': 'EUR'},
    {'source_amount': Decimal('2')},
    {'transaction_source': 'ofx'},
])
def test_database_rejects_invalid_raw_postings(clean_ledger, values):
    fields = dict(timestamp=at(2042, 2, 1), debit_subaccount='Rent',
                  credit_subaccount='Chase Checking', functional_amount=Decimal('1'),
                  functional_currency='USD', source_amount=Decimal('1'),
                  source_currency='USD')
    fields.update(values)
    db.session.add(JournalEntries(**fields))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert db.session.query(JournalEntries).count() == 0
    assert db.session.query(TrialBalances).count() == 0


def test_foreign_currency_requires_reproducible_conversion_evidence(clean_ledger):
    with pytest.raises(ValueError, match='explicit source amount'):
        post_pair(timestamp=at(2042, 2, 1), debit='Rent',
                  credit='Accounts Payable', functional_amount='80.00',
                  source_currency='EUR', conversion_rate='0.80',
                  conversion_source='test quote',
                  conversion_date=date(2042, 2, 1))
    with pytest.raises(ValueError, match='rate, source, and date'):
        post_pair(timestamp=at(2042, 2, 1), debit='Rent',
                  credit='Chase Checking', functional_amount='80.00',
                  source_amount='100.00', source_currency='EUR')
    with pytest.raises(ValueError, match='rate, source, and date'):
        post_pair(timestamp=at(2042, 2, 1), debit='Rent',
                  credit='Accounts Payable', functional_amount='80.00',
                  source_amount='100.00', source_currency='EUR',
                  conversion_rate='0.80', conversion_source='  ',
                  conversion_date=date(2042, 2, 1))
    entry = post_pair(timestamp=at(2042, 2, 1), debit='Rent',
                      credit='Accounts Payable', functional_amount='80.00',
                      source_amount='100.00', source_currency='EUR',
                      conversion_rate='0.80', conversion_source='test quote',
                      conversion_date=date(2042, 2, 1))
    db.session.commit()
    assert entry.conversion_rate == Decimal('0.80')
    assert audit_ledger(db.session) == []


def test_split_posting_retries_and_rejects_changed_allocations(clean_ledger):
    kwargs = dict(timestamp=at(2043, 1, 1), source='manual-test',
                  transaction_id='split-1', fixed_account='Chase Checking',
                  allocations=[('rent', 'Rent', '7.00'),
                               ('food', 'Groceries', '3.00')], total='10.00')
    with pytest.raises(ValueError, match='do not equal'):
        post_allocations(**dict(kwargs, total='9.00'))
    entries = post_allocations(**kwargs)
    db.session.commit()
    assert len(entries) == 2
    assert len(post_allocations(**kwargs)) == 2
    with pytest.raises(ValueError, match='different allocation'):
        post_allocations(**dict(kwargs, allocations=[('rent', 'Rent', '8.00'),
                                                ('food', 'Groceries', '2.00')]))
    assert statement(db.session, 'YYYY-MM', '2043-01')['cash'] == Decimal('-10')
    assert audit_ledger(db.session) == []


def test_reversal_preserves_original_and_records_reason(clean_ledger):
    original = post_pair(timestamp=at(2044, 1, 10), debit='Rent',
                         credit='Accounts Payable', functional_amount='12.00')
    db.session.commit()
    reversal = reverse_entry(original.id, timestamp=at(2044, 2, 10),
                             reason='Incorrect invoice')
    db.session.commit()
    assert reverse_entry(original.id, timestamp=at(2044, 2, 10),
                         reason='Incorrect invoice').id == reversal.id
    assert db.session.query(JournalEntries).count() == 2
    assert statement(db.session, 'YYYY-MM', '2044-02')['equation_difference'] == 0
    assert statement(db.session, 'YYYY-MM', '2044-02')['cumulative_income'] == 0
    events = db.session.query(JournalEntryEvents).order_by(JournalEntryEvents.id).all()
    assert [event.action for event in events] == ['INSERT', 'INSERT']
    assert events[1].reason == 'Incorrect invoice'
    assert audit_ledger(db.session) == []


def test_imported_bank_balance_requires_opening_cash_or_all_transactions(clean_ledger):
    account_id = run_sql(text(
        "INSERT INTO ofx.acctfrom (subclass, name) "
        "VALUES ('bankacctfrom', 'Chase Checking') RETURNING id")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    db.session.commit()
    as_of = at(2045, 1, 31).replace(hour=0)
    try:
        run_sql(text("""INSERT INTO ofx.ledgerbal
            (acctfrom_id, dtasof, balamt) VALUES (:id, :as_of, 100)
        """), {'id': account_id, 'as_of': as_of})
        differences = bank_balance_differences(db.session)
        assert len(differences) == 1
        assert differences[0]['booked'] == 0
        assert differences[0]['bank_balance'] == 100
        post_pair(timestamp=at(2045, 1, 31), debit='Chase Checking',
                  credit='Personal Equity', functional_amount='100.00')
        db.session.commit()
        assert bank_balance_differences(db.session) == []
        assert audit_ledger(db.session) == []
    finally:
        db.session.rollback()
        run_sql(text('DELETE FROM ofx.ledgerbal WHERE acctfrom_id = :id'),
                          {'id': account_id})
        run_sql(text('DELETE FROM ofx.acctfrom WHERE id = :id'),
                          {'id': account_id})


def test_direct_journal_edit_is_recorded_and_audit_history_is_immutable(clean_ledger):
    entry = post_pair(timestamp=at(2046, 1, 1), debit='Rent',
                      credit='Accounts Payable', functional_amount='10.00')
    db.session.commit()
    entry.functional_amount = Decimal('11.00')
    entry.source_amount = Decimal('11.00')
    db.session.commit()
    events = db.session.query(JournalEntryEvents).order_by(JournalEntryEvents.id).all()
    assert [event.action for event in events] == ['INSERT', 'UPDATE']
    assert events[1].before_row['functional_amount'] == 10
    assert events[1].after_row['functional_amount'] == 11
    with pytest.raises(DBAPIError):
        db.session.execute(text(
            "UPDATE bookkeeping.journal_entry_events SET action = 'ERASED' WHERE id = :id"),
            {'id': events[0].id})
        db.session.commit()
    db.session.rollback()
    assert db.session.query(JournalEntryEvents).count() == 2


def test_old_amazon_expense_suspense_mapping_is_rejected(clean_ledger):
    mapping = Mappings(source='amazon', keyword='review-me',
                       positive_debit_subaccount_id='Groceries',
                       positive_credit_subaccount_id='Amazon Suspense Account')
    db.session.add(mapping)
    db.session.commit()
    with pytest.raises(ValueError, match='liability or cash'):
        apply_single_amazon_mapping(mapping.id)
    assert db.session.query(JournalEntries).count() == 0


def test_amazon_purchase_and_bank_settlement_do_not_double_count_expense(clean_ledger):
    item = AmazonItems(item_total=Decimal('10.00'), currency='USD',
                       title='Cross-source purchase', shipment_date=date(2048, 2, 1))
    db.session.add(item)
    db.session.flush()
    account_id = run_sql(text(
        "INSERT INTO ofx.acctfrom (subclass, name) "
        "VALUES ('bankacctfrom', 'Chase Checking') RETURNING id")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    db.session.commit()
    fitid = 'SETTLEMENT-{}'.format(item.id)
    try:
        run_sql(text("""INSERT INTO ofx.stmttrn
            (fitid, acctfrom_id, trntype, dtposted, trnamt, name)
            VALUES (:fitid, :account, 'DEBIT', :posted, -10, 'Amazon payment')
        """), {'fitid': fitid, 'account': account_id,
               'posted': at(2048, 2, 2)})
        post_pair(timestamp=at(2048, 1, 1), debit='Chase Checking',
                  credit='Personal Equity', functional_amount='100.00')
        post_pair(timestamp=at(2048, 2, 1), debit='Groceries',
                  credit='Amazon Payable', functional_amount='10.00',
                  transaction_source='amazon', transaction_id=str(item.id))
        post_pair(timestamp=at(2048, 2, 2), debit='Amazon Payable',
                  credit='Chase Checking', functional_amount='10.00',
                  transaction_source='ofx',
                  transaction_id=source_identity(db.session, account_id, fitid))
        db.session.commit()
        report = statement(db.session, 'YYYY-MM', '2048-02')
        assert report['current_income'] == Decimal('-10')
        assert report['cash'] == Decimal('90')
        assert report['balances']['Amazon Payable'] == 0
        assert report['equity'] == Decimal('90')
        assert audit_ledger(db.session) == []
    finally:
        db.session.rollback()
        # Remove fixture postings before their now-FK-protected sources.
        run_sql(text("DELETE FROM bookkeeping.journal_entries WHERE transaction_source='ofx' "
                               "AND transaction_id=(SELECT public_id FROM bookkeeping.ofx_source_identities "
                               "WHERE acctfrom_id=:account AND fitid=:fitid)"),
                          {'fitid': fitid, 'account': account_id})
        run_sql(text('DELETE FROM ofx.stmttrn WHERE fitid = :fitid '
                               'AND acctfrom_id = :account'),
                          {'fitid': fitid, 'account': account_id})
        run_sql(text('DELETE FROM ofx.acctfrom WHERE id = :id'),
                          {'id': account_id})


def test_ofx_split_is_one_source_row_and_reconciles_full_amount(clean_ledger):
    account_id = run_sql(text(
        "INSERT INTO ofx.acctfrom (subclass, name) "
        "VALUES ('bankacctfrom', 'Chase Checking') RETURNING id")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    db.session.commit()
    fitid = 'SPLIT-TEST-{}'.format(account_id)
    try:
        run_sql(text("""INSERT INTO ofx.stmttrn
            (fitid, acctfrom_id, trntype, dtposted, trnamt, name)
            VALUES (:fitid, :account, 'DEBIT', :posted, -10, 'Split receipt')
        """), {'fitid': fitid, 'account': account_id,
               'posted': at(2049, 1, 1)})
        source_id = source_identity(db.session, account_id, fitid)
        post_allocations(timestamp=at(2049, 1, 1), source='ofx',
                         transaction_id=source_id,
                         fixed_account='Chase Checking', total='10.00',
                         allocations=[('rent', 'Rent', '7.00'),
                                      ('food', 'Groceries', '3.00')])
        db.session.commit()
        rows = db.session.execute(text(
            'SELECT journal_entry_id, debit_subaccount FROM ofx.transactions '
            'WHERE id = :id'), {'id': source_id}).fetchall()
        assert len(rows) == 1
        assert rows[0][0] is not None and rows[0][1] == '[split]'
        assert source_differences(db.session) == []
        assert audit_ledger(db.session) == []
    finally:
        db.session.rollback()
        # Remove fixture postings before their now-FK-protected sources.
        run_sql(text("DELETE FROM bookkeeping.journal_entries WHERE transaction_source='ofx' "
                               "AND transaction_id=(SELECT public_id FROM bookkeeping.ofx_source_identities "
                               "WHERE acctfrom_id=:account AND fitid=:fitid)"),
                          {'fitid': fitid, 'account': account_id})
        run_sql(text('DELETE FROM ofx.stmttrn WHERE fitid = :fitid '
                               'AND acctfrom_id = :account'),
                          {'fitid': fitid, 'account': account_id})
        run_sql(text('DELETE FROM ofx.acctfrom WHERE id = :id'),
                          {'id': account_id})


def test_ofx_currency_must_be_reviewed_before_mapping(clean_ledger, app):
    import manage

    account_id = run_sql(text(
        "INSERT INTO ofx.acctfrom (subclass, name) "
        "VALUES ('bankacctfrom', 'Chase Checking') RETURNING id")).scalar()
    fitid = 'CURRENCY-TEST-{}'.format(account_id)
    try:
        run_sql(text("""INSERT INTO ofx.stmttrn
            (fitid, acctfrom_id, trntype, dtposted, trnamt, name)
            VALUES (:fitid, :account, 'DEBIT', :posted, -10, 'Currency fixture')
        """), {'fitid': fitid, 'account': account_id,
               'posted': at(2049, 2, 1)})
        source_id = source_identity(db.session, account_id, fitid)
        kwargs = dict(timestamp=at(2049, 2, 1), debit='Rent',
                      credit='Chase Checking', functional_amount='10.00',
                      transaction_source='ofx', transaction_id=source_id)
        with pytest.raises(ValueError, match='reviewed USD'):
            post_pair(**kwargs)
        db.session.add(BankFeedAccounts(ofx_account_id=account_id,
                                        currency='EUR'))
        db.session.commit()
        with pytest.raises(ValueError, match='reviewed USD'):
            post_pair(**kwargs)
        db.session.get(BankFeedAccounts, account_id).currency = 'USD'
        db.session.commit()
        post_pair(**kwargs)
        db.session.commit()
        assert audit_ledger(db.session) == []
        app.cli.add_command(manage.app.cli.commands['set_ofx_account_currency'])
        confirmed = app.test_cli_runner().invoke(args=[
            'set_ofx_account_currency', '--account-id', str(account_id),
            '--currency', 'USD'])
        assert confirmed.exit_code == 0, confirmed.output
    finally:
        db.session.rollback()
        # Remove fixture postings before their now-FK-protected sources.
        run_sql(text("DELETE FROM bookkeeping.journal_entries WHERE transaction_source='ofx' "
                               "AND transaction_id=(SELECT public_id FROM bookkeeping.ofx_source_identities "
                               "WHERE acctfrom_id=:account AND fitid=:fitid)"),
                          {'fitid': fitid, 'account': account_id})
        run_sql(text('DELETE FROM ofx.stmttrn WHERE fitid = :fitid '
                               'AND acctfrom_id = :account'),
                          {'fitid': fitid, 'account': account_id})
        run_sql(text('DELETE FROM ofx.acctfrom WHERE id = :id'),
                          {'id': account_id})


def test_closed_month_preserves_statement_and_requires_later_reversal(clean_ledger):
    original = post_pair(timestamp=at(2025, 1, 10), debit='Chase Checking',
                         credit='Personal Equity', functional_amount='100.00')
    db.session.commit()
    closed = close_month(db.session, '2025-01')
    db.session.commit()
    original_digest = closed.journal_digest
    assert closed.journal_count == 1
    assert closed_period_differences(db.session) == []

    with pytest.raises(DBAPIError):
        post_pair(timestamp=at(2025, 1, 20), debit='Rent',
                  credit='Chase Checking', functional_amount='1.00')
        db.session.commit()
    db.session.rollback()
    with pytest.raises(DBAPIError):
        original.functional_amount = Decimal('101.00')
        original.source_amount = Decimal('101.00')
        db.session.commit()
    db.session.rollback()

    reverse_entry(original.id, timestamp=at(2025, 2, 1),
                  reason='Opening amount was incorrect')
    db.session.commit()
    assert db.session.get(ClosedPeriods, '2025-01').journal_digest == original_digest
    assert statement(db.session, 'YYYY-MM', '2025-01')['cash'] == Decimal('100')
    assert statement(db.session, 'YYYY-MM', '2025-02')['cash'] == 0
    assert audit_ledger(db.session) == []
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE bookkeeping.closed_periods "
                                "SET journal_digest = 'changed' WHERE period = '2025-01'"))
        db.session.commit()
    db.session.rollback()


def test_closed_statement_displays_snapshot_and_detects_reclassification(
        clean_ledger, client, admin_user):
    post_pair(timestamp=at(2025, 3, 1), debit='Chase Checking',
              credit='Personal Equity', functional_amount='100.00')
    db.session.commit()
    close_month(db.session, '2025-03')
    db.session.commit()
    account = db.session.query(Subaccounts).filter_by(name='Chase Checking').one()
    original_parent = account.parent
    try:
        account.parent = 'Groceries Expense'
        db.session.commit()
        assert any(issue['kind'] == 'closed_period' for issue in audit_ledger(db.session))
        client.post('/login/', data=admin_user)
        page = client.get('/balance-sheet/YYYY-MM/2025-03/')
        assert page.status_code == 200
        assert b'Closed statement no longer matches' in page.data
        assert b'<strong>Net Equity</strong>: 100.00' in page.data
    finally:
        db.session.rollback()
        account.parent = original_parent
        db.session.commit()
    assert audit_ledger(db.session) == []


def test_business_day_expense_accrual_settlement_and_idempotency(clean_ledger):
    schedule = Accruals(name='Daily expense', accrual_type='expense',
                        start_date=date(2025, 1, 3), end_date=date(2025, 1, 6),
                        business_days=True, daily_amount=Decimal('5.00'),
                        income_statement_subaccount='Rent',
                        balance_sheet_subaccount='Accounts Payable')
    db.session.add(schedule)
    db.session.commit()
    post_pair(timestamp=at(2025, 1, 1), debit='Chase Checking',
              credit='Personal Equity', functional_amount='20.00')
    assert len(generate_accruals(date(2025, 1, 6))) == 2
    db.session.commit()
    assert generate_accruals(date(2025, 1, 6)) == []
    assert db.session.query(JournalEntries).filter_by(
        transaction_source='accrual').count() == 2
    post_pair(timestamp=at(2025, 1, 7), debit='Accounts Payable',
              credit='Chase Checking', functional_amount='10.00')
    db.session.commit()
    report = statement(db.session, 'YYYY-MM', '2025-01')
    assert report['current_income'] == Decimal('-10')
    assert report['cash'] == Decimal('10')
    assert report['balances']['Accounts Payable'] == 0
    assert audit_ledger(db.session) == []
    schedule.daily_amount = Decimal('6.00')
    db.session.commit()
    with pytest.raises(ValueError, match='changed after posting'):
        generate_accruals(date(2025, 1, 6))


def test_income_accrual_uses_receivable_and_rejects_wrong_categories(clean_ledger):
    schedule = Accruals(name='Earned income', accrual_type='income',
                        start_date=date(2025, 2, 1), end_date=date(2025, 2, 2),
                        business_days=False, daily_amount=Decimal('3.00'),
                        income_statement_subaccount='Salary',
                        balance_sheet_subaccount='Accounts Receivable')
    db.session.add(schedule)
    db.session.commit()
    assert len(generate_accruals(date(2025, 2, 2))) == 2
    db.session.commit()
    post_pair(timestamp=at(2025, 2, 3), debit='Chase Checking',
              credit='Accounts Receivable', functional_amount='6.00')
    db.session.commit()
    report = statement(db.session, 'YYYY-MM', '2025-02')
    assert report['current_income'] == Decimal('6')
    assert report['cash'] == Decimal('6')
    assert report['balances']['Accounts Receivable'] == 0
    schedule.balance_sheet_subaccount = 'Accounts Payable'
    db.session.commit()
    with pytest.raises(ValueError, match='incompatible account categories'):
        generate_accruals(date(2025, 2, 2))


def test_local_accounting_commands_post_split_check_close_and_reverse(
        clean_ledger, app, tmp_path):
    import manage

    for name in ('post_journal', 'post_split', 'accounting_check',
                 'close_month', 'reverse_journal'):
        app.cli.add_command(manage.app.cli.commands[name])
    runner = app.test_cli_runner()
    posted = runner.invoke(args=[
        'post_journal', '--date', '2025-01-01', '--reference', 'opening-cli',
        '--debit', 'Chase Checking', '--credit', 'Personal Equity',
        '--amount', '100.00'])
    assert posted.exit_code == 0, posted.output
    opening_id = db.session.query(JournalEntries).filter_by(
        transaction_source='manual', transaction_id='opening-cli').one().id
    allocation = tmp_path / 'split.json'
    allocation.write_text(json.dumps({
        'date': '2025-01-02', 'source': 'manual',
        'transaction_id': 'receipt-cli', 'fixed_account': 'Chase Checking',
        'total': '10.00', 'allocations': [
            {'key': 'rent', 'account': 'Rent', 'amount': '7.00'},
            {'key': 'food', 'account': 'Groceries', 'amount': '3.00'}]}))
    split = runner.invoke(args=['post_split', str(allocation)])
    assert split.exit_code == 0, split.output
    checked = runner.invoke(args=['accounting_check'])
    assert checked.exit_code == 0, checked.output
    assert '"passed": true' in checked.output
    closed = runner.invoke(args=['close_month', '--period', '2025-01'])
    assert closed.exit_code == 0, closed.output
    reversed_result = runner.invoke(args=[
        'reverse_journal', '--id', str(opening_id), '--date', '2025-02-01',
        '--reason', 'Incorrect opening balance'])
    assert reversed_result.exit_code == 0, reversed_result.output
    assert runner.invoke(args=['accounting_check']).exit_code == 0
