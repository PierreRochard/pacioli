"""Preview and post brokerage sources as journals. Output is counts, not rows.

Posting re-derives every leg under the ledger lock, refuses if anything changed
since the reviewed preview digest, and commits only if the ledger audit passes.
Already-posted sources are never re-posted; stale ones require amend().
"""
from collections import Counter
from datetime import datetime

from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.brokerage_plan import Leg, NotReady, Unsupported, legs_for
from pacioli.functions.reconciliation import digest, evidence, lock_ledger, require_open


SOURCES = text('''SELECT s.source_id, s.public_id, s.account_id, s.kind,
        coalesce(a.posted_on, w.trigger_on, v.valuation_date, o.as_of) AS source_date
    FROM investments.brokerage_sources s
    LEFT JOIN investments.brokerage_activity a ON a.source_id=s.source_id
    LEFT JOIN investments.wash_adjustments w ON w.source_id=s.source_id
    LEFT JOIN investments.valuation_points v ON v.source_id=s.source_id
    LEFT JOIN investments.brokerage_openings o ON o.source_id=s.source_id
    WHERE (CAST(:account AS integer) IS NULL OR s.account_id=:account)
    ORDER BY source_date, s.source_id''')


def posted_legs(session, public_id):
    rows = session.execute(text('''SELECT allocation_key, debit_subaccount, credit_subaccount,
            functional_amount, timestamp FROM bookkeeping.journal_entries
        WHERE transaction_source='schwab' AND transaction_id=:id'''), {'id': public_id}).all()
    return {Leg(*row) for row in rows}


def source_status(session, source):
    """(status, reason, planned legs). Status: posted, ready, stale, quantity_only,
    not_ready or unsupported."""
    posted = posted_legs(session, source.public_id)
    try:
        legs = legs_for(session, source.source_id)
    except (NotReady, Unsupported) as exc:
        status = 'not_ready' if isinstance(exc, NotReady) else 'unsupported'
        return ('stale' if posted else status), str(exc), []
    planned = set(legs)
    if posted == planned:
        return ('posted' if planned else 'quantity_only'), None, legs
    return ('stale' if posted else 'ready'), None, legs


def preview(session, account_id=None):
    rows = []
    for source in session.execute(SOURCES, {'account': account_id}):
        status, reason, legs = source_status(session, source)
        rows.append(dict(public_id=source.public_id, kind=source.kind, date=source.source_date,
                         status=status, reason=reason,
                         legs=[[leg.key, leg.debit, leg.credit, str(leg.amount), leg.timestamp.isoformat()]
                               for leg in legs]))
    summary = {'sources': len(rows),
               'by_status': dict(Counter(row['status'] for row in rows)),
               'by_reason': dict(Counter(row['reason'] for row in rows if row['reason'])),
               'ready_legs': sum(len(row['legs']) for row in rows if row['status'] == 'ready'),
               'digest': digest([{k: v for k, v in row.items() if k != 'date'} for row in rows])}
    return summary, rows


def post(expected_digest, reviewer, reason, account_id=None):
    """Post every ready source atomically; returns counts."""
    from pacioli.functions.accounting_report import audit_ledger, trial_balance_differences
    from pacioli.functions.journal_posting import post_pair
    from pacioli.functions.local_mapping_posting import blocking_batch_issues
    from pacioli.functions.mapping_workflow import rebuild_trial_balances
    try:
        lock_ledger(reviewer, reason)
        summary, rows = preview(db.session, account_id)
        if summary['digest'] != expected_digest:
            raise ValueError('Brokerage sources changed since the preview; review again')
        ready = [row for row in rows if row['status'] == 'ready']
        if not ready:
            db.session.rollback()
            return dict(summary, posted_sources=0, posted_legs=0)
        for row in ready:
            for leg in row['legs']:
                require_open(datetime.fromisoformat(leg[4]))
        state = db.session.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
            "'bookkeeping.journal_entries'::regclass AND tgname='subaccount_insert_trigger'")).scalar()
        if state != 'O':
            raise ValueError('Trial-balance trigger must be enabled before bulk posting')
        db.session.execute(text('ALTER TABLE bookkeeping.journal_entries DISABLE TRIGGER subaccount_insert_trigger'))
        count = 0
        for row in ready:
            for key, debit, credit, amount, when in row['legs']:
                post_pair(timestamp=datetime.fromisoformat(when), debit=debit, credit=credit,
                          functional_amount=amount, transaction_source='schwab',
                          transaction_id=row['public_id'], allocation_key=key,
                          memo='Brokerage {} {}'.format(row['kind'], row['public_id']))
                count += 1
            db.session.flush()
        rebuild_trial_balances()
        db.session.execute(text('ALTER TABLE bookkeeping.journal_entries ENABLE TRIGGER subaccount_insert_trigger'))
        if trial_balance_differences(db.session):
            raise ValueError('Trial balances do not reconcile; posting rolled back')
        issues = audit_ledger(db.session)
        if blocking_batch_issues(db.session, issues, set()):
            raise ValueError('Ledger audit failed; brokerage posting rolled back')
        evidence('brokerage_posting', {'digest': expected_digest, 'reason': reason,
                                       'sources': [row['public_id'] for row in ready]}, reviewer)
        db.session.commit()
        return dict(summary, posted_sources=len(ready), posted_legs=count,
                    audit_issue_counts=dict(Counter(issue['kind'] for issue in issues)))
    except Exception:
        db.session.rollback()
        raise
