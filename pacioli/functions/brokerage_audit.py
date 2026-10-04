"""Independent checks of brokerage journals against their sources.

Issues carry a scope: 'ledger' issues mean posted journals are wrong and block
every posting batch; 'close' issues are incomplete work (unposted, unsupported,
awaiting lots) that only blocks closing the affected month.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from pacioli.functions.brokerage_accounts import registered_subaccounts
from pacioli.functions.brokerage_posting import SOURCES, source_status


def _before(value, end):
    if end is None or value is None:
        return True
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return value < end


def brokerage_differences(session, end=None):
    if not session.execute(text("SELECT to_regclass('investments.brokerage_sources')")).scalar():
        return []
    issues = []
    for source in session.execute(SOURCES, {'account': None}):
        if not _before(source.source_date, end):
            continue
        status, reason, _ = source_status(session, source)
        if status in ('posted', 'quantity_only'):
            continue
        if status == 'stale':
            issues.append(dict(kind='brokerage_source', scope='ledger', problem='posting_mismatch',
                               source=source.public_id, detail=reason))
        else:
            issues.append(dict(kind='brokerage_source', scope='close',
                               problem='unposted_source' if status == 'ready' else reason,
                               source=source.public_id))
    journals = session.execute(text('''SELECT j.id, j.transaction_id, j.timestamp, s.source_id
        FROM bookkeeping.journal_entries j
        LEFT JOIN investments.brokerage_sources s ON s.public_id=j.transaction_id
        WHERE j.transaction_source='schwab' ''')).all()
    for row in journals:
        if row.source_id is None and _before(row.timestamp, end):
            issues.append(dict(kind='brokerage_source', scope='ledger', problem='missing_source',
                               journal_id=row.id))
    reserved = registered_subaccounts(session)
    if reserved:
        foreign = session.execute(text('''SELECT id, timestamp FROM bookkeeping.journal_entries
            WHERE transaction_source IS DISTINCT FROM 'schwab'
              AND (debit_subaccount = ANY(:names) OR credit_subaccount = ANY(:names))'''),
            {'names': sorted(reserved)}).all()
        for row in foreign:
            if _before(row.timestamp, end):
                issues.append(dict(kind='brokerage_foreign_posting', scope='ledger', journal_id=row.id))
    return issues


def brokerage_source_facts(session, end=None):
    """Accepted brokerage evidence for close fingerprints, one row per fact.

    Lot facts exclude the wash disallowance: that is a separate fact dated when
    it became known, so a later lots export cannot alter an earlier closed month.
    """
    if not session.execute(text("SELECT to_regclass('investments.brokerage_sources')")).scalar():
        return []
    from pacioli.functions.reconciliation import digest
    rows = []
    queries = (
        ('schwab', '''SELECT s.public_id AS id, a.posted_on AS day, a.action, a.symbol, a.quantity, a.price,
                a.fees, a.amount FROM investments.brokerage_activity a
            JOIN investments.brokerage_sources s ON s.source_id=a.source_id'''),
        ('schwab_lot', '''SELECT id::text AS id, closed_on AS day, symbol, quantity, opened_on, proceeds,
                cost_basis, term, supersedes_id FROM investments.realized_lots'''),
        ('schwab_wash', '''SELECT s.public_id AS id, w.trigger_on AS day, w.lot_id, w.amount
            FROM investments.wash_adjustments w JOIN investments.brokerage_sources s ON s.source_id=w.source_id'''),
        ('schwab_opening', '''SELECT s.public_id AS id, o.as_of AS day, o.cash, o.cost_basis, e.sha256
            FROM investments.brokerage_openings o
            JOIN investments.brokerage_sources s ON s.source_id=o.source_id
            JOIN bookkeeping.accounting_evidence e ON e.id=o.evidence_id'''),
        ('schwab_valuation', '''SELECT s.public_id AS id, v.valuation_date AS day, v.market_value,
                v.cost_basis, v.target, e.sha256 FROM investments.valuation_points v
            JOIN investments.brokerage_sources s ON s.source_id=v.source_id
            JOIN bookkeeping.accounting_evidence e ON e.id=v.evidence_id'''),
    )
    for source, sql in queries:
        for row in session.execute(text(sql)):
            if not _before(row.day, end):
                continue
            values = {key: str(value) if value is not None else None for key, value in row._mapping.items()}
            rows.append(dict(source=source, transaction_id=values['id'], date=values['day'],
                             content_digest=digest(values)))
    return rows
