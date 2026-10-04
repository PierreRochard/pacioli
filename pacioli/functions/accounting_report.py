"""Independent, journal-derived personal financial statements and checks."""

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import json
import re
from hashlib import sha256

from sqlalchemy import text

from pacioli.models import (Accounts, AmazonItems, BankFeedAccounts,
                            Classifications, Elements, JournalEntries,
                            Subaccounts, TrialBalances)


ZERO = Decimal('0')
INTERVALS = ('YYYY', 'YYYY-Q', 'YYYY-MM', 'YYYY-WW', 'YYYY-MM-DD')
INCOME = frozenset(('Revenues', 'Expenses', 'Gains', 'Losses'))
BALANCE_SHEET = frozenset(('Assets', 'Liabilities',
                           'Capital Contributions', 'Capital Distributions'))
# Unrealized investment gains: part of equity and net worth, never of net income.
OCI = frozenset(('Other Comprehensive Income',))


def period_key(timestamp, interval):
    timestamp = timestamp.astimezone(timezone.utc)
    year = timestamp.year
    if interval == 'YYYY':
        return str(year)
    if interval == 'YYYY-Q':
        return '{}-{}'.format(year, (timestamp.month - 1) // 3 + 1)
    if interval == 'YYYY-MM':
        return timestamp.strftime('%Y-%m')
    if interval == 'YYYY-WW':
        return '{}-{:02d}'.format(year, (timestamp.timetuple().tm_yday - 1) // 7 + 1)
    if interval == 'YYYY-MM-DD':
        return timestamp.strftime('%Y-%m-%d')
    raise ValueError('Unsupported reporting interval')


def period_bounds(interval, period):
    """Inclusive start, exclusive end using UTC and PostgreSQL's YYYY-WW rule."""
    if interval not in INTERVALS:
        raise ValueError('Unsupported reporting interval')
    try:
        if interval == 'YYYY':
            year = int(period)
            if period != str(year):
                raise ValueError()
            start = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        elif interval == 'YYYY-Q':
            year_text, quarter_text = period.split('-')
            year, quarter = int(year_text), int(quarter_text)
            if period != '{}-{}'.format(year, quarter) or quarter not in (1, 2, 3, 4):
                raise ValueError()
            start = datetime(year, (quarter - 1) * 3 + 1, 1, tzinfo=timezone.utc)
            end = datetime(year + (quarter == 4), quarter * 3 + 1 if quarter < 4 else 1,
                           1, tzinfo=timezone.utc)
        elif interval == 'YYYY-MM':
            start = datetime.strptime(period, '%Y-%m').replace(tzinfo=timezone.utc)
            if start.strftime('%Y-%m') != period:
                raise ValueError()
            end = datetime(start.year + (start.month == 12), start.month % 12 + 1,
                           1, tzinfo=timezone.utc)
        elif interval == 'YYYY-WW':
            year_text, week_text = period.split('-')
            year, week = int(year_text), int(week_text)
            if period != '{}-{:02d}'.format(year, week) or not 1 <= week <= 53:
                raise ValueError()
            start = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=(week - 1) * 7)
            end = min(start + timedelta(days=7), datetime(year + 1, 1, 1,
                                                            tzinfo=timezone.utc))
            if start >= end:
                raise ValueError()
        else:
            start = datetime.strptime(period, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            if start.strftime('%Y-%m-%d') != period:
                raise ValueError()
            end = start + timedelta(days=1)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Invalid reporting period: {}'.format(period))
    return start, end


def chart_accounts(session):
    rows = (session.query(Subaccounts.name, Elements.name, Accounts.is_cash)
            .join(Accounts, Subaccounts.parent == Accounts.name)
            .join(Classifications, Accounts.parent == Classifications.name)
            .join(Elements, Classifications.parent == Elements.name).all())
    return {name: (element, bool(is_cash)) for name, element, is_cash in rows}


def statement(session, interval, period):
    """Calculate an as-of report from source journals, including quiet periods."""
    start, end = period_bounds(interval, period)
    accounts = chart_accounts(session)
    balances = defaultdict(lambda: ZERO)
    changes = defaultdict(lambda: ZERO)
    unknown = set()
    for entry in session.query(JournalEntries).filter(JournalEntries.timestamp < end):
        when = entry.timestamp.astimezone(timezone.utc)
        amount = entry.functional_amount
        for name, signed in ((entry.debit_subaccount, amount),
                             (entry.credit_subaccount, -amount)):
            balances[name] += signed
            if when >= start:
                changes[name] += signed
            if name not in accounts:
                unknown.add(name)
    totals = defaultdict(lambda: ZERO)
    current_income = ZERO
    cumulative_income = ZERO
    current_oci = ZERO
    accumulated_oci = ZERO
    cash = ZERO
    income_rows, balance_rows, oci_rows = [], [], []
    for name, balance in sorted(balances.items()):
        element, is_cash = accounts.get(name, (None, False))
        if element is None:
            continue
        totals[element] += balance
        if is_cash:
            cash += balance
        if element in INCOME:
            cumulative_income -= balance
            current_income -= changes[name]
            if changes[name]:
                income_rows.append((name, -changes[name]))
        elif element in OCI:
            accumulated_oci -= balance
            current_oci -= changes[name]
            if changes[name]:
                oci_rows.append((name, -changes[name]))
            if balance:
                balance_rows.append((name, balance))
        elif element in BALANCE_SHEET and balance:
            balance_rows.append((name, balance))
    assets = totals['Assets']
    liabilities = -totals['Liabilities']
    contributions = -totals['Capital Contributions']
    distributions = totals['Capital Distributions']
    equity = contributions - distributions + cumulative_income + accumulated_oci
    from pacioli.functions.mapping_workflow import SUSPENSE_ACCOUNTS
    from pacioli.functions.reconciliation import resolved_transfer_ids
    suspense_count = session.query(JournalEntries.id).filter(
        JournalEntries.timestamp < end,
        ~JournalEntries.id.in_(resolved_transfer_ids(session, end)),
        (JournalEntries.debit_subaccount.in_(SUSPENSE_ACCOUNTS) |
         JournalEntries.credit_subaccount.in_(SUSPENSE_ACCOUNTS))).count()
    return {
        'period': period, 'interval': interval,
        'income_rows': income_rows, 'balance_rows': balance_rows,
        'current_income': current_income, 'cumulative_income': cumulative_income,
        'current_oci': current_oci, 'accumulated_oci': accumulated_oci, 'oci_rows': oci_rows,
        'comprehensive_income': current_income + current_oci,
        'assets': assets, 'liabilities': liabilities,
        'contributions': contributions, 'distributions': distributions,
        'equity': equity, 'equation_difference': assets - liabilities - equity,
        'cash': cash, 'unknown_accounts': sorted(unknown),
        'balances': dict(balances), 'changes': dict(changes),
        'suspense_entries': suspense_count,
        'suspense_balance': sum((balances[name] for name in SUSPENSE_ACCOUNTS), ZERO),
    }


def trial_balance_differences(session, end=None):
    """Compare every stored metric and carried-forward row to a Python oracle."""
    entries = session.query(JournalEntries).all()
    actual = {(row.subaccount, row.period_interval, row.period): row
              for row in session.query(TrialBalances).all()}
    expected = {}
    for interval in INTERVALS:
        period_changes = defaultdict(lambda: [ZERO, ZERO])
        names_by_period = defaultdict(set)
        periods = set()
        for entry in entries:
            period = period_key(entry.timestamp, interval)
            periods.add(period)
            names_by_period[period].update((entry.debit_subaccount,
                                            entry.credit_subaccount))
            period_changes[period, entry.debit_subaccount][0] += entry.functional_amount
            period_changes[period, entry.credit_subaccount][1] += entry.functional_amount
        running = defaultdict(lambda: [ZERO, ZERO])
        active = set()
        periods = sorted(periods)
        for period in periods:
            active.update(names_by_period[period])
            for name in active:
                debit_changes, credit_changes = period_changes[period, name]
                running[name][0] += debit_changes
                running[name][1] += credit_changes
                debit_balance, credit_balance = running[name]
                expected[name, interval, period] = (debit_balance, credit_balance,
                    debit_balance - credit_balance, debit_changes, credit_changes,
                    debit_changes - credit_changes)
    fields = ('debit_balance', 'credit_balance', 'net_balance',
              'debit_changes', 'credit_changes', 'net_changes')
    differences = []
    for key in sorted(set(expected) | set(actual)):
        # A stored annual/quarterly balance includes the entire period. Only
        # compare completed metrics when validating an earlier month close.
        if end is not None and period_bounds(key[1], key[2])[1] > end:
            continue
        found = actual.get(key)
        value = tuple(getattr(found, field) for field in fields) if found else None
        if expected.get(key) != value:
            differences.append({'key': key, 'expected': expected.get(key),
                                'actual': value})
    return differences


def utc_timestamp(value):
    """OFX stores naive UTC; never reinterpret it in the host/session timezone."""
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def _amazon_proof_ancestry(proof, proofs):
    """Validate versioned review history without using a mutable ORM session."""
    from pacioli.functions.reconciliation import money
    required = {'bundle_id', 'evidence_digest', 'source', 'allocations', 'digest'}
    allowed = required | {'allow_suspense', 'revision'}
    current, seen = proof, set()
    while True:
        if (not isinstance(current, dict) or not required <= set(current) <= allowed
                or ('allow_suspense' in current and current['allow_suspense'] is not True)
                or current['digest'] in seen):
            raise ValueError('Invalid allocation proof schema or ancestry')
        seen.add(current['digest'])
        allocations = current['allocations']
        if (not isinstance(allocations, list) or not 1 <= len(allocations) <= 100
                or any(not isinstance(row, dict) or set(row) != {'category', 'amount'}
                       or not isinstance(row['category'], str) or not row['category']
                       or not isinstance(row['amount'], str) or money(row['amount']) <= 0
                       or row['category'] == 'Transfer Clearing'
                       or (row['category'] == 'Bookkeeping Suspense' and
                           current.get('allow_suspense') is not True) for row in allocations)
                or not any(row['category'] != 'Bookkeeping Suspense' for row in allocations)
                or allocations != sorted(allocations, key=lambda row: (row['category'], row['amount']))
                or sum((money(row['amount']) for row in allocations), ZERO) != abs(money(current['source']['amount']))):
            raise ValueError('Invalid historical allocation amounts or categories')
        if 'revision' not in current:
            return
        revision = current['revision']
        if (not isinstance(revision, dict) or set(revision) != {'number', 'previous_posting_digest'}
                or type(revision['number']) is not int or not 2 <= revision['number'] <= 10000
                or not isinstance(revision['previous_posting_digest'], str)
                or not re.fullmatch('[0-9a-f]{64}', revision['previous_posting_digest'])):
            raise ValueError('Invalid allocation revision identity')
        previous = proofs.get(revision['previous_posting_digest'])
        if (not previous or previous.get('revision', {}).get('number', 1) != revision['number'] - 1
                or previous['bundle_id'] != proof['bundle_id']
                or previous['source'] != proof['source']
                or previous['evidence_digest'] != proof['evidence_digest']):
            raise ValueError('Allocation revision ancestry is missing or changed')
        current = previous


def amazon_support_evidence(session, end=None):
    """Revalidate receipt support against immutable evidence and bank allocations.

    A populated bundle pointer is not proof. Recompute the exact reviewed
    allocation fingerprint and require every bank leg to preserve its source.
    """
    from pacioli.models import AmazonEvidenceBundles, AccountingEvidence
    from pacioli.functions.reconciliation import digest, dump
    bundles = session.query(AmazonEvidenceBundles).filter(
        AmazonEvidenceBundles.transaction_id.isnot(None) |
        AmazonEvidenceBundles.posting_digest.isnot(None)).all()
    if not bundles:
        return {}, []
    identifiers = [b.transaction_id for b in bundles if b.transaction_id]
    sources = {row.id: row for row in session.execute(text("""SELECT id,date,amount,
        description,account,account_id FROM ofx.transactions WHERE id=ANY(:ids)"""),
        {'ids': identifiers})} if identifiers else {}
    entries = defaultdict(list)
    for entry in session.query(JournalEntries).filter(
            JournalEntries.transaction_source == 'ofx',
            JournalEntries.transaction_id.in_(identifiers)).order_by(JournalEntries.allocation_key):
        entries[entry.transaction_id].append(entry)
    documents = {row.id: row for row in session.query(AccountingEvidence).filter(
        AccountingEvidence.id.in_([b.evidence_id for b in bundles]))}
    proofs = {}
    for doc in session.query(AccountingEvidence).filter_by(kind='amazon_allocation'):
        try:
            body = json.loads(doc.payload)
            preview = body['preview']
            if (isinstance(preview, dict) and digest(body) == doc.sha256 and
                    digest({k: v for k, v in preview.items() if k != 'digest'}) == preview['digest']):
                proofs[preview['digest']] = preview
        except (ValueError, TypeError, KeyError):
            continue
    chart = chart_accounts(session)
    supporting, issues = defaultdict(lambda: ZERO), []
    for bundle in bundles:
        source = sources.get(bundle.transaction_id)
        if end is not None and source is not None and source.date is not None and utc_timestamp(source.date) >= end:
            continue  # Future settlement cannot satisfy an earlier close.
        try:
            document = documents.get(bundle.evidence_id)
            body = json.loads(bundle.details)
            if (document is None or document.kind != 'amazon_items' or
                    sha256(document.payload.encode()).hexdigest() != document.sha256 or
                    json.loads(document.payload) != body or body['bundle_id'] != bundle.external_id or
                    not source or not bundle.posting_digest):
                raise ValueError('Missing or changed receipt evidence')
            lines = body['lines']
            proof = proofs.get(bundle.posting_digest)
            if proof is None:
                raise ValueError('Missing immutable allocation proof')
            _amazon_proof_ancestry(proof, proofs)
            partial = proof.get('allow_suspense') is True
            total = sum((Decimal(line['amount']) for line in lines), ZERO)
            if (not total.is_finite() or not total or source.amount != -total or
                    any(line['currency'] != 'USD' for line in lines)):
                raise ValueError('Receipt does not conserve the bank movement')
            allocations = []
            for index, entry in enumerate(entries[bundle.transaction_id]):
                bank_side, category = ((entry.credit_subaccount, entry.debit_subaccount)
                                       if source.amount < 0 else
                                       (entry.debit_subaccount, entry.credit_subaccount))
                if (bank_side != source.account or category == source.account or
                        chart.get(category, (None,))[0] not in ('Assets', 'Expenses') or
                        category == 'Transfer Clearing' or (category == 'Bookkeeping Suspense' and not partial) or
                        entry.source_currency != 'USD' or entry.functional_currency != 'USD' or
                        entry.source_amount != entry.functional_amount or
                        not entry.functional_amount.is_finite() or entry.functional_amount <= 0 or
                        utc_timestamp(entry.timestamp) != utc_timestamp(source.date) or
                        any(getattr(entry, field) is not None for field in
                            ('conversion_rate', 'conversion_source', 'conversion_date')) or
                        entry.allocation_key != 'amazon:{:03d}'.format(index)):
                    raise ValueError('Bank allocation has changed')
                allocations.append(dict(category=category, amount=str(entry.functional_amount)))
            if sum((Decimal(a['amount']) for a in allocations), ZERO) != abs(total):
                raise ValueError('Incomplete bank allocation')
            # Keys identify exact reviewed legs. Sorting a changed journal back
            # into canonical order would hide category/key association drift.
            if allocations != sorted(allocations, key=lambda a: (a['category'], a['amount'])):
                raise ValueError('Reviewed allocation order changed')
            payload = dict(bundle_id=bundle.id, evidence_digest=digest(body),
                source=dict(source, date=utc_timestamp(source.date)), allocations=allocations)
            if any(proof.get(k) != v for k,v in json.loads(dump(payload)).items()):
                raise ValueError('Reviewed allocation fingerprint no longer agrees')
            if partial and not any(a['category'] != 'Bookkeeping Suspense' for a in allocations):
                raise ValueError('An allocation cannot resolve nothing')
            for line in lines:
                supporting[line['order_id']] += Decimal(line['amount'])
        except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError):
            issues.append(dict(bundle_id=bundle.id, problem='invalid_bank_allocation_support'))
    return dict(supporting), issues


def source_records(session, end=None):
    """Read the accepted source population, including currently unposted rows.

    OFX posting time is the exact normalized provider timestamp. Legacy Amazon
    items have only a shipment date, so their posting contract is that UTC day.
    Reconciled Amazon bundles are supporting evidence for their existing bank
    settlement, not a second cash/accrual source.
    """
    params = {}
    cutoff = ''
    if end is not None:
        cutoff = ' WHERE t.dtposted < :end OR t.dtposted IS NULL'
        params['end'] = utc_timestamp(end).replace(tzinfo=None)
    rows = [dict(source='ofx', transaction_id=row.id if row.id is not None else
                 'unregistered:{}:{}'.format(row.account_id, row.fitid),
                 identity_registered=row.id is not None, amount=row.amount,
                 date=utc_timestamp(row.date) if row.date is not None else None,
                 account=row.account, account_id=row.account_id,
                 currency=row.currency, description=row.description, supporting=False)
            for row in session.execute(text('''SELECT identity.public_id AS id,t.fitid,
                t.dtposted AS date,t.trnamt AS amount,a.name AS account,t.acctfrom_id AS account_id,
                concat(t.name,t.memo) AS description,b.currency FROM ofx.stmttrn t
                LEFT JOIN bookkeeping.ofx_source_identities identity
                  ON identity.acctfrom_id=t.acctfrom_id AND identity.fitid=t.fitid
                LEFT JOIN ofx.acctfrom a ON a.id=t.acctfrom_id
                LEFT JOIN bookkeeping.bank_feed_accounts b ON b.ofx_account_id=t.acctfrom_id
                ''' + cutoff), params)]
    supporting_amounts, _ = amazon_support_evidence(session, end)
    items = session.query(AmazonItems)
    if end is not None:
        items = items.filter((AmazonItems.shipment_date < end.date()) |
                             AmazonItems.shipment_date.is_(None))
    items = items.all()
    order_amounts = defaultdict(lambda: ZERO)
    for item in items:
        if item.item_total is not None:
            order_amounts[item.order_id] += item.item_total
    supporting_orders = {order for order, amount in supporting_amounts.items()
                         if amount == order_amounts.get(order)}
    for item in items:
        rows.append(dict(source='amazon', transaction_id=str(item.id), identity_registered=True,
                         amount=item.item_total,
                         date=datetime.combine(item.shipment_date, time.min, timezone.utc)
                              if item.shipment_date else None,
                         account=None, account_id=None, currency=item.currency,
                         description=item.title,
                         supporting=item.order_id in supporting_orders))
    return rows


def source_differences(session, end=None):
    """Independently conserve every source and validate its individual cash leg."""
    records = source_records(session, end)
    chart = chart_accounts(session)
    sources = defaultdict(list)
    for row in records:
        sources[row['source'], row['transaction_id']].append(row)
    entries = defaultdict(list)
    journals = session.query(JournalEntries).filter(
        JournalEntries.transaction_source.in_(('amazon', 'ofx')))
    for entry in journals:
        key = entry.transaction_source, entry.transaction_id
        # A misdated future posting of a historical source must still be checked.
        if end is None or utc_timestamp(entry.timestamp) < end or key in sources:
            entries[key].append(entry)
    issues = []
    for key in sorted(set(sources) | set(entries)):
        source, identifier = key
        allocation = entries[key]
        rows = sources[key]
        common = dict(source=source, transaction_id=identifier)
        if len(rows) != 1:
            issues.append(dict(common, problem='missing_source' if not rows else 'ambiguous_source',
                               journal_ids=[e.id for e in allocation]))
            continue
        row = rows[0]
        if not row['identity_registered']:
            issues.append(dict(common, problem='missing_source_identity'))
        expected = row['amount']
        if expected is None or not expected.is_finite() or row['date'] is None:
            issues.append(dict(common, problem='invalid_source_amount_or_date'))
            continue
        if row['currency'] != 'USD' or (source == 'ofx' and
                chart.get(row['account'], (None, False))[0] not in ('Assets', 'Liabilities')):
            issues.append(dict(common, problem='invalid_source_account_or_currency'))
        if row['supporting']:
            if allocation:
                issues.append(dict(common, problem='duplicate_supporting_evidence',
                                   journal_ids=[e.id for e in allocation]))
            continue
        actual = sum((e.functional_amount for e in allocation
                      if e.functional_amount is not None and e.functional_amount.is_finite()), ZERO)
        if actual != abs(expected):
            # Keep the public amount-difference shape used by existing callers.
            difference = dict(common, booked=actual, source_amount=abs(expected))
            if not allocation:
                difference['problem'] = 'unposted_source'
            issues.append(difference)
        for entry in allocation:
            problems = []
            if entry.source_currency != row['currency'] or entry.source_amount != entry.functional_amount:
                problems.append('source_currency_or_amount')
            when = utc_timestamp(entry.timestamp)
            if (when != row['date'] if source == 'ofx' else when.date() != row['date'].date()):
                problems.append('posting_date')
            if source == 'ofx':
                signed_side = entry.debit_subaccount if expected > 0 else entry.credit_subaccount
                other_side = entry.credit_subaccount if expected > 0 else entry.debit_subaccount
                if signed_side != row['account'] or other_side == row['account']:
                    problems.append('bank_account_or_direction')
            if problems:
                issues.append(dict(common, journal_id=entry.id, problems=problems))
    return issues


def imported_reversal_differences(session, end=None):
    """A cash-source reversal needs reviewed amendment evidence before closing.

    Generic reversals preserve journal history, but cannot erase an accepted
    provider movement. Until a linked amendment workflow exists, this explicit
    unresolved state is safer than treating a cancelled bank leg as complete.
    """
    cutoff = ' AND reversal.timestamp < :end' if end is not None else ''
    return [dict(journal_id=row.id, original_journal_id=row.original_id,
                 source=row.transaction_source, transaction_id=row.transaction_id,
                 problem='imported_source_reversal_requires_reviewed_amendment')
            for row in session.execute(text("""SELECT reversal.id,original.id AS original_id,
                original.transaction_source,original.transaction_id
                FROM bookkeeping.journal_entries reversal
                JOIN bookkeeping.journal_entries original ON original.id::text=reversal.transaction_id
                WHERE reversal.transaction_source='reversal'
                  AND original.transaction_source IN ('ofx','amazon')""" + cutoff),
                {'end': end} if end is not None else {})]


def source_association_differences(session, end=None):
    """Check explicit foreign-key associations independently of their triggers."""
    cutoff = ''
    params = {}
    if end is not None:
        cutoff = ' AND (j.timestamp < :end OR s.dtposted < :source_end)'
        params = dict(end=end, source_end=utc_timestamp(end).replace(tzinfo=None))
    return [dict(journal_id=row.id, transaction_id=row.transaction_id,
                 problem='missing_or_incorrect_source_association')
            for row in session.execute(text("""SELECT j.id,j.transaction_id
                FROM bookkeeping.journal_entries j
                LEFT JOIN bookkeeping.ofx_source_identities identity ON identity.public_id=j.transaction_id
                LEFT JOIN bookkeeping.ofx_journal_sources link ON link.journal_entry_id=j.id
                LEFT JOIN ofx.stmttrn s ON s.acctfrom_id=identity.acctfrom_id AND s.fitid=identity.fitid
                WHERE j.transaction_source='ofx'
                  AND (link.journal_entry_id IS NULL OR identity.source_id IS NULL
                       OR link.source_id IS DISTINCT FROM identity.source_id)
                """ + cutoff), params)]


def ofx_currency_differences(session, end=None):
    return [dict(transaction_id=row['transaction_id'], ofx_account_id=row['account_id'],
                 reviewed_currency=row['currency'])
            for row in source_records(session, end)
            if row['source'] == 'ofx' and row['currency'] != 'USD']


def journal_money_differences(session, end=None):
    """Audit evidence independently of ORM helpers and database CHECKs."""
    from pacioli.functions.journal_posting import PRECISION
    entries = session.query(JournalEntries)
    if end is not None:
        entries = entries.filter(JournalEntries.timestamp < end)
    issues = []
    for entry in entries:
        problems = []
        for label, amount, currency in (
                ('functional', entry.functional_amount, entry.functional_currency),
                ('source', entry.source_amount, entry.source_currency)):
            valid = (isinstance(amount, Decimal) and amount.is_finite() and amount > 0
                     and currency in PRECISION)
            try:
                valid = valid and amount == amount.quantize(PRECISION[currency])
            except InvalidOperation:
                valid = False
            if not valid or (label == 'functional' and currency != 'USD'):
                problems.append(label + '_money')
        if entry.source_currency == 'USD':
            if entry.source_amount != entry.functional_amount or any(value is not None for value in (
                    entry.conversion_rate, entry.conversion_source, entry.conversion_date)):
                problems.append('usd_conversion_evidence')
        else:
            rate = entry.conversion_rate
            valid = (isinstance(rate, Decimal) and rate.is_finite() and rate > 0 and
                     isinstance(entry.conversion_source, str) and entry.conversion_source.strip() and
                     entry.conversion_date is not None)
            if valid and not problems:
                try:
                    valid = ((entry.source_amount * rate).quantize(Decimal('.01'),
                              rounding=ROUND_HALF_UP) == entry.functional_amount)
                except InvalidOperation:
                    valid = False
            if not valid:
                problems.append('foreign_conversion_evidence')
        if problems:
            issues.append(dict(journal_id=entry.id, problems=problems))
    return issues


def bank_balance_differences(session, *, include_reviewed=False, end=None):
    """Check dated balances, honoring only revalidated, reviewed cutoff evidence."""
    snapshots = session.execute(text("""
        SELECT lb.acctfrom_id, lb.dtasof, lb.balamt, af.name,
               cc.id IS NOT NULL AS is_credit_card
        FROM ofx.ledgerbal lb
        JOIN ofx.acctfrom af ON af.id = lb.acctfrom_id
        LEFT JOIN ofx.ccacctfrom cc ON cc.id = lb.acctfrom_id
        ORDER BY lb.acctfrom_id, lb.dtasof
    """)).fetchall()
    accounts = chart_accounts(session)
    from pacioli.functions.snapshot_reconciliation import reviewed
    reconciled = set() if include_reviewed else reviewed(session)[0]
    differences = []
    for account_id, as_of, source_balance, subaccount, is_credit_card in snapshots:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        if end is not None and as_of >= end:
            continue
        next_day = datetime.combine(as_of.astimezone(timezone.utc).date() +
                                    timedelta(days=1), time.min,
                                    tzinfo=timezone.utc)
        registration = session.query(BankFeedAccounts).get(account_id)
        if registration is None or registration.currency != 'USD':
            differences.append({'account_id': account_id, 'as_of': as_of,
                                'subaccount': subaccount,
                                'problem': 'Bank feed currency is not reviewed USD'})
            continue
        element, is_cash = accounts.get(subaccount, (None, False))
        valid_account = element == 'Liabilities' if is_credit_card else is_cash
        if not valid_account:
            differences.append({'account_id': account_id, 'as_of': as_of,
                                'subaccount': subaccount,
                                'problem': ('Credit card must map to a liability'
                                            if is_credit_card else
                                            'Bank account is not designated as cash')})
            continue
        booked = session.execute(text("""
            SELECT COALESCE(SUM(
                CASE WHEN debit_subaccount = :subaccount THEN functional_amount ELSE 0 END
                - CASE WHEN credit_subaccount = :subaccount THEN functional_amount ELSE 0 END
            ), 0)
            FROM bookkeeping.journal_entries
            WHERE timestamp < :next_day
              AND (debit_subaccount = :subaccount OR credit_subaccount = :subaccount)
        """), {'subaccount': subaccount, 'next_day': next_day}).scalar()
        if booked != source_balance and (account_id, as_of, source_balance) not in reconciled:
            differences.append({'account_id': account_id, 'as_of': as_of,
                                'subaccount': subaccount, 'booked': booked,
                                'bank_balance': source_balance})
    return differences


def amazon_settlement_differences(session, end=None):
    """Amazon purchases/refunds must flow through a payable or cash account."""
    accounts = chart_accounts(session)
    issues = []
    entries = session.query(JournalEntries).filter_by(transaction_source='amazon')
    if end is not None:
        entries = entries.filter(JournalEntries.timestamp < end)
    for entry in entries:
        item = session.query(AmazonItems).get(int(entry.transaction_id)) if (
            entry.transaction_id and entry.transaction_id.isdigit()) else None
        if item is None or item.item_total is None:
            continue  # Source existence and amount are checked separately.
        settlement = (entry.credit_subaccount if item.item_total > 0
                      else entry.debit_subaccount)
        element, is_cash = accounts.get(settlement, (None, False))
        if element != 'Liabilities' and not is_cash:
            issues.append({'journal_id': entry.id, 'settlement_account': settlement,
                           'element': element})
    return issues


def audit_ledger(session, end=None):
    """Return a read-only list of independently detected accounting problems."""
    issues = [dict(kind='journal_money', **difference)
              for difference in journal_money_differences(session, end)]
    if any('functional_money' in issue['problems'] for issue in issues):
        # Corrupt nonfinite numeric values cannot safely enter report arithmetic.
        return issues
    from pacioli.functions.reconciliation import evidence_differences
    issues.extend(evidence_differences(session, end=end))
    from pacioli.functions.mapping_workflow import SUSPENSE_ACCOUNTS
    from pacioli.functions.reconciliation import resolved_transfer_ids
    pending_query = session.query(JournalEntries.id).filter(
        ~JournalEntries.id.in_(resolved_transfer_ids(session, end)),
        JournalEntries.debit_subaccount.in_(SUSPENSE_ACCOUNTS) |
        JournalEntries.credit_subaccount.in_(SUSPENSE_ACCOUNTS))
    if end is not None:
        pending_query = pending_query.filter(JournalEntries.timestamp < end)
    pending = pending_query.count()
    if pending:
        issues.append({'kind': 'classification_pending', 'journal_count': pending})
    entries_query = session.query(JournalEntries)
    if end is not None:
        entries_query = entries_query.filter(JournalEntries.timestamp < end)
    for entry in entries_query.filter(
            JournalEntries.debit_subaccount == JournalEntries.credit_subaccount):
        issues.append({'kind': 'self_offset', 'journal_id': entry.id,
                       'subaccount': entry.debit_subaccount})
    for difference in trial_balance_differences(session, end=end):
        issues.append({'kind': 'trial_balance', **difference})
    for difference in source_differences(session, end=end):
        issues.append({'kind': 'source_amount', **difference})
    for difference in imported_reversal_differences(session, end=end):
        issues.append({'kind': 'source_correction', **difference})
    for difference in amazon_support_evidence(session, end=end)[1]:
        issues.append({'kind': 'amazon_support_evidence', **difference})
    for difference in source_association_differences(session, end=end):
        issues.append({'kind': 'source_identity', **difference})
    for difference in ofx_currency_differences(session, end=end):
        issues.append({'kind': 'ofx_currency', **difference})
    for difference in bank_balance_differences(session, end=end):
        issues.append({'kind': 'bank_balance', **difference})
    for difference in amazon_settlement_differences(session, end=end):
        issues.append({'kind': 'amazon_settlement', **difference})
    from pacioli.functions.brokerage_audit import brokerage_differences
    issues.extend(brokerage_differences(session, end=end))
    from pacioli.functions.period_close import closed_period_differences
    for difference in closed_period_differences(session, end=end):
        issues.append({'kind': 'closed_period', **difference})
    entries = entries_query.with_entities(JournalEntries.timestamp).all()
    if entries:
        start = min(value[0] for value in entries).astimezone(timezone.utc)
        last = max(max(value[0] for value in entries).astimezone(timezone.utc),
                   datetime.now(timezone.utc))
        if end is not None:
            last = min(last, end - timedelta(microseconds=1))
        cursor = start.year * 12 + start.month - 1
        final = last.year * 12 + last.month - 1
        while cursor <= final:
            period = '{}-{:02d}'.format(cursor // 12, cursor % 12 + 1)
            report = statement(session, 'YYYY-MM', period)
            if report['unknown_accounts']:
                issues.append({'kind': 'unclassified_accounts', 'period': period,
                               'accounts': report['unknown_accounts']})
            if report['equation_difference']:
                issues.append({'kind': 'accounting_equation', 'period': period,
                               'difference': report['equation_difference']})
            cursor += 1
    return issues
