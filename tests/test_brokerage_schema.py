"""Brokerage evidence tables, identities, guards, and unrealized gains in equity."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pacioli.extensions import db
from pacioli.functions.accounting_report import audit_ledger, statement
from pacioli.functions.brokerage_accounts import register_account, registered_subaccounts
from pacioli.functions.dashboard import monthly_summary
from pacioli.models import JournalEntries


def when(month, day):
    return datetime(2026, month, day, tzinfo=timezone.utc)


@pytest.fixture
def brokerage(clean_ledger):
    account_id = register_account(db.session, provider='schwab', label='Fixture', masked_number='123',
                                  opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    db.session.commit()
    yield account_id
    db.session.remove()
    db.session.execute(text("DELETE FROM investments.symbol_map"))
    db.session.commit()


def new_source(account_id, kind='activity'):
    return db.session.execute(text('''INSERT INTO investments.brokerage_sources(account_id, kind)
        VALUES (:account, :kind) RETURNING source_id, public_id'''),
        {'account': account_id, 'kind': kind}).first()


def post(public_id, debit, credit, amount, day, key='0', source='schwab'):
    amount = Decimal(amount)
    entry = JournalEntries(timestamp=day, debit_subaccount=debit, credit_subaccount=credit,
                           functional_amount=amount, functional_currency='USD', source_amount=amount,
                           source_currency='USD', transaction_source=source, transaction_id=public_id,
                           allocation_key=key)
    db.session.add(entry)
    db.session.flush()
    return entry


def test_registration_builds_chart_and_is_idempotent(brokerage):
    names = registered_subaccounts(db.session)
    assert names == {'Fixture Brokerage Cash', 'Fixture Securities at Cost',
                     'Fixture Market Value Adjustment', 'Fixture Unrealized Gain (Loss)'}
    again = register_account(db.session, provider='schwab', label='Fixture', masked_number='123',
                             opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    assert again == brokerage
    with pytest.raises(ValueError, match='different brokerage account'):
        register_account(db.session, provider='schwab', label='Fixture', masked_number='999',
                         opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    with pytest.raises(ValueError, match='last 3 or 4 digits'):
        register_account(db.session, provider='schwab', label='Other', masked_number='123456789',
                         opened_on=date(2026, 1, 2), reviewer='owner@example.com')


def test_public_ids_links_and_immutability(brokerage):
    source = new_source(brokerage)
    assert source.public_id == 'schwab:v1:{}'.format(source.source_id)
    entry = post(source.public_id, 'Fixture Brokerage Cash', 'Transfer Clearing', '100.00', when(1, 5))
    linked = db.session.execute(text('SELECT source_id FROM investments.brokerage_journal_sources '
                                     'WHERE journal_entry_id=:id'), {'id': entry.id}).scalar()
    assert linked == source.source_id
    db.session.commit()
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE investments.brokerage_sources SET kind='wash'"))
    db.session.rollback()
    with pytest.raises(DBAPIError, match='cannot be changed'):
        db.session.execute(text("UPDATE investments.brokerage_accounts SET masked_number='456'"))
    db.session.rollback()


def test_unknown_brokerage_reference_is_rejected(brokerage):
    with pytest.raises(DBAPIError, match='missing source'):
        post('schwab:v1:999999', 'Fixture Brokerage Cash', 'Transfer Clearing', '1.00', when(1, 5))
    db.session.rollback()


@pytest.mark.parametrize('source', ['manual', None])
def test_other_sources_cannot_touch_brokerage_subaccounts(brokerage, source):
    with pytest.raises(DBAPIError, match='only from brokerage sources'):
        post('x-1' if source else None, 'Fixture Brokerage Cash', 'Chase Checking', '1.00', when(1, 5),
             source=source)
    db.session.rollback()


def test_unrealized_gains_are_equity_not_income(brokerage):
    for kind, debit, credit, amount, day in (
            ('activity', 'Fixture Brokerage Cash', 'Transfer Clearing', '1000.00', when(1, 5)),
            ('activity', 'Fixture Securities at Cost', 'Fixture Brokerage Cash', '1000.00', when(1, 6)),
            ('valuation', 'Fixture Market Value Adjustment', 'Fixture Unrealized Gain (Loss)', '150.00', when(1, 31)),
            ('valuation', 'Fixture Unrealized Gain (Loss)', 'Fixture Market Value Adjustment', '40.00', when(2, 28))):
        post(new_source(brokerage, kind).public_id, debit, credit, amount, day)
    db.session.commit()
    january, february = statement(db.session, 'YYYY-MM', '2026-01'), statement(db.session, 'YYYY-MM', '2026-02')
    assert january['current_income'] == 0 and january['current_oci'] == Decimal('150.00')
    assert january['accumulated_oci'] == Decimal('150.00')
    assert february['current_oci'] == Decimal('-40.00') and february['accumulated_oci'] == Decimal('110.00')
    assert february['comprehensive_income'] == Decimal('-40.00')
    assert january['equation_difference'] == february['equation_difference'] == 0
    assert not [issue for issue in audit_ledger(db.session)
                if issue['kind'] in ('accounting_equation', 'unclassified_accounts')]
    series = monthly_summary(db.session, '2026-02', months=2)['series']
    assert [row['income'] for row in series] == [0, 0]
    # The deposit's other leg is Transfer Clearing (an asset): only the gain moves net worth.
    assert series[-1]['net_worth'] == Decimal('110.00')


def test_realized_losses_reduce_income_not_spending(brokerage):
    source = new_source(brokerage)
    post(source.public_id, 'Fixture Brokerage Cash', 'Transfer Clearing', '100.00', when(3, 1))
    post(new_source(brokerage).public_id, 'Realized Loss - Short Term', 'Fixture Brokerage Cash', '30.00', when(3, 2))
    post(new_source(brokerage).public_id, 'Fixture Brokerage Cash', 'Qualified Dividends', '10.00', when(3, 3))
    db.session.commit()
    march = monthly_summary(db.session, '2026-03', months=1)
    assert march['series'][-1]['income'] == Decimal('-20.00')
    assert march['series'][-1]['spending'] == 0
    assert march['spending'] == []
