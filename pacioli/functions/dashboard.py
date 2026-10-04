"""Home page figures: monthly journal movements plus read-only work queues."""

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, text

from pacioli.functions.accounting_report import ZERO, period_key
from pacioli.models import (AccountingReviews, Accounts, ChaseBrowserRuns,
                            Classifications, ClosedPeriods, Elements,
                            JournalEntries, Subaccounts)


# Realized investment losses reduce income rather than counting as spending.
# Other Comprehensive Income (unrealized gains) is in neither.
INFLOW_ELEMENTS = frozenset(('Revenues', 'Gains', 'Losses'))
OUTFLOW_ELEMENTS = frozenset(('Expenses',))
SPENDING_CATEGORIES = 6

# Debits are positive and credits negative, as in accounting_report.statement().
MONTHLY_MOVEMENTS = text('''
    SELECT to_char(timestamp AT TIME ZONE 'UTC', 'YYYY-MM') AS period,
           subaccount, sum(amount) AS amount, count(*) AS entries
    FROM (SELECT timestamp, debit_subaccount AS subaccount, functional_amount AS amount
          FROM bookkeeping.journal_entries
          UNION ALL
          SELECT timestamp, credit_subaccount, -functional_amount
          FROM bookkeeping.journal_entries) movements
    GROUP BY 1, 2''')

FEED_ACCOUNTS = text('''
    SELECT account.id, account.name, activity.transactions, activity.newest,
           latest.balance_amount, latest.balance_asof, latest.imported_at
    FROM ofx.acctfrom account
    JOIN (SELECT acctfrom_id, count(*) AS transactions, max(dtposted) AS newest
          FROM ofx.stmttrn GROUP BY acctfrom_id) activity
      ON activity.acctfrom_id = account.id
    LEFT JOIN LATERAL (
        SELECT balance_amount, balance_asof, imported_at
        FROM admin.ofx_file_imports
        WHERE ofx_account_id = account.id
        ORDER BY balance_asof DESC, id DESC LIMIT 1) latest ON true
    ORDER BY account.name''')


def month_index(period):
    return int(period[:4]) * 12 + int(period[5:]) - 1


