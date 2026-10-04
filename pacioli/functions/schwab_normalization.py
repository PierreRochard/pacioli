"""Pure Schwab export parsing helpers. No I/O; unknown input is never guessed.

Action labels map to canonical kinds that the posting plan understands. Labels
not listed here become 'unsupported' and stay visible for review.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re


ACTION_KINDS = {
    'moneylink transfer': 'transfer', 'moneylink deposit': 'transfer',
    'funds received': 'transfer', 'wire funds': 'transfer', 'wire funds received': 'transfer',
    'wire received': 'transfer', 'journal': 'transfer', 'internal transfer': 'transfer',
    # Cash leaving the account: pending in Transfer Clearing until matched or reviewed (owner decision).
    'funds paid': 'transfer', 'wire sent': 'transfer', 'returned check': 'transfer',
    'misc cash entry': 'transfer',
    'buy': 'buy', 'reinvest shares': 'buy',
    'sell': 'sell',
    'qualified dividend': 'qualified_dividend', 'qual div reinvest': 'qualified_dividend',
    'cash dividend': 'ordinary_dividend', 'non-qualified div': 'ordinary_dividend',
    'reinvest dividend': 'ordinary_dividend', 'special dividend': 'ordinary_dividend',
    'pr yr cash div': 'ordinary_dividend', 'pr yr div reinvest': 'ordinary_dividend',
    'long term cap gain': 'capital_gain_distribution',
    'short term cap gain': 'capital_gain_distribution',
    'long term cap gain reinvest': 'capital_gain_distribution',
    'short term cap gain reinvest': 'capital_gain_distribution',
    'bank interest': 'interest', 'credit interest': 'interest', 'bond interest': 'interest',
    'adr mgmt fee': 'fee', 'service fee': 'fee', 'wire fee': 'fee', 'auto s1 fee': 'fee',
    'foreign tax paid': 'foreign_tax',
    'stock split': 'split',
}

_MONEY = re.compile(r'(-)?\(?\$?(-)?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\)?')
_DATE = re.compile(r'(\d{2})/(\d{2})/(\d{4})(?:\s+as\s+of\s+(\d{2})/(\d{2})/(\d{4}))?')


def action_kind(action):
    return ACTION_KINDS.get(' '.join((action or '').lower().split()), 'unsupported')


def decimal_value(value, *, places=None):
    """Exact decimal from '$1,234.56', '-$5.00', '($5.00)', '1.5'; blank -> None."""
    if value is None:
        return None
    if isinstance(value, (int, Decimal)):
        result = Decimal(value)
    else:
        raw = str(value).strip()
        if raw in ('', '--', 'N/A'):
            return None
        match = _MONEY.fullmatch(raw.replace(' ', ''))
        if not match or raw.count('(') != raw.count(')'):
            raise ValueError('Unrecognized number format')
        negative = bool(match.group(1) or match.group(2)) or (raw.startswith('(') and raw.endswith(')'))
        try:
            result = Decimal(match.group(3).replace(',', ''))
        except InvalidOperation:
            raise ValueError('Unrecognized number format')
        if negative:
            result = -result
    if not result.is_finite():
        raise ValueError('Non-finite number')
    if places is not None and result != round(result, places):
        raise ValueError('Amount has more than {} decimal places'.format(places))
    return result


def posting_dates(value):
    """'MM/DD/YYYY' or 'MM/DD/YYYY as of MM/DD/YYYY' -> (posted_on, as_of or None)."""
    match = _DATE.fullmatch(' '.join(str(value or '').split()))
    if not match:
        raise ValueError('Transaction has no definite date')
    month, day, year = (int(match.group(i)) for i in (1, 2, 3))
    posted = date(year, month, day)
    as_of = None
    if match.group(4):
        as_of = date(int(match.group(6)), int(match.group(4)), int(match.group(5)))
    return posted, as_of


def iso_or_us_date(value):
    raw = str(value or '').strip()
    for pattern in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    raise ValueError('Unrecognized date format')
