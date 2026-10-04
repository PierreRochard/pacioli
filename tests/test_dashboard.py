"""The home dashboard must agree with the statements it summarizes."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import dashboard as dashboard_module
from pacioli.functions.accounting_report import statement
from pacioli.functions.dashboard import dashboard, month_keys, spending_breakdown
from pacioli.models import JournalEntries
from pacioli.views.dashboard_views import compact, dollars, flow_chart, net_worth_chart, scale


NOW = datetime(2026, 3, 20, 15, 0, tzinfo=timezone.utc)


def post(when, debit, credit, amount):
    amount = Decimal(amount)
    db.session.add(JournalEntries(timestamp=when, debit_subaccount=debit, credit_subaccount=credit,
                                  functional_amount=amount, functional_currency='USD',
                                  source_amount=amount, source_currency='USD'))


def utc(year, month, day):
    return datetime(year, month, day, 12, tzinfo=timezone.utc)


@pytest.fixture
def synthetic_ledger(clean_ledger):
    post(utc(2025, 1, 2), 'Chase Checking', 'Personal Equity', '5000.00')
    post(utc(2026, 1, 31), 'Chase Checking', 'Salary', '4000.00')
    post(utc(2026, 2, 3), 'Rent', 'Chase Checking', '1800.00')
    post(utc(2026, 2, 28), 'Chase Checking', 'Salary', '4000.00')
    post(utc(2026, 3, 1), 'Rent', 'Chase Checking', '1800.00')
    post(utc(2026, 3, 4), 'Groceries', 'Credit Card', '120.55')
    post(utc(2026, 3, 5), 'Restaurants', 'Credit Card', '64.20')
    post(utc(2026, 3, 6), 'Credit Card', 'Groceries', '20.55')   # refund
    post(utc(2026, 3, 7), 'Bookkeeping Suspense', 'Credit Card', '33.00')
    post(utc(2026, 4, 2), 'Rent', 'Chase Checking', '1800.00')    # future-dated
    db.session.commit()


def test_month_keys_cross_year():
    assert month_keys('2026-02', 4) == ['2025-11', '2025-12', '2026-01', '2026-02']


def test_dashboard_agrees_with_statements(synthetic_ledger):
    report = dashboard(db.session, now=NOW)
    assert report['period'] == '2026-03'
    assert [row['period'] for row in report['series']] == month_keys('2026-03', 12)
    for row in report['series'][-3:]:
        reference = statement(db.session, 'YYYY-MM', row['period'])
        assert row['assets'] == reference['assets']
        assert row['liabilities'] == reference['liabilities']
        assert row['cash'] == reference['cash']
        assert row['net_income'] == reference['current_income']
        assert row['net_worth'] == reference['assets'] - reference['liabilities']
    month = report['month']
    assert month['income'] == Decimal('0')
    assert month['spending'] == Decimal('1964.20')
    assert report['net_worth_change'] == Decimal('-1964.20')
    assert report['spending'] == [{'name': 'Rent Expense', 'amount': Decimal('1800.00')},
                                  {'name': 'Groceries Expense', 'amount': Decimal('100.00')},
                                  {'name': 'Restaurant Expense', 'amount': Decimal('64.20')}]
    assert report['month_suspense'] == {'entries': 1, 'amount': Decimal('33.00')}
    assert report['queue']['uncategorized'] == 1
    assert report['queue']['unmatched_transfers'] == 0
    # January 2025 through February 2026 are complete and unclosed.
    assert report['queue']['first_open'] == '2025-01'
    assert report['queue']['open_months'] == 14
    assert report['unknown_accounts'] == []


def test_feed_accounts_report_ledger_balance_and_activity(synthetic_ledger):
    name = 'Checking ' + uuid4().hex[:8]
    db.session.execute(text("INSERT INTO bookkeeping.subaccounts(name, parent) VALUES(:name, 'Cash')"),
                       {'name': name})
    account_id = db.session.execute(text("INSERT INTO ofx.acctfrom(subclass, name) "
                                         "VALUES('bankacctfrom', :name) RETURNING id"),
                                    {'name': name}).scalar()
    for day in (2, 9):
        db.session.execute(text('''INSERT INTO ofx.stmttrn(fitid, acctfrom_id, trntype, dtposted, trnamt, name)
            VALUES(:fitid, :aid, 'CREDIT', :when, 10.00, 'FIXTURE')'''),
            dict(fitid=uuid4().hex, aid=account_id, when=utc(2026, 3, day)))
    post(utc(2026, 3, 9), name, 'Salary', '20.00')
    db.session.commit()
    try:
        rows = [row for row in dashboard(db.session, now=NOW)['accounts'] if row['id'] == account_id]
        assert len(rows) == 1
        assert rows[0]['kind'] == 'Cash'
        assert rows[0]['ledger_balance'] == Decimal('20.00')
        assert rows[0]['transactions'] == 2
        assert rows[0]['newest'].date().isoformat() == '2026-03-09'
        assert rows[0]['bank_balance'] is None
    finally:
        db.session.rollback()
        db.session.execute(text('DELETE FROM bookkeeping.journal_entries WHERE debit_subaccount=:n'), {'n': name})
        db.session.execute(text('DELETE FROM ofx.stmttrn WHERE acctfrom_id=:id'), {'id': account_id})
        db.session.execute(text('DELETE FROM ofx.acctfrom WHERE id=:id'), {'id': account_id})
        db.session.execute(text('DELETE FROM bookkeeping.subaccounts WHERE name=:n'), {'n': name})
        db.session.commit()


def test_spending_breakdown_groups_the_tail():
    spending = {'A': Decimal(7), 'B': Decimal(6), 'C': Decimal(5), 'D': Decimal(4),
                'E': Decimal(3), 'F': Decimal(2), 'G': Decimal(1), 'H': Decimal('0.5'),
                'Refunded': Decimal(-9)}
    rows = spending_breakdown(spending)
    assert [row['name'] for row in rows] == ['A', 'B', 'C', 'D', 'E', 'F', 'Everything else']
    assert rows[-1]['amount'] == Decimal('1.5')


def test_formatting_and_chart_geometry():
    assert dollars(Decimal('-1234.56')) == '−$1,235'
    assert dollars(None) == '—'
    assert compact(Decimal('125000')) == '$125k'
    assert compact(Decimal('-2500000')) == '−$2.5M'
    y, ticks = scale([Decimal('1000'), Decimal('9000')])
    assert ticks[0]['y'] > ticks[-1]['y']
    assert ticks[0]['y'] >= y(1000) and ticks[-1]['y'] <= y(9000)
    series = [dict(period='2026-0{}'.format(month), net_worth=Decimal(month * 100),
                   income=Decimal(500), spending=Decimal(month * 50)) for month in range(1, 4)]
    chart = net_worth_chart(series)
    assert [point['label'] for point in chart['points']] == ['Jan', 'Feb', 'Mar']
    assert chart['points'][0]['y'] > chart['points'][-1]['y']
    bars = flow_chart(series)
    assert all(bar['height'] >= 0 for group in bars['groups'] for bar in group['bars'])


def test_home_requires_sign_in_for_figures(client, synthetic_ledger):
    client.get('/logout/')
    response = client.get('/')
    assert response.status_code == 200
    assert b'Sign in' in response.data
    assert b'Net worth' not in response.data


def test_home_dashboard_for_administrator(client, admin_user, synthetic_ledger):
    client.post('/login/', data=admin_user)
    response = client.get('/')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    page = response.data.decode()
    assert 'Net worth' in page and '<svg' in page
    assert 'Transactions awaiting a category' in page
    assert 'id="chase-attention-banner"' in page
    # The page must not depend on third-party assets.
    assert 'cdn' not in page.lower()


def test_home_dashboard_failure_is_contained(client, admin_user, clean_ledger, monkeypatch):
    def broken(session, now=None):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(dashboard_module, 'dashboard', broken)
    client.post('/login/', data=admin_user)
    response = client.get('/')
    assert response.status_code == 200
    assert b'could not be calculated' in response.data
