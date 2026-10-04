"""Brokerage posting plan, posting service and audit on synthetic evidence."""
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.accounting_report import audit_ledger
from pacioli.functions.brokerage_accounts import register_account
from pacioli.functions.brokerage_plan import legs_for
from pacioli.functions.brokerage_posting import post, preview
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.local_mapping_posting import blocking_batch_issues
from pacioli.functions.reconciliation import evidence


D = Decimal


@pytest.fixture
def account(clean_ledger):
    account_id = register_account(db.session, provider='schwab', label='Test', masked_number='321',
                                  opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    row = evidence('schwab_source', {'fixture': uuid4().hex}, 'owner@example.com')
    export_id = db.session.execute(text('''INSERT INTO investments.brokerage_exports
        (account_id, kind, format, evidence_id, sha256, row_count, state, reviewer)
        VALUES (:account, 'transactions', 'json', :evidence, :sha, 0, 'active', 'owner@example.com')
        RETURNING id'''), dict(account=account_id, evidence=row.id, sha=uuid4().hex * 2)).scalar()
    db.session.commit()
    return account_id, export_id


def activity(account, day, action, amount=None, symbol=None, quantity=None, price=None, fees=None):
    account_id, export_id = account
    source = db.session.execute(text('''INSERT INTO investments.brokerage_sources(account_id, kind)
        VALUES (:account, 'activity') RETURNING source_id, public_id'''), {'account': account_id}).first()
    digest = sha256(uuid4().bytes).hexdigest()
    db.session.execute(text('''INSERT INTO investments.brokerage_activity
        (source_id, account_id, posted_on, action, symbol, quantity, price, fees, amount,
         economic_digest, day_ordinal, first_export_id)
        VALUES (:source, :account, :day, :action, :symbol, :quantity, :price, :fees, :amount,
                :digest, 1, :export)'''),
        dict(source=source.source_id, account=account_id, day=day, action=action, symbol=symbol,
             quantity=quantity, price=price, fees=fees, amount=amount, digest=digest, export=export_id))
    return source


def lot(account, symbol, quantity, closed_on, proceeds, cost, term='short', disallowed='0', opened_on=None):
    account_id, export_id = account
    return db.session.execute(text('''INSERT INTO investments.realized_lots
        (account_id, export_id, symbol, quantity, opened_on, closed_on, proceeds, cost_basis,
         disallowed_loss, term, lot_digest, lot_ordinal)
        VALUES (:account, :export, :symbol, :quantity, :opened, :closed, :proceeds, :cost,
                :disallowed, :term, :digest, 1) RETURNING id'''),
        dict(account=account_id, export=export_id, symbol=symbol, quantity=quantity,
             opened=opened_on, closed=closed_on, proceeds=proceeds, cost=cost, disallowed=disallowed,
             term=term, digest=sha256(uuid4().bytes).hexdigest())).scalar()


def plan(source):
    return [(leg.key, leg.debit, leg.credit, leg.amount) for leg in legs_for(db.session, source.source_id)]


def status_of(public_id):
    return {row['public_id']: row for row in preview(db.session)[1]}[public_id]


def test_cash_income_fee_and_buy_legs(account):
    deposit = activity(account, date(2026, 1, 5), 'MoneyLink Transfer', '5000.00')
    withdrawal = activity(account, date(2026, 1, 6), 'MoneyLink Transfer', '-200.00')
    buy = activity(account, date(2026, 1, 7), 'Buy', '-1000.95', 'AAA', '10', '100.00', '0.95')
    dividend = activity(account, date(2026, 2, 1), 'Qualified Dividend', '12.34', 'AAA')
    interest = activity(account, date(2026, 2, 1), 'Bank Interest', '0.56')
    fee = activity(account, date(2026, 2, 2), 'ADR Mgmt Fee', '-1.10', 'AAA')
    assert plan(deposit) == [('cash', 'Test Brokerage Cash', 'Transfer Clearing', D('5000.00'))]
    assert plan(withdrawal) == [('cash', 'Transfer Clearing', 'Test Brokerage Cash', D('200.00'))]
    assert plan(buy) == [('cost', 'Test Securities at Cost', 'Test Brokerage Cash', D('1000.95'))]
    assert plan(dividend) == [('income', 'Test Brokerage Cash', 'Qualified Dividends', D('12.34'))]
    assert plan(interest) == [('income', 'Test Brokerage Cash', 'Investment Interest', D('0.56'))]
    assert plan(fee) == [('expense', 'Investment Fees', 'Test Brokerage Cash', D('1.10'))]


def test_sell_uses_lots_across_terms(account):
    activity(account, date(2026, 1, 7), 'Buy', '-3000.00', 'AAA', '30', '100')
    sell = activity(account, date(2026, 3, 2), 'Sell', '2489.50', 'AAA', '25', '99.62', '1.00')
    lot(account, 'AAA', '10', date(2026, 3, 2), '995.80', '900.00', 'long')
    lot(account, 'AAA', '15', date(2026, 3, 2), '1493.70', '1600.00', 'short')
    legs = plan(sell)
    assert legs == [('basis', 'Test Brokerage Cash', 'Test Securities at Cost', D('2393.70')),
                    ('loss:short', 'Realized Loss - Short Term', 'Test Securities at Cost', D('106.30')),
                    ('gain:long', 'Test Brokerage Cash', 'Realized Gain - Long Term', D('95.80'))]
    cash = sum(amount for key, debit, _, amount in legs if debit == 'Test Brokerage Cash')
    relieved = sum(amount for key, _, credit, amount in legs if credit == 'Test Securities at Cost')
    assert cash == D('2489.50') and relieved == D('2500.00')


def test_sell_waits_for_lots_and_same_day_sells_partition(account):
    early = activity(account, date(2026, 3, 3), 'Sell', '500.00', 'BBB', '5', '100')
    assert status_of(early.public_id)['reason'] == 'awaiting_realized_lots'
    first = activity(account, date(2026, 3, 4), 'Sell', '300.00', 'CCC', '3', '100')
    second = activity(account, date(2026, 3, 4), 'Sell', '200.00', 'CCC', '2', '100')
    lot(account, 'CCC', '2', date(2026, 3, 4), '200.00', '150.00')
    lot(account, 'CCC', '3', date(2026, 3, 4), '300.00', '330.00')
    assert plan(first)[-1] == ('loss:short', 'Realized Loss - Short Term', 'Test Securities at Cost', D('30.00'))
    assert plan(second)[-1] == ('gain:short', 'Test Brokerage Cash', 'Realized Gain - Short Term', D('50.00'))
    twin_a = activity(account, date(2026, 3, 5), 'Sell', '100.00', 'DDD', '1', '100')
    activity(account, date(2026, 3, 5), 'Sell', '100.00', 'DDD', '1', '100')
    lot(account, 'DDD', '1', date(2026, 3, 5), '100.00', '90.00')
    lot(account, 'DDD', '1', date(2026, 3, 5), '100.00', '120.00')
    assert status_of(twin_a.public_id)['reason'] == 'ambiguous_lot_assignment'


def test_splits_unsupported_and_inconsistent_prices(account):
    split = activity(account, date(2026, 4, 1), 'Stock Split', None, 'AAA', '30')
    short = activity(account, date(2026, 4, 1), 'Sell Short', '100.00', 'AAA', '1', '100')
    wrong = activity(account, date(2026, 4, 2), 'Buy', '-150.00', 'AAA', '1', '100.00')
    assert status_of(split.public_id)['status'] == 'quantity_only'
    assert status_of(short.public_id)['status'] == 'unsupported'
    assert status_of(wrong.public_id)['reason'] == 'price_amount_inconsistent'


def test_wash_disallowance_posts_at_trigger_date(account):
    account_id, _ = account
    loss_lot = lot(account, 'AAA', '10', date(2026, 5, 29), '800.00', '1000.00', disallowed='150.00')
    source = db.session.execute(text('''INSERT INTO investments.brokerage_sources(account_id, kind)
        VALUES (:account, 'wash') RETURNING source_id, public_id'''), {'account': account_id}).first()
    db.session.execute(text('''INSERT INTO investments.wash_adjustments(source_id, lot_id, amount, trigger_on)
        VALUES (:source, :lot, 150.00, :day)'''), dict(source=source.source_id, lot=loss_lot, day=date(2026, 6, 3)))
    legs = legs_for(db.session, source.source_id)
    assert [(l.key, l.debit, l.credit, l.amount) for l in legs] == [
        ('wash', 'Test Securities at Cost', 'Wash Sale Disallowed Loss', D('150.00'))]
    assert legs[0].timestamp == datetime(2026, 6, 3, tzinfo=timezone.utc)


def test_valuation_points_post_deltas(account):
    account_id, _ = account
    ids = []
    for day, market, cost in ((date(2026, 1, 31), '1150.00', '1000.00'), (date(2026, 2, 28), '1080.00', '1000.00')):
        source = db.session.execute(text('''INSERT INTO investments.brokerage_sources(account_id, kind)
            VALUES (:account, 'valuation') RETURNING source_id, public_id'''), {'account': account_id}).first()
        row = evidence('brokerage_valuation', {'day': str(day)}, 'owner@example.com')
        db.session.execute(text('''INSERT INTO investments.valuation_points
            (source_id, account_id, kind, valuation_date, valued_at, market_value, cost_basis, target, evidence_id)
            VALUES (:source, :account, 'market_close', :day, :at, :market, :cost, :target, :evidence)'''),
            dict(source=source.source_id, account=account_id, day=day,
                 at=datetime(day.year, day.month, day.day, 23, 59, tzinfo=timezone.utc),
                 market=market, cost=cost, target=D(market) - D(cost), evidence=row.id))
        ids.append(source)
    assert plan(ids[0]) == [('valuation', 'Test Market Value Adjustment', 'Test Unrealized Gain (Loss)', D('150.00'))]
    assert plan(ids[1]) == [('valuation', 'Test Unrealized Gain (Loss)', 'Test Market Value Adjustment', D('70.00'))]


def test_post_is_atomic_idempotent_and_audited(account):
    activity(account, date(2026, 1, 5), 'MoneyLink Transfer', '5000.00')
    activity(account, date(2026, 1, 7), 'Buy', '-1000.00', 'AAA', '10', '100')
    pending = activity(account, date(2026, 3, 3), 'Sell', '500.00', 'BBB', '5', '100')
    db.session.commit()
    summary, _ = preview(db.session)
    assert summary['by_status'] == {'ready': 2, 'not_ready': 1}
    with pytest.raises(ValueError, match='changed since the preview'):
        post('0' * 64, 'owner@example.com', 'Test posting')
    result = post(summary['digest'], 'owner@example.com', 'Test posting')
    assert (result['posted_sources'], result['posted_legs']) == (2, 2)
    again_summary, _ = preview(db.session)
    assert again_summary['by_status'] == {'posted': 2, 'not_ready': 1}
    assert post(again_summary['digest'], 'owner@example.com', 'Again')['posted_sources'] == 0
    issues = [i for i in audit_ledger(db.session) if i['kind'].startswith('brokerage')]
    assert issues == [dict(kind='brokerage_source', scope='close', problem='awaiting_realized_lots',
                           source=pending.public_id)]
    assert blocking_batch_issues(db.session, issues, set()) == []


def test_tampered_or_unplanned_postings_are_caught(account):
    deposit = activity(account, date(2026, 1, 5), 'MoneyLink Transfer', '5000.00')
    db.session.commit()
    with pytest.raises(ValueError, match='differs from the planned'):
        post_pair(timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), debit='Test Brokerage Cash',
                  credit='Transfer Clearing', functional_amount='4000.00', transaction_source='schwab',
                  transaction_id=deposit.public_id, allocation_key='cash')
    db.session.rollback()
    summary, _ = preview(db.session)
    post(summary['digest'], 'owner@example.com', 'Test posting')
    db.session.execute(text("UPDATE bookkeeping.journal_entries SET functional_amount=4000, source_amount=4000 "
                            "WHERE transaction_id=:id"), {'id': deposit.public_id})
    db.session.commit()
    issues = [i for i in audit_ledger(db.session) if i['kind'].startswith('brokerage')]
    assert [(i['scope'], i['problem']) for i in issues] == [('ledger', 'posting_mismatch')]
    assert blocking_batch_issues(db.session, issues, set()) == issues


def test_rounded_lot_quantities_still_match_exact_proceeds(account):
    sell = activity(account, date(2026, 5, 4), 'Sell', '300.00', 'EEE', '3.333', '90.009')
    lot(account, 'EEE', '1.67', date(2026, 5, 4), '150.00', '140.00')
    lot(account, 'EEE', '1.66', date(2026, 5, 4), '150.00', '160.00')
    assert plan(sell)[0] == ('basis', 'Test Brokerage Cash', 'Test Securities at Cost', D('290.00'))
    other = activity(account, date(2026, 5, 5), 'Sell', '300.00', 'FFF', '3.4', '88.24')
    lot(account, 'FFF', '1.67', date(2026, 5, 5), '150.00', '140.00')
    lot(account, 'FFF', '1.66', date(2026, 5, 5), '150.00', '160.00')
    assert status_of(other.public_id)['reason'] == 'lots_do_not_match_sale'
