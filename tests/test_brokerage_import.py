"""Brokerage export intake: replay, overlap agreement, provisional days, lot revisions."""
from datetime import date
from decimal import Decimal
import json

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.brokerage_accounts import register_account
from pacioli.functions.brokerage_import import import_lots, import_positions, import_transactions


D = Decimal
TODAY = date(2026, 3, 15)


@pytest.fixture
def account(clean_ledger):
    account_id = register_account(db.session, provider='schwab', label='Imp', masked_number='246',
                                  opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    db.session.commit()
    return account_id


def row(day, action, amount, symbol=None, quantity=None, price=None, fees=None, description='FIXTURE'):
    return dict(posted_on=day, as_of=None, action=action, symbol=symbol, description=description,
                quantity=None if quantity is None else D(quantity), price=None if price is None else D(price),
                fees=None if fees is None else D(fees), amount=None if amount is None else D(amount))


def export(start, end, rows, total=None):
    raw = json.dumps({'start': str(start), 'end': str(end), 'rows': [str(r) for r in rows]}).encode()
    return raw, dict(format='json', window_start=start, window_end=end, rows=rows, total=total)


def activity_count(account):
    return db.session.execute(text('SELECT count(*) FROM investments.brokerage_activity WHERE account_id=:a'),
                              {'a': account}).scalar()


def test_replay_duplicates_and_overlap_agreement(account):
    jan = [row(date(2026, 1, 5), 'MoneyLink Transfer', '500.00'),
           row(date(2026, 1, 6), 'Buy', '-100.00', 'AAA', '1', '100'),
           row(date(2026, 1, 6), 'Buy', '-100.00', 'AAA', '1', '100')]
    raw, parsed = export(date(2026, 1, 1), date(2026, 1, 31), jan, total=D('300.00'))
    summary, created = import_transactions(db.session, account, raw, 'jan.json', parsed, 'owner', today=TODAY)
    assert created and summary['new_rows'] == 3 and summary['state'] == 'active'
    again, created = import_transactions(db.session, account, raw, 'renamed.json', parsed, 'owner', today=TODAY)
    assert not created and again['replay']
    feb = [row(date(2026, 2, 3), 'Qualified Dividend', '1.23', 'AAA')]
    raw2, parsed2 = export(date(2026, 1, 1), date(2026, 2, 28), jan + feb)
    summary2, _ = import_transactions(db.session, account, raw2, 'janfeb.json', parsed2, 'owner', today=TODAY)
    assert (summary2['state'], summary2['new_rows']) == ('active', 1)
    assert activity_count(account) == 4
    ordinals = db.session.execute(text('''SELECT day_ordinal FROM investments.brokerage_activity
        WHERE action='Buy' ORDER BY day_ordinal''')).scalars().all()
    assert ordinals == [1, 2]
    raw_payloads = db.session.execute(text("SELECT payload FROM bookkeeping.accounting_evidence "
                                           "WHERE kind='schwab_source'")).scalars().all()
    assert raw_payloads and all(value.startswith('fernet:') and 'MoneyLink' not in value for value in raw_payloads)


def test_disagreeing_overlap_activates_nothing(account):
    jan = [row(date(2026, 1, 5), 'MoneyLink Transfer', '500.00')]
    raw, parsed = export(date(2026, 1, 1), date(2026, 1, 31), jan)
    import_transactions(db.session, account, raw, 'jan.json', parsed, 'owner', today=TODAY)
    changed = [row(date(2026, 1, 5), 'MoneyLink Transfer', '450.00')]
    raw2, parsed2 = export(date(2026, 1, 1), date(2026, 1, 31), changed)
    summary, _ = import_transactions(db.session, account, raw2, 'jan2.json', parsed2, 'owner', today=TODAY)
    assert summary['state'] == 'disagreement' and summary['disagreeing_days'] == 1
    assert activity_count(account) == 1


def test_provisional_last_day_may_grow_but_not_shrink(account):
    first = [row(date(2026, 3, 14), 'Bank Interest', '0.10')]
    raw, parsed = export(date(2026, 3, 1), date(2026, 3, 14), first)
    summary, _ = import_transactions(db.session, account, raw, 'mar.json', parsed, 'owner', today=TODAY)
    grown = first + [row(date(2026, 3, 14), 'Qualified Dividend', '2.00', 'AAA')]
    raw2, parsed2 = export(date(2026, 3, 1), date(2026, 3, 14), grown)
    summary2, _ = import_transactions(db.session, account, raw2, 'mar2.json', parsed2, 'owner', today=TODAY)
    assert (summary2['state'], summary2['new_rows']) == ('active', 1)
    shrunk = [row(date(2026, 3, 14), 'Qualified Dividend', '2.00', 'AAA')]
    raw3, parsed3 = export(date(2026, 3, 1), date(2026, 3, 14), shrunk)
    assert import_transactions(db.session, account, raw3, 'mar3.json', parsed3, 'owner',
                               today=date(2026, 3, 20))[0]['state'] == 'disagreement'


@pytest.mark.parametrize('problem', ['outside', 'total'])
def test_invalid_exports_are_rejected_before_saving(account, problem):
    rows = [row(date(2026, 2, 5) if problem == 'outside' else date(2026, 1, 5), 'Bank Interest', '0.10')]
    raw, parsed = export(date(2026, 1, 1), date(2026, 1, 31), rows, total=D('9.99') if problem == 'total' else None)
    with pytest.raises(ValueError):
        import_transactions(db.session, account, raw, 'bad.json', parsed, 'owner', today=TODAY)
    db.session.rollback()
    assert db.session.execute(text('SELECT count(*) FROM investments.brokerage_exports')).scalar() == 0


def lot(symbol, quantity, closed, proceeds, cost, disallowed='0', opened=date(2025, 12, 1), term='short'):
    return dict(symbol=symbol, quantity=D(quantity), opened_on=opened, closed_on=closed,
                proceeds=D(proceeds), cost_basis=D(cost), disallowed_loss=D(disallowed), term=term)


def test_revised_lot_supersedes_prior_version(account):
    first = [lot('AAA', '10', date(2026, 2, 2), '800.00', '1000.00'),
             lot('BBB', '1', date(2026, 2, 3), '50.00', '40.00')]
    raw, _ = export(date(2026, 1, 1), date(2026, 2, 28), [])
    summary, _ = import_lots(db.session, account, raw + b'1', 'lots.json',
                             dict(format='json', window_start=date(2026, 1, 1), window_end=date(2026, 2, 28),
                                  rows=first), 'owner')
    assert summary['new_lots'] == 2
    revised = [lot('AAA', '10', date(2026, 2, 2), '800.00', '1000.00', disallowed='150.00'),
               lot('BBB', '1', date(2026, 2, 3), '50.00', '40.00')]
    summary2, _ = import_lots(db.session, account, raw + b'2', 'lots2.json',
                              dict(format='json', window_start=date(2026, 1, 1), window_end=date(2026, 2, 28),
                                   rows=revised), 'owner')
    assert (summary2['new_lots'], summary2['superseded_lots'], summary2['prior_lots_missing_from_export']) == (1, 1, 0)
    active = db.session.execute(text('''SELECT symbol, disallowed_loss FROM investments.realized_lots l
        WHERE NOT EXISTS (SELECT 1 FROM investments.realized_lots n WHERE n.supersedes_id=l.id)
        ORDER BY symbol''')).all()
    assert [(r.symbol, r.disallowed_loss) for r in active] == [('AAA', D('150.00')), ('BBB', D('0'))]


def test_positions_snapshot_is_stored(account):
    parsed = dict(format='json', as_of='2026-03-13T20:00:00+00:00', market_date=date(2026, 3, 13), end_of_day=True,
                  total=D('1100.00'), lines=[
                      dict(symbol='AAA', description='A', security_type='Equity', quantity=D('10'),
                           price=D('110'), market_value=D('1100.00'), cost_basis=D('1000.00'), is_cash=False),
                      dict(symbol=None, description='Cash', security_type='Cash', quantity=None, price=None,
                           market_value=D('50.00'), cost_basis=None, is_cash=True)])
    summary, created = import_positions(db.session, account, b'positions-fixture', 'pos.json', parsed, 'owner')
    assert created and summary['lines'] == 2


def test_wash_source_dated_at_replacement(account):
    from pacioli.functions.brokerage_import import derive_wash_sources
    rows = [row(date(2026, 2, 2), 'Sell', '800.00', 'AAA', '10', '80'),
            row(date(2026, 3, 3), 'Buy', '-850.00', 'AAA', '10', '85')]
    raw, parsed = export(date(2026, 2, 1), date(2026, 3, 10), rows)
    import_transactions(db.session, account, raw, 'feb.json', parsed, 'owner', today=TODAY)
    import_lots(db.session, account, b'lots', 'lots.json', dict(format='json', window_start=None, window_end=None,
                rows=[lot('AAA', '10', date(2026, 2, 2), '800.00', '1000.00', disallowed='150.00'),
                      lot('BBB', '1', date(2026, 1, 3), '50.00', '90.00', disallowed='40.00')]), 'owner')
    assert derive_wash_sources(db.session, account, today=TODAY) == {'created': 1, 'wash_without_replacement': 1}
    wash = db.session.execute(text('SELECT trigger_on, amount FROM investments.wash_adjustments')).one()
    assert (wash.trigger_on, wash.amount) == (date(2026, 3, 3), D('150.00'))
    assert derive_wash_sources(db.session, account, today=TODAY) == {'wash_without_replacement': 1}
