"""Parse Schwab website exports into canonical rows for brokerage_import. Pure."""
import csv
from datetime import datetime, time
import io
import json
import re
from zoneinfo import ZoneInfo

from pacioli.functions.schwab_normalization import decimal_value, iso_or_us_date, posting_dates


EASTERN = ZoneInfo('America/New_York')
TRANSACTION_FIELDS = ('Date', 'Action', 'Symbol', 'Description', 'Quantity', 'Price', 'Fees & Comm', 'Amount')
POSITIONS_TITLE = re.compile(r'Positions for account (?P<name>.*?)\s*(?:\.{3}|…)(?P<digits>\d{3,4})\s+as of\s+'
                             r'(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)\s*ET,\s*(?P<date>\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})',
                             re.I)
POSITION_COLUMNS = {'symbol': 'Symbol', 'description': 'Description', 'quantity': 'Qty (Quantity)',
                    'price': 'Price', 'market_value': 'Mkt Val (Market Value)', 'cost_basis': 'Cost Basis',
                    'security_type': 'Asset Type'}


def _blank(value):
    return value is None or str(value).strip() in ('', '--', 'N/A')


def parse_transactions_json(raw):
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError):
        raise ValueError('Transactions export is not valid JSON')
    if not isinstance(data, dict) or not isinstance(data.get('BrokerageTransactions'), list):
        raise ValueError('Unrecognized transactions export layout')
    rows = []
    for item in data['BrokerageTransactions']:
        if not isinstance(item, dict) or any(field not in item for field in TRANSACTION_FIELDS):
            raise ValueError('Transaction row is missing a field')
        posted_on, as_of = posting_dates(item['Date'])
        rows.append(dict(
            posted_on=posted_on, as_of=as_of, action=' '.join(str(item['Action']).split()),
            symbol=None if _blank(item['Symbol']) else str(item['Symbol']).strip(),
            description=' '.join(str(item['Description'] or '').split())[:500],
            quantity=None if _blank(item['Quantity']) else decimal_value(item['Quantity']),
            price=None if _blank(item['Price']) else decimal_value(item['Price']),
            fees=None if _blank(item['Fees & Comm']) else decimal_value(item['Fees & Comm'], places=2),
            amount=None if _blank(item['Amount']) else decimal_value(item['Amount'], places=2)))
    total = data.get('TotalTransactionsAmount')
    return dict(format='json', window_start=iso_or_us_date(data['FromDate']), window_end=iso_or_us_date(data['ToDate']),
                rows=rows, total=None if _blank(total) else decimal_value(total, places=2))


def parse_positions_csv(raw, expected_digits=None):
    text = raw.decode('utf-8-sig')
    lines = text.splitlines()
    title = POSITIONS_TITLE.search(lines[0] if lines else '')
    if not title:
        raise ValueError('Unrecognized positions export title')
    if expected_digits and title.group('digits') != expected_digits:
        raise ValueError('Positions export is for a different account')
    hour = int(title.group('hour')) % 12 + (12 if title.group('ampm').upper() == 'PM' else 0)
    written = title.group('date')
    market_date = datetime.strptime(written, '%Y/%m/%d' if len(written.split('/')[0]) == 4 else '%m/%d/%Y').date()
    as_of = datetime.combine(market_date, time(hour, int(title.group('minute'))), EASTERN)
    header = next((i for i, line in enumerate(lines[1:6], 1) if line.startswith('"Symbol"') or line.startswith('Symbol')), None)
    if header is None:
        raise ValueError('Positions export header not found')
    reader = csv.DictReader(io.StringIO('\n'.join(lines[header:])))
    # Some account types (e.g. managed accounts) export no cost basis; it is then unknown.
    if any(column not in (reader.fieldnames or []) for key, column in POSITION_COLUMNS.items() if key != 'cost_basis'):
        raise ValueError('Unrecognized positions columns')
    parsed, total = [], None
    for row in reader:
        symbol = (row['Symbol'] or '').strip()
        if symbol.lower() in ('account total', 'positions total'):
            total = decimal_value(row['Mkt Val (Market Value)'], places=2)
            continue
        is_cash = symbol.lower().startswith('cash & cash investments')
        values = {key: row.get(column) for key, column in POSITION_COLUMNS.items()}
        parsed.append(dict(
            symbol=None if is_cash else symbol, description='' if is_cash else ' '.join((values['description'] or '').split())[:200],
            security_type=None if _blank(values['security_type']) else values['security_type'].strip(),
            quantity=None if _blank(values['quantity']) else decimal_value(values['quantity']),
            price=None if _blank(values['price']) else decimal_value(values['price']),
            market_value=None if _blank(values['market_value']) else decimal_value(values['market_value'], places=2),
            cost_basis=None if _blank(values['cost_basis']) else decimal_value(values['cost_basis'], places=2),
            is_cash=is_cash))
    return dict(format='csv', as_of=as_of, market_date=market_date,
                end_of_day=as_of.time() >= time(16, 0), total=total, lines=parsed,
                account_name=title.group('name').strip())


LOTS_TITLE = re.compile(r'Realized Gain/Loss - Lot Details for .*? from (\d{1,2}/\d{1,2}/\d{4}) to (\d{1,2}/\d{1,2}/\d{4})')
LOT_COLUMNS = ('Symbol', 'Closed Date', 'Opened Date', 'Quantity', 'Proceeds', 'Cost Basis (CB)', 'Term',
               'Wash Sale?', 'Disallowed Loss')
TERMS = {'long term': 'long', 'short term': 'short'}


def parse_lots_csv(raw):
    """Realized lot details. Schwab's cost basis and term are authoritative."""
    lines = raw.decode('utf-8-sig').splitlines()
    title = LOTS_TITLE.search(lines[0] if lines else '')
    if not title:
        raise ValueError('Unrecognized realized gain/loss export title')
    window = [datetime.strptime(value, '%m/%d/%Y').date() for value in title.groups()]
    if len(lines) <= 2:
        return dict(format='csv', window_start=window[0], window_end=window[1], rows=[])
    reader = csv.DictReader(io.StringIO('\n'.join(lines[1:])))
    if any(column not in (reader.fieldnames or []) for column in LOT_COLUMNS):
        raise ValueError('Unrecognized realized gain/loss columns')
    rows = []
    for row in reader:
        symbol = (row['Symbol'] or '').strip()
        if not symbol or symbol.lower().startswith('total'):
            continue
        term = TERMS.get(' '.join((row['Term'] or '').lower().split()))
        if term is None:
            raise ValueError('Unrecognized lot term')
        disallowed = None if _blank(row['Disallowed Loss']) else decimal_value(row['Disallowed Loss'], places=2)
        rows.append(dict(
            symbol=symbol, quantity=decimal_value(row['Quantity']),
            opened_on=None if _blank(row['Opened Date']) or not re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', row['Opened Date'].strip())
            else datetime.strptime(row['Opened Date'].strip(), '%m/%d/%Y').date(),
            closed_on=datetime.strptime(row['Closed Date'].strip(), '%m/%d/%Y').date(),
            proceeds=decimal_value(row['Proceeds'], places=2), cost_basis=decimal_value(row['Cost Basis (CB)'], places=2),
            disallowed_loss=abs(disallowed) if disallowed else 0, term=term))
    return dict(format='csv', window_start=window[0], window_end=window[1], rows=rows)
