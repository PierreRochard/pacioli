"""Register a brokerage account and its four dedicated chart subaccounts."""
import re

from sqlalchemy import text

from pacioli.models import Accounts, Classifications, Elements, Subaccounts


OCI_ELEMENT = 'Other Comprehensive Income'
# role: (element, classification, account, cash source, subaccount pattern)
ROLES = {
    'cash': ('Assets', 'Long-term Investments', 'Brokerage Cash', 'Investing', '{} Brokerage Cash'),
    'securities': ('Assets', 'Long-term Investments', 'Brokerage Securities at Cost', 'Investing',
                   '{} Securities at Cost'),
    'valuation': ('Assets', 'Long-term Investments', 'Brokerage Market Value Adjustment', 'Investing',
                  '{} Market Value Adjustment'),
    'oci': (OCI_ELEMENT, 'Unrealized Investment Gains and Losses',
            'Unrealized Gain (Loss) on Investments', 'Investing', '{} Unrealized Gain (Loss)'),
}


def ensure_chart_path(session, element, classification, account, cash_source, subaccount):
    """Add missing levels; refuse to reinterpret an existing name under another parent."""
    if session.query(Elements).get(element) is None:
        session.add(Elements(name=element))
        session.flush()
    existing = session.query(Classifications).get(classification)
    if existing is None:
        session.add(Classifications(name=classification, parent=element))
        session.flush()
    elif existing.parent != element:
        raise ValueError('Chart classification {} already belongs to {}'.format(classification, existing.parent))
    existing = session.query(Accounts).get(account)
    if existing is None:
        session.add(Accounts(name=account, parent=classification, cash_source=cash_source, is_cash=False))
        session.flush()
    elif existing.parent != classification or existing.is_cash:
        raise ValueError('Chart account {} conflicts with the brokerage chart'.format(account))
    existing = session.query(Subaccounts).filter_by(name=subaccount).first()
    if existing is None:
        session.add(Subaccounts(name=subaccount, parent=account))
        session.flush()
    elif existing.parent != account:
        raise ValueError('Chart subaccount {} already belongs to {}'.format(subaccount, existing.parent))


# Shared income, gain and expense subaccounts. Retirement accounts book their
# results to Other Comprehensive Income: part of net worth, not spendable income.
SHARED = {
    'taxable': [('Revenues', 'Revenues', 'Investment Income', 'Investing', name) for name in
                ('Qualified Dividends', 'Ordinary Dividends', 'Investment Interest', 'Capital Gain Distributions')] +
               [('Gains', 'Gains', 'Investment Gain', 'Investing', 'Realized Gain - Short Term'),
                ('Gains', 'Gains', 'Investment Gain', 'Investing', 'Realized Gain - Long Term'),
                ('Losses', 'Losses', 'Investment Loss', 'Investing', 'Realized Loss - Short Term'),
                ('Losses', 'Losses', 'Investment Loss', 'Investing', 'Realized Loss - Long Term'),
                ('Losses', 'Losses', 'Investment Loss', 'Investing', 'Wash Sale Disallowed Loss')],
    'retirement': [(OCI_ELEMENT, 'Retirement Account Income', 'Retirement Account Income', 'Investing', name)
                   for name in ('Retirement Dividends', 'Retirement Interest', 'Retirement Capital Gain Distributions',
                                'Retirement Realized Gain (Loss)')],
    'expense': [('Expenses', 'Fixed Costs', 'Investment Expense', 'Investing', 'Investment Fees'),
                ('Expenses', 'Fixed Costs', 'Investment Expense', 'Investing', 'Foreign Taxes Paid')],
}
RETIREMENT_WORDS = re.compile(r'\b(ira|roth|sep|simple|401 ?k|403 ?b|contributory|rollover|inherited|beneficiary|'
                              r'retirement|pension|keogh)\b', re.I)


def tax_treatment_for(account_type):
    return 'retirement' if RETIREMENT_WORDS.search(account_type or '') else 'taxable'


def register_account(session, *, provider, label, masked_number, opened_on, reviewer, tax_treatment='taxable'):
    """Idempotent for identical input; the caller commits."""
    if tax_treatment not in ('taxable', 'retirement'):
        raise ValueError('Unsupported tax treatment')
    for path in SHARED[tax_treatment] + SHARED['expense']:
        ensure_chart_path(session, *path)
    if provider != 'schwab':
        raise ValueError('Unsupported brokerage provider')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9 ]{0,30}', label or ''):
        raise ValueError('A short alphanumeric account label is required')
    if not re.fullmatch(r'[0-9]{3,4}', masked_number or ''):
        raise ValueError('Record only the last 3 or 4 digits of the account number')
    if not reviewer:
        raise ValueError('A reviewer is required')
    names = {}
    for role, (element, classification, account, cash_source, pattern) in ROLES.items():
        names[role] = pattern.format(label)
        ensure_chart_path(session, element, classification, account, cash_source, names[role])
    row = session.execute(text('SELECT * FROM investments.brokerage_accounts WHERE label=:label'),
                          {'label': label}).first()
    if row is not None:
        same = (row.provider == provider and row.masked_number == masked_number
                and row.tax_treatment == tax_treatment
                and row.opened_on == opened_on and row.cash_subaccount == names['cash']
                and row.securities_subaccount == names['securities']
                and row.valuation_subaccount == names['valuation'] and row.oci_subaccount == names['oci'])
        if not same:
            raise ValueError('A different brokerage account already uses this label')
        return row.id
    return session.execute(text('''INSERT INTO investments.brokerage_accounts
        (provider, label, masked_number, opened_on, cash_subaccount, securities_subaccount,
         valuation_subaccount, oci_subaccount, reviewer, tax_treatment)
        VALUES (:provider, :label, :masked, :opened, :cash, :securities, :valuation, :oci, :reviewer, :tax)
        RETURNING id'''), dict(provider=provider, label=label, masked=masked_number, opened=opened_on,
                              reviewer=reviewer, tax=tax_treatment, **names)).scalar()


def accounts(session):
    return session.execute(text('SELECT * FROM investments.brokerage_accounts ORDER BY id')).all()


def registered_subaccounts(session):
    """Every subaccount reserved for brokerage sources."""
    names = set()
    for row in accounts(session):
        names.update((row.cash_subaccount, row.securities_subaccount,
                      row.valuation_subaccount, row.oci_subaccount))
    return names
