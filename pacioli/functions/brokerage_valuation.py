"""Month-end valuation points: market value from closes, cost from the ledger.

A point freezes its prices, quantities and totals as evidence. Its target
T = market value - securities cost; the posting plan books T minus the previous
point's target between the market value adjustment and unrealized gain (OCI),
so the adjustment's balance at each point is exactly T.
"""
import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from pacioli.functions.brokerage_holdings import ledger_balance, quantities_as_of
from pacioli.functions.brokerage_posting import SOURCES, source_status
from pacioli.functions.market_prices import close_on_or_before, market_ticker
from pacioli.functions.reconciliation import evidence


CENT = Decimal('0.01')


def month_ends(opened_on, today):
    """Last calendar day of each complete month from the opening month."""
    result, year, month = [], opened_on.year, opened_on.month
    while True:
        last = date(year, month, calendar.monthrange(year, month)[1])
        if last >= today:
            return result
        result.append(last)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def blockers_through(session, account_id, day):
    """Sources dated on or before `day` that are not fully posted."""
    blocked = []
    for source in session.execute(SOURCES, {'account': account_id}):
        if source.source_date is None or source.source_date > day or source.kind == 'valuation':
            continue
        status, reason, _ = source_status(session, source)
        if status not in ('posted', 'quantity_only'):
            blocked.append(dict(source=source.public_id, status=status, reason=reason))
    return blocked


def month_end_valuation(session, account, day, closes):
    """Compute (without saving) a valuation, or raise ValueError naming what is missing."""
    blocked = blockers_through(session, account.id, day)
    if blocked:
        raise ValueError('{} sources on or before {} are not posted'.format(len(blocked), day))
    lines, total = [], Decimal('0')
    for symbol, quantity in sorted(quantities_as_of(session, account.id, day).items()):
        ticker = market_ticker(session, symbol)
        price_day, price = close_on_or_before(closes.get(ticker, {}), day)
        if price is None:
            raise ValueError('No market close for a held symbol on or before {}'.format(day))
        yahoo_close, factor = price
        traded_close = yahoo_close * factor
        value = quantity * traded_close
        total += value
        lines.append(dict(symbol=symbol, ticker=ticker, quantity=str(quantity), price_date=str(price_day),
                          provider_close=str(yahoo_close), split_factor=str(factor),
                          close=str(traded_close), value=str(value)))
    market_value = total.quantize(CENT, rounding=ROUND_HALF_UP)
    cost = ledger_balance(session, account.securities_subaccount, day)
    return dict(account_id=account.id, valuation_date=str(day), provider='yahoo', lines=lines,
                market_value=str(market_value), cost_basis=str(cost), target=str(market_value - cost))


def adopt(session, account, valuation, reviewer, kind='market_close'):
    """Save a computed valuation as a source; idempotent for the same date and figures."""
    day = date.fromisoformat(valuation['valuation_date'])
    existing = session.execute(text('''SELECT v.*, e.payload FROM investments.valuation_points v
        JOIN bookkeeping.accounting_evidence e ON e.id=v.evidence_id
        WHERE v.account_id=:account AND v.valuation_date=:day'''), dict(account=account.id, day=day)).first()
    if existing is not None:
        if (str(existing.market_value), str(existing.cost_basis)) != (valuation['market_value'], valuation['cost_basis']):
            raise ValueError('A different valuation already exists for {}'.format(day))
        return existing.source_id
    record = evidence('brokerage_valuation', valuation, reviewer)
    source_id = session.execute(text("INSERT INTO investments.brokerage_sources(account_id, kind) "
                                     "VALUES (:account, 'valuation') RETURNING source_id"),
                                {'account': account.id}).scalar()
    valued_at = datetime.combine(day + timedelta(days=1), time.min, timezone.utc) - timedelta(microseconds=1)
    session.execute(text('''INSERT INTO investments.valuation_points
        (source_id, account_id, kind, valuation_date, valued_at, market_value, cost_basis, target, evidence_id)
        VALUES (:source, :account, :kind, :day, :at, :market, :cost, :target, :evidence)'''),
        dict(source=source_id, account=account.id, kind=kind, day=day, at=valued_at,
             market=valuation['market_value'], cost=valuation['cost_basis'],
             target=valuation['target'], evidence=record.id))
    return source_id
