"""Openings rolled back from a positions export for accounts older than their exports (synthetic)."""
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import brokerage_opening as bo
from pacioli.functions.accounting_report import audit_ledger
from pacioli.functions.brokerage_accounts import register_account
from pacioli.functions.brokerage_holdings import ledger_balance, quantities_as_of
from pacioli.functions.brokerage_plan import legs_for
from pacioli.functions.brokerage_posting import post, preview
from pacioli.functions.brokerage_valuation import adopt as adopt_valuation, month_end_valuation
from pacioli.functions.local_mapping_posting import blocking_batch_issues
from pacioli.functions.reconciliation import evidence


D = Decimal
WINDOW = date(2026, 1, 5)
OWNER = 'owner@example.com'


@pytest.fixture
def account(clean_ledger):
    account_id = register_account(db.session, provider='schwab', label='Old', masked_number='432',
                                  opened_on=WINDOW, reviewer=OWNER)
    exports = {}
    for kind in ('transactions', 'positions', 'realized_lots'):
        record = evidence('schwab_source', {'fixture': uuid4().hex}, OWNER)
        exports[kind] = db.session.execute(text('''INSERT INTO investments.brokerage_exports
            (account_id, kind, format, evidence_id, sha256, window_start, window_end, row_count, state, reviewer)
            VALUES (:account, :kind, 'json', :evidence, :sha, :start, :end, 0, 'active', :owner) RETURNING id'''),
            dict(account=account_id, kind=kind, evidence=record.id, sha=uuid4().hex * 2,
                 start=WINDOW if kind == 'transactions' else None,
                 end=date(2026, 3, 31) if kind == 'transactions' else None, owner=OWNER)).scalar()
    db.session.commit()
    row = db.session.execute(text('SELECT * FROM investments.brokerage_accounts WHERE id=:id'),
                             {'id': account_id}).first()
    return row, exports


def activity(account, day, action, amount=None, symbol=None, quantity=None, price=None):
    row, exports = account
    source = db.session.execute(text("INSERT INTO investments.brokerage_sources(account_id, kind) "
                                     "VALUES (:account, 'activity') RETURNING source_id, public_id"),
                                {'account': row.id}).first()
    db.session.execute(text('''INSERT INTO investments.brokerage_activity
        (source_id, account_id, posted_on, action, symbol, quantity, price, amount, economic_digest,
         day_ordinal, first_export_id)
        VALUES (:source, :account, :day, :action, :symbol, :quantity, :price, :amount, :digest, 1, :export)'''),
        dict(source=source.source_id, account=row.id, day=day, action=action, symbol=symbol, quantity=quantity,
             price=price, amount=amount, digest=sha256(uuid4().bytes).hexdigest(), export=exports['transactions']))
    return source


def lot(account, symbol, quantity, closed_on, proceeds, cost):
    row, exports = account
    db.session.execute(text('''INSERT INTO investments.realized_lots
        (account_id, export_id, symbol, quantity, closed_on, proceeds, cost_basis, term, lot_digest, lot_ordinal)
        VALUES (:account, :export, :symbol, :quantity, :closed, :proceeds, :cost, 'long', :digest, 1)'''),
        dict(account=row.id, export=exports['realized_lots'], symbol=symbol, quantity=quantity, closed=closed_on,
             proceeds=proceeds, cost=cost, digest=sha256(uuid4().bytes).hexdigest()))


def positions(account, cash, lines, end_of_day=True):
    row, exports = account
    snapshot = db.session.execute(text('''INSERT INTO investments.position_snapshots
        (account_id, export_id, as_of, market_date, end_of_day) VALUES (:account, :export, :at, :day, :eod)
        RETURNING id'''), dict(account=row.id, export=exports['positions'],
                               at=datetime(2026, 3, 31, 21, tzinfo=timezone.utc), day=date(2026, 3, 31),
                               eod=end_of_day)).scalar()
    db.session.execute(text('''INSERT INTO investments.position_lines(snapshot_id, symbol, quantity, market_value,
        cost_basis, is_cash) VALUES (:snapshot, NULL, NULL, :cash, NULL, true)'''), dict(snapshot=snapshot, cash=cash))
    for symbol, quantity, cost in lines:
        db.session.execute(text('''INSERT INTO investments.position_lines(snapshot_id, symbol, quantity, cost_basis)
            VALUES (:snapshot, :symbol, :quantity, :cost)'''),
            dict(snapshot=snapshot, symbol=symbol, quantity=quantity, cost=cost))


def held_before_window(account):
    """10 AAA (cost 1000) and $500 held before the window; then a buy, a sale and a dividend."""
    activity(account, date(2026, 1, 6), 'Buy', '-550.00', 'BBB', '5', '110.00')
    activity(account, date(2026, 2, 2), 'Sell', '600.00', 'AAA', '4', '150.00')
    lot(account, 'AAA', '4', date(2026, 2, 2), '600.00', '400.00')
    activity(account, date(2026, 2, 3), 'Qualified Dividend', '7.00', 'BBB')
    activity(account, date(2026, 2, 4), 'Buy', '-300.00', 'CCC', '3', '100.00')
    activity(account, date(2026, 2, 9), 'Sell', '330.00', 'CCC', '3', '110.00')
    lot(account, 'CCC', '3', date(2026, 2, 9), '330.00', '300.00')
    positions(account, '587.00', [('AAA', '6', '600.00'), ('BBB', '5', '550.00')])
    db.session.commit()


def test_opening_rolls_back_cash_shares_and_cost(account):
    held_before_window(account)
    opening = bo.derive(db.session, account[0].id)
    assert opening['status'] == 'ready' and opening['issues'] == []
    assert opening['as_of'] == '2026-01-04'
    assert (D(opening['cash']), D(opening['cost_basis'])) == (D('500.00'), D('1000.00'))
    assert opening['lines'] == [dict(symbol='AAA', quantity='10', cost_basis='1000.00')]