def month_label(index):
    return '{}-{:02d}'.format(index // 12, index % 12 + 1)


def next_month(period):
    return month_label(month_index(period) + 1)


def month_keys(period, months):
    """The `months` YYYY-MM labels ending with `period`, oldest first."""
    last = month_index(period)
    return [month_label(index) for index in range(last - months + 1, last + 1)]


def chart(session):
    rows = (session.query(Subaccounts.name, Accounts.name, Elements.name, Accounts.is_cash)
            .join(Accounts, Subaccounts.parent == Accounts.name)
            .join(Classifications, Accounts.parent == Classifications.name)
            .join(Elements, Classifications.parent == Elements.name).all())
    return {name: (element, account, bool(is_cash))
            for name, account, element, is_cash in rows}


def monthly_summary(session, period, months=12, accounts=None):
    """Month-end position and monthly flows for the trailing `months` periods.

    Figures agree with statement(session, 'YYYY-MM', period) for each month.
    """
    accounts = chart(session) if accounts is None else accounts
    shown = month_keys(period, months)
    movements = defaultdict(list)
    unknown = set()
    for row in session.execute(MONTHLY_MOVEMENTS):
        if row.period > shown[-1]:
            continue
        movements[row.period].append((row.subaccount, row.amount, row.entries))
        if row.subaccount not in accounts:
            unknown.add(row.subaccount)
    balances = defaultdict(lambda: ZERO)
    for key in sorted(movements):
        if key >= shown[0]:
            break
        for name, amount, _ in movements[key]:
            balances[name] += amount
    series, spending = [], defaultdict(lambda: ZERO)
    for key in shown:
        inflow = outflow = ZERO
        for name, amount, _ in movements.get(key, ()):
            balances[name] += amount
            element, account, _ = accounts.get(name, (None, None, False))
            if element in INFLOW_ELEMENTS:
                inflow -= amount
            elif element in OUTFLOW_ELEMENTS:
                outflow += amount
                if key == shown[-1]:
                    spending[account] += amount
        totals = defaultdict(lambda: ZERO)
        cash = ZERO
        for name, balance in balances.items():
            element, _, is_cash = accounts.get(name, (None, None, False))
            totals[element] += balance
            if is_cash:
                cash += balance
        series.append({'period': key, 'assets': totals['Assets'],
                       'liabilities': -totals['Liabilities'],
                       'net_worth': totals['Assets'] + totals['Liabilities'],
                       'cash': cash, 'income': inflow, 'spending': outflow,
                       'net_income': inflow - outflow})
    current = movements.get(shown[-1], ())
    return {'series': series, 'balances': dict(balances),
            'spending': spending_breakdown(spending),
            'unknown_accounts': sorted(unknown),
            'month_suspense': {
                'entries': sum(entries for name, _, entries in current
                               if name == 'Bookkeeping Suspense'),
                'amount': sum((amount for name, amount, _ in current
                               if name == 'Bookkeeping Suspense'), ZERO)}}


def spending_breakdown(spending, limit=SPENDING_CATEGORIES):
    """Largest positive expense accounts; refunds larger than spend are omitted."""
    rows = sorted(((amount, name) for name, amount in spending.items() if amount > 0),
                  reverse=True)
    top = [{'name': name, 'amount': amount} for amount, name in rows[:limit]]
    rest = sum((amount for amount, _ in rows[limit:]), ZERO)
    if rest:
        top.append({'name': 'Everything else', 'amount': rest, 'other': True})
    return top


def feed_accounts(session, balances, accounts=None):
    accounts = chart(session) if accounts is None else accounts
    result = []
    for row in session.execute(FEED_ACCOUNTS):
        element, _, is_cash = accounts.get(row.name, (None, None, False))
        result.append({'id': row.id, 'name': row.name, 'element': element,
                       'kind': ('Card' if element == 'Liabilities' else
                                'Cash' if is_cash else element or 'Unclassified'),
                       'ledger_balance': balances.get(row.name, ZERO) if element else None,
                       'transactions': row.transactions, 'newest': row.newest,
                       'bank_balance': row.balance_amount,
                       'bank_balance_asof': row.balance_asof,
                       'imported_at': row.imported_at})
    return result


def work_queue(session, period):
    """Counts of reviewable work; each is a link, never an automatic action."""
    from pacioli.functions.reconciliation import resolved_transfer_ids
    resolved = resolved_transfer_ids(session)
    counts = {}
    for name in ('Bookkeeping Suspense', 'Transfer Clearing'):
        counts[name] = session.query(JournalEntries.id).filter(
            ~JournalEntries.id.in_(resolved),
            (JournalEntries.debit_subaccount == name) |
            (JournalEntries.credit_subaccount == name)).count()
    reviews = dict(session.query(AccountingReviews.state, func.count())
                   .group_by(AccountingReviews.state).all())
    latest_closed = (session.query(ClosedPeriods.period)
                     .order_by(ClosedPeriods.period.desc()).limit(1).scalar())
    first = (session.query(JournalEntries.timestamp)
             .order_by(JournalEntries.timestamp).limit(1).scalar())
    # Only completed months can be closed.
    first_open = (next_month(latest_closed) if latest_closed else
                  period_key(first, 'YYYY-MM') if first is not None else period)
    open_months = max(0, month_index(period) - month_index(first_open))
    return {'uncategorized': counts['Bookkeeping Suspense'],
            'unmatched_transfers': counts['Transfer Clearing'],
            'suggestions_ready': reviews.get('suggested', 0),
            'reviews_open': sum(count for state, count in reviews.items()
                                if state != 'approved'),
            'latest_closed': latest_closed, 'first_open': first_open,
            'open_months': open_months}


def chase_status(session):
    run = session.query(ChaseBrowserRuns).order_by(ChaseBrowserRuns.id.desc()).first()
    if run is None:
        return None
    return {'status': run.status, 'mode': run.mode,
            'finished_at': run.finished_at, 'updated_at': run.updated_at,
            'imported_count': run.imported_count}


def dashboard(session, now=None):
    now = now or datetime.now(timezone.utc)
    period = period_key(now, 'YYYY-MM')
    accounts = chart(session)
    summary = monthly_summary(session, period, accounts=accounts)
    this_month, last_month = summary['series'][-1], summary['series'][-2]
    latest_entry = session.query(JournalEntries.timestamp).order_by(
        JournalEntries.timestamp.desc()).limit(1).scalar()
    return dict(summary, period=period, now=now,
                month=this_month, previous=last_month,
                net_worth_change=this_month['net_worth'] - last_month['net_worth'],
                latest_entry=latest_entry,
                accounts=feed_accounts(session, summary['balances'], accounts),
                queue=work_queue(session, period),
                chase=chase_status(session))
