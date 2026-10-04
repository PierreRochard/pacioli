"""Month-end closing prices from Yahoo Finance, requested through a fixed decoy universe.

Owner-approved disclosure (AGENTS.md, "Approved disclosures"): only ticker symbols
and a date range leave the machine. Every request asks for the same universe, a
private decoy list plus every symbol ever held (append-only), so a request never
reveals current holdings. Only held tickers' prices are kept. Runs solely on an
explicit owner command; never on a schedule or on page load.

The decoy list lives in the untracked .local/ directory. Publishing it would let
anyone holding the request subtract it and recover the holdings.
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text


DECOY_FILE = Path(__file__).resolve().parents[2] / '.local' / 'decoy_tickers.txt'
MIN_DECOYS = 200
REQUEST_TIMEOUT = 60


def decoy_tickers():
    """Fail closed: without enough decoys, a request would reveal the holdings."""
    try:
        lines = DECOY_FILE.read_text().splitlines()
    except FileNotFoundError:
        raise RuntimeError('Decoy ticker list not found at {}; refusing to request '
                           'prices'.format(DECOY_FILE)) from None
    words = []
    for line in lines:
        if not line.startswith('#'):
            words.extend(line.split())
    if len(set(words)) < MIN_DECOYS:
        raise RuntimeError('Decoy ticker list has {} distinct tickers, fewer than {}; '
                           'refusing to request prices'.format(len(set(words)), MIN_DECOYS))
    return words


def market_ticker(session, symbol):
    mapped = session.execute(text('SELECT market_ticker FROM investments.symbol_map '
                                  'WHERE provider_symbol=:symbol'), {'symbol': symbol}).scalar()
    return mapped or symbol.upper().replace('/', '-').replace('.', '-')


def record_held(session, symbols):
    """Add held tickers to the permanent request universe (never removed)."""
    for symbol in symbols:
        session.execute(text('INSERT INTO investments.price_request_universe(ticker) VALUES (:ticker) '
                             'ON CONFLICT DO NOTHING'), {'ticker': market_ticker(session, symbol)})


def held_tickers(session):
    return sorted(row[0] for row in session.execute(text('SELECT ticker FROM investments.price_request_universe')))


def request_universe(session):
    return sorted(set(decoy_tickers()) | set(held_tickers(session)))


def _default_downloader(**kwargs):
    import yfinance
    return yfinance.download(**kwargs)


def fetch_closes(session, start, today=None, downloader=None):
    """Return {ticker: {day: (yahoo_close, split_factor_after_day)}} for held tickers.

    Yahoo's Close is adjusted for every later split, so the request always runs
    through today and returns the product of split ratios after each day; the
    traded close on that day is yahoo_close * factor.
    """
    today = today or date.today()
    universe = request_universe(session)
    held = set(held_tickers(session))
    frame = (downloader or _default_downloader)(
        tickers=universe, start=start.isoformat(), end=(today + timedelta(days=1)).isoformat(),
        interval='1d', auto_adjust=False, actions=True, group_by='column',
        progress=False, threads=True, timeout=REQUEST_TIMEOUT)
    result = {}
    for ticker in sorted(held):
        try:
            closes = frame['Close'][ticker].dropna()
            splits = frame['Stock Splits'][ticker].fillna(0)
        except KeyError:
            continue
        events = [(stamp.date(), Decimal(str(ratio))) for stamp, ratio in splits.items() if ratio and ratio > 0]
        series = {}
        for stamp, close in closes.items():
            day = stamp.date()
            factor = Decimal('1')
            for split_day, ratio in events:
                if split_day > day:
                    factor *= ratio
            # Provider floats carry binary noise; four places exceeds quoted precision.
            series[day] = (Decimal(str(close)).quantize(Decimal('0.0001')), factor)
        result[ticker] = series
    return result


def close_on_or_before(series, day, window=7):
    """Last available close within `window` days on or before `day`."""
    for offset in range(window + 1):
        candidate = day - timedelta(days=offset)
        if candidate in series:
            return candidate, series[candidate]
    return None, None