def test_adopted_opening_posts_against_equity_and_feeds_holdings(account):
    held_before_window(account)
    opening = bo.derive(db.session, account[0].id)
    bo.adopt(db.session, account[0].id, opening['digest'], OWNER, 'Synthetic opening')
    db.session.commit()
    with pytest.raises(ValueError, match='already'):
        bo.adopt(db.session, account[0].id, opening['digest'], OWNER, 'Again')
    db.session.rollback()
    summary, _ = preview(db.session)
    post(summary['digest'], OWNER, 'Post opening and activity')
    source = db.session.execute(text("SELECT source_id FROM investments.brokerage_sources WHERE kind='opening'")).scalar()
    end_of_opening = datetime(2026, 1, 5, tzinfo=timezone.utc).replace(microsecond=0)
    legs = {(leg.key, leg.debit, leg.credit, leg.amount) for leg in legs_for(db.session, source)}
    assert legs == {('opening:cash', 'Old Brokerage Cash', 'Personal Equity', D('500.00')),
                    ('opening:cost', 'Old Securities at Cost', 'Personal Equity', D('1000.00'))}
    assert all(leg.timestamp < end_of_opening for leg in legs_for(db.session, source))
    assert quantities_as_of(db.session, account[0].id, date(2026, 1, 4)) == {'AAA': D('10')}
    assert quantities_as_of(db.session, account[0].id, date(2026, 3, 31)) == {'AAA': D('6'), 'BBB': D('5')}
    # Ledger cash and cost now equal the positions export.
    assert ledger_balance(db.session, 'Old Brokerage Cash', date(2026, 3, 31)) == D('587.00')
    assert ledger_balance(db.session, 'Old Securities at Cost', date(2026, 3, 31)) == D('1150.00')
    assert not blocking_batch_issues(db.session, audit_ledger(db.session), set())
    # Later imports cannot slip activity in before the opening.
    early = activity(account, date(2026, 1, 3), 'Bank Interest', '1.00')
    db.session.commit()
    assert {row['public_id']: row['reason'] for row in preview(db.session)[1]}[early.public_id] == 'before_opening'


def test_gains_held_at_the_opening_are_equity_not_oci(account):
    held_before_window(account)
    opening = bo.derive(db.session, account[0].id)
    bo.adopt(db.session, account[0].id, opening['digest'], OWNER, 'Synthetic opening')
    db.session.commit()
    summary, _ = preview(db.session)
    post(summary['digest'], OWNER, 'Post opening and activity')
    closes = {'AAA': {date(2026, 1, 2): (D('120'), D('1')), date(2026, 1, 30): (D('125'), D('1'))},
              'BBB': {date(2026, 1, 30): (D('100'), D('1'))}}
    for day in (date(2026, 1, 4), date(2026, 1, 31)):
        adopt_valuation(db.session, account[0], month_end_valuation(db.session, account[0], day, closes), OWNER)
    db.session.commit()
    rows = {row['date']: row['legs'] for row in preview(db.session)[1] if row['kind'] == 'valuation'}
    # 10 x 120 - 1000 = 200 held at the opening; January adds (1250 + 500) - 1550 - 200 = 0.
    assert rows[date(2026, 1, 4)] == [['valuation', 'Old Market Value Adjustment', 'Personal Equity', '200.00',
                                        rows[date(2026, 1, 4)][0][4]]]
    assert rows[date(2026, 1, 31)] == []


@pytest.mark.parametrize('problem, issue', [
    ('no_lots', 'opening_cost_unknown:1'),
    ('oversold', 'negative_opening_quantity:1'),
    ('wrong_cost', 'cost_does_not_roll_back:1'),
    ('intraday', 'positions_not_end_of_day'),
])
def test_unverifiable_openings_are_blocked(account, problem, issue):
    activity(account, date(2026, 2, 2), 'Sell', '600.00', 'AAA', '4', '150.00')
    if problem != 'no_lots':
        lot(account, 'AAA', '4', date(2026, 2, 2), '600.00', '400.00')
    if problem == 'oversold':
        # Bought in the window, not held now, never sold: shares left without a record.
        activity(account, date(2026, 2, 3), 'Buy', '-150.00', 'DDD', '1', '150.00')
    if problem == 'wrong_cost':
        activity(account, date(2026, 2, 4), 'Buy', '-100.00', 'EEE', '1', '100.00')
        activity(account, date(2026, 2, 5), 'Sell', '100.00', 'EEE', '1', '100.00')
        lot(account, 'EEE', '1', date(2026, 2, 5), '100.00', '90.00')
    positions(account, '600.00', [('AAA', '6', '600.00')], end_of_day=problem != 'intraday')
    db.session.commit()
    opening = bo.derive(db.session, account[0].id)
    assert opening['status'] == 'blocked' and issue in opening['issues']
    with pytest.raises(ValueError, match='ready'):
        bo.adopt(db.session, account[0].id, opening['digest'], OWNER, 'Should fail')


def test_account_opened_inside_the_window_needs_no_opening(account):
    activity(account, date(2026, 1, 6), 'MoneyLink Transfer', '1000.00')
    activity(account, date(2026, 1, 7), 'Buy', '-400.00', 'AAA', '4', '100.00')
    positions(account, '600.00', [('AAA', '4', '400.00')])
    db.session.commit()
    opening = bo.derive(db.session, account[0].id)
    assert opening['status'] == 'not_needed' and opening['lines'] == []
    with pytest.raises(ValueError, match='ready'):
        bo.adopt(db.session, account[0].id, opening['digest'], OWNER, 'Nothing to open')
