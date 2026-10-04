"""Reviewed month closes protect historical personal-accounting statements."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from pacioli.models import ClosedPeriods, JournalEntries
from pacioli.functions.accounting_report import period_bounds, statement, source_records

# 1: OFX and Amazon sources. 2: adds brokerage facts and unrealized gains (OCI).
SOURCE_CONTRACT = 2


def journal_fingerprint(session, end):
    columns = ('id', 'timestamp', 'debit_subaccount', 'credit_subaccount',
               'functional_amount', 'functional_currency', 'source_amount',
               'source_currency', 'conversion_rate', 'conversion_source',
               'conversion_date', 'transaction_source', 'transaction_id',
               'allocation_key', 'mapping_id', 'memo')
    rows = []
    for entry in (session.query(JournalEntries)
                  .filter(JournalEntries.timestamp < end)
                  .order_by(JournalEntries.id)):
        row = {}
        for column in columns:
            value = getattr(entry, column)
            if column == 'timestamp':
                value = value.astimezone(timezone.utc).isoformat()
            else:
                value = str(value) if value is not None else None
            row[column] = value
        rows.append(row)
    payload = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return len(rows), hashlib.sha256(payload).hexdigest()


def source_fingerprint(session, end, version=SOURCE_CONTRACT):
    """Fingerprint accepted source evidence, including sources without journals."""
    rows = source_records(session, end)
    if version >= 2:
        from pacioli.functions.brokerage_audit import brokerage_source_facts
        rows = rows + brokerage_source_facts(session, end)
    normalized = [{key: str(value) if value is not None else None
                   for key, value in row.items()} for row in rows]
    normalized.sort(key=lambda row: (row['source'], row['transaction_id']))
    payload = json.dumps(normalized, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return len(rows), hashlib.sha256(payload).hexdigest()


def statement_snapshot(session, period, version=SOURCE_CONTRACT):
    report = statement(session, 'YYYY-MM', period)
    fields = ('assets', 'liabilities', 'contributions', 'distributions',
              'current_income', 'cumulative_income', 'equity', 'cash',
              'equation_difference')
    snapshot = {field: str(report[field]) for field in fields}
    snapshot['balance_rows'] = [[name, str(amount)] for name, amount
                                in report['balance_rows']]
    snapshot['income_rows'] = [[name, str(amount)] for name, amount
                               in report['income_rows']]
    snapshot['unknown_accounts'] = report['unknown_accounts']
    if version >= 2:
        snapshot['current_oci'] = str(report['current_oci'])
        snapshot['accumulated_oci'] = str(report['accumulated_oci'])
        snapshot['oci_rows'] = [[name, str(amount)] for name, amount in report['oci_rows']]
    _, end = period_bounds('YYYY-MM', period)
    count, digest = source_fingerprint(session, end, version)
    snapshot['source_contract_version'] = version
    snapshot['source_count'] = count
    snapshot['source_digest'] = digest
    return snapshot


def snapshot_report(closed):
    """Recover typed display amounts from an immutable closed snapshot."""
    saved = closed.statement_snapshot
    report = {field: Decimal(saved[field]) for field in (
        'assets', 'liabilities', 'contributions', 'distributions',
        'current_income', 'cumulative_income', 'equity', 'cash',
        'equation_difference')}
    report['balance_rows'] = [(name, Decimal(amount)) for name, amount
                              in saved['balance_rows']]
    report['income_rows'] = [(name, Decimal(amount)) for name, amount
                             in saved['income_rows']]
    report['unknown_accounts'] = saved['unknown_accounts']
    report['current_oci'] = Decimal(saved.get('current_oci', '0'))
    report['accumulated_oci'] = Decimal(saved.get('accumulated_oci', '0'))
    report['oci_rows'] = [(name, Decimal(amount)) for name, amount in saved.get('oci_rows', [])]
    report['comprehensive_income'] = report['current_income'] + report['current_oci']
    return report


def _version(closed):
    """Recompute a close at the contract it recorded; unrecorded means current."""
    return closed.statement_snapshot.get('source_contract_version') or SOURCE_CONTRACT


def closed_period_matches(session, closed):
    _, end = period_bounds('YYYY-MM', closed.period)
    count, digest = journal_fingerprint(session, end)
    return ((count, digest) == (closed.journal_count, closed.journal_digest)
            and statement_snapshot(session, closed.period, _version(closed)) == closed.statement_snapshot)


def closed_period_differences(session, end=None):
    differences = []
    for closed in session.query(ClosedPeriods).order_by(ClosedPeriods.period):
        _, closed_end = period_bounds('YYYY-MM', closed.period)
        if end is not None and closed_end > end:
            continue
        count, digest = journal_fingerprint(session, closed_end)
        snapshot = statement_snapshot(session, closed.period, _version(closed))
        source_fields = {'source_contract_version', 'source_count', 'source_digest'}
        financial_matches = ({k: v for k, v in snapshot.items() if k not in source_fields} ==
                             {k: v for k, v in closed.statement_snapshot.items() if k not in source_fields})
        if ((count, digest) != (closed.journal_count, closed.journal_digest) or
                snapshot != closed.statement_snapshot):
            differences.append({'period': closed.period,
                                'journal_count': count,
                                'expected_count': closed.journal_count,
                                'digest_matches': digest == closed.journal_digest,
                                'statement_matches': financial_matches,
                                'source_coverage_recorded': closed.statement_snapshot.get('source_contract_version') in (1, 2),
                                'source_matches': snapshot.get('source_digest') == closed.statement_snapshot.get('source_digest')})
    return differences


def close_month(session, period):
    """Stage a close in the caller's transaction after full reconciliation."""
    _, end = period_bounds('YYYY-MM', period)
    if end > datetime.now(timezone.utc):
        raise ValueError('Only completed months may be closed')
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
    latest = session.query(ClosedPeriods.period).order_by(ClosedPeriods.period.desc()).first()
    if latest is not None and period <= latest[0]:
        raise ValueError('Month is already closed or precedes a closed month')
    from pacioli.functions.accounting_report import audit_ledger
    problems = audit_ledger(session, end=end)
    if problems:
        raise ValueError('Accounting check found {} problems; month was not closed'.format(
            len(problems)))
    count, digest = journal_fingerprint(session, end)
    closed = ClosedPeriods(period=period, journal_count=count,
                           journal_digest=digest,
                           statement_snapshot=statement_snapshot(session, period))
    session.add(closed)
    session.flush()
    return closed
