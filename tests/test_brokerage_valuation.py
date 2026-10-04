"""Decoy-universe price requests, split un-adjustment, and month-end valuations."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pacioli.extensions import db
from pacioli.functions import market_prices as mp
from pacioli.functions.brokerage_accounts import register_account
from pacioli.functions.brokerage_plan import legs_for
from pacioli.functions.brokerage_posting import post, preview
from pacioli.functions.brokerage_valuation import adopt, month_end_valuation, month_ends
from pacioli.functions.reconciliation import evidence


def frame(prices, splits=None):
    """yfinance-shaped frame: columns (field, ticker), daily index."""
    days = sorted({day for series in prices.values() for day in series})
    index = pd.to_datetime(days)
    columns, data = [], {}
    for field in ('Close', 'Stock Splits'):
        for ticker in prices:
            columns.append((field, ticker))
            if field == 'Close':
                data[(field, ticker)] = [prices[ticker].get(day) for day in days]
            else:
                data[(field, ticker)] = [(splits or {}).get(ticker, {}).get(day, 0.0) for day in days]
    return pd.DataFrame(data, index=index, columns=pd.MultiIndex.from_tuples(columns))


@pytest.fixture(autouse=True)
def decoys(tmp_path, monkeypatch):
    """Synthetic decoys; tests never read the private list."""
    path = tmp_path / 'decoy_tickers.txt'
    path.write_text('# synthetic\n' + ' '.join('DZ{:03d}'.format(i) for i in range(mp.MIN_DECOYS)) + '\n')
    monkeypatch.setattr(mp, 'DECOY_FILE', path)
    return path


@pytest.fixture
def account(clean_ledger):
    account_id = register_account(db.session, provider='schwab', label='Val', masked_number='555',
                                  opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    record = evidence('schwab_source', {'fixture': uuid4().hex}, 'owner@example.com')
    export_id = db.session.execute(text('''INSERT INTO investments.brokerage_exports
        (account_id, kind, format, evidence_id, sha256, row_count, state, reviewer)
        VALUES (:account, 'transactions', 'json', :evidence, :sha, 0, 'active', 'owner@example.com')
        RETURNING id'''), dict(account=account_id, evidence=record.id, sha=uuid4().hex * 2)).scalar()
    db.session.commit()
    yield db.session.execute(text('SELECT * FROM investments.brokerage_accounts WHERE id=:id'),
                             {'id': account_id}).first(), export_id
    db.session.remove()


def activity(account, day, action, amount=None, symbol=None, quantity=None, price=None):
    row, export_id = account
    source = db.session.execute(text("INSERT INTO investments.brokerage_sources(account_id, kind) "
                                     "VALUES (:account, 'activity') RETURNING source_id"), {'account': row.id}).scalar()
    db.session.execute(text('''INSERT INTO investments.brokerage_activity
        (source_id, account_id, posted_on, action, symbol, quantity, price, amount, economic_digest,
         day_ordinal, first_export_id)
        VALUES (:source, :account, :day, :action, :symbol, :quantity, :price, :amount, :digest, 1, :export)'''),
        dict(source=source, account=row.id, day=day, action=action, symbol=symbol, quantity=quantity,
             price=price, amount=amount, digest=sha256(uuid4().bytes).hexdigest(), export=export_id))


def test_request_universe_is_fixed_and_append_only(account):
    seen = []

    def downloader(**kwargs):
        seen.append(tuple(kwargs['tickers']))
        return frame({ticker: {date(2026, 1, 30): 10.0} for ticker in kwargs['tickers']})

    mp.record_held(db.session, ['ZZTEST', 'BRK/B'])
    db.session.commit()
    first = mp.fetch_closes(db.session, date(2026, 1, 1), today=date(2026, 2, 5), downloader=downloader)
    assert set(mp.decoy_tickers()) <= set(seen[0]) and {'ZZTEST', 'BRK-B'} <= set(seen[0])
    assert set(first) == {'BRK-B', 'ZZTEST'}
    # Selling a holding never shrinks the request: the universe cannot lose members.
    with pytest.raises(DBAPIError, match='append-only'):
        db.session.execute(text("DELETE FROM investments.price_request_universe WHERE ticker='ZZTEST'"))
    db.session.rollback()
    mp.fetch_closes(db.session, date(2026, 1, 1), today=date(2026, 2, 5), downloader=downloader)
    assert seen[0] == seen[1]
    db.session.execute(text('TRUNCATE investments.price_request_universe'))
    db.session.commit()


@pytest.mark.parametrize('content', [None, 'DZ001 DZ002\n'])
def test_fetch_refuses_without_enough_decoys(account, decoys, content):
    if content is None:
        decoys.unlink()
    else:
        decoys.write_text(content)
    mp.record_held(db.session, ['ZZTEST'])
    db.session.commit()
    requested = []
    with pytest.raises(RuntimeError, match='refusing to request prices'):
        mp.fetch_closes(db.session, date(2026, 1, 1), today=date(2026, 2, 5),
                        downloader=lambda **kwargs: requested.append(kwargs))
    assert requested == []
    db.session.execute(text('TRUNCATE investments.price_request_universe'))
    db.session.commit()


def test_fetch_returns_split_factors(account):
    mp.record_held(db.session, ['AAA'])
    db.session.commit()
    fake = frame({'AAA': {date(2026, 1, 30): 50.0, date(2026, 2, 10): 52.0}}, {'AAA': {date(2026, 2, 10): 2.0}})
    closes = mp.fetch_closes(db.session, date(2026, 1, 1), today=date(2026, 2, 20), downloader=lambda **kw: fake)
    assert closes['AAA'][date(2026, 1, 30)] == (Decimal('50.0000'), Decimal('2'))
    assert closes['AAA'][date(2026, 2, 10)] == (Decimal('52.0000'), Decimal('1'))
    db.session.execute(text('TRUNCATE investments.price_request_universe'))
    db.session.commit()


def test_month_end_valuation_posts_unrealized_gain(account):
    row, _ = account
    activity(account, date(2026, 1, 5), 'MoneyLink Transfer', '1000.00')
    activity(account, date(2026, 1, 6), 'Buy', '-1000.00', 'AAA', '10', '100')
    db.session.commit()
    closes = {'AAA': {date(2026, 1, 30): (Decimal('55.0000'), Decimal('2'))}}
    assert month_ends(date(2026, 1, 2), date(2026, 2, 15)) == [date(2026, 1, 31)]
    with pytest.raises(ValueError, match='not posted'):
        month_end_valuation(db.session, row, date(2026, 1, 31), closes)
    summary, _ = preview(db.session)
    post(summary['digest'], 'owner@example.com', 'Fixture posting')
    valuation = month_end_valuation(db.session, row, date(2026, 1, 31), closes)
    assert (valuation['market_value'], valuation['cost_basis'], valuation['target']) == ('1100.00', '1000.00', '100.00')
    source_id = adopt(db.session, row, valuation, 'owner@example.com')
    assert adopt(db.session, row, valuation, 'owner@example.com') == source_id
    db.session.commit()
    legs = legs_for(db.session, source_id)
    assert [(l.debit, l.credit, l.amount) for l in legs] == [
        ('Val Market Value Adjustment', 'Val Unrealized Gain (Loss)', Decimal('100.00'))]
    with pytest.raises(ValueError, match='No market close'):
        month_end_valuation(db.session, row, date(2026, 1, 31), {})
