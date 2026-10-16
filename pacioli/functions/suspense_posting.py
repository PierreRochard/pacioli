"""Audited, source-preserving reclassification of reviewed suspense mappings."""
from collections import Counter
from datetime import datetime, timezone
import json

from sqlalchemy import text

from pacioli.extensions import db
from pacioli.models import JournalEntries, Mappings
from pacioli.functions.accounting_report import audit_ledger
from pacioli.functions.local_mapping_posting import CHART_SQL, digest, blocking_batch_issues
from pacioli.functions.mapping_workflow import (SUSPENSE_ACCOUNTS, mapping_values,
                                                rebuild_trial_balances)


def snapshot_mappings(mapping_ids):
    """Capture every linked entry; a review cannot silently omit other rule uses."""
    mappings=[dict(r._mapping) for r in db.session.execute(text('''SELECT * FROM admin.mappings
        WHERE id=ANY(:ids) ORDER BY id'''),{'ids':mapping_ids})]
    journals=[dict(r._mapping) for r in db.session.execute(text('''SELECT * FROM bookkeeping.journal_entries
        WHERE mapping_id=ANY(:ids) ORDER BY id'''),{'ids':mapping_ids})]
    sources=[dict(r._mapping) for r in db.session.execute(text('''SELECT t.id,t.date,t.amount,t.description,t.account,
        t.account_id,b.currency FROM ofx.transactions t JOIN bookkeeping.bank_feed_accounts b
        ON b.ofx_account_id=t.account_id WHERE t.id=ANY(:ids) ORDER BY t.id'''),
        {'ids':[j['transaction_id'] for j in journals if j['transaction_source']=='ofx']})]
    chart=[dict(r._mapping) for r in db.session.execute(text(CHART_SQL))]
    snap=json.loads(json.dumps(dict(mappings=mappings,journals=journals,sources=sources,chart=chart),default=str))
    snap['snapshot_sha256']=digest(snap)
    return snap


def reclassify(snapshot, review, *, commit=False, finalize=True,
               source_category='Bookkeeping Suspense'):
    """Change category only after a bounded independent review, atomically.

    `source_category` is the provisional account every selected mapping and
    journal currently uses. Transfer Clearing legs already in a saved transfer
    match cannot be reclassified; unmatch them through reconciliation first.

    Existing journal IDs/source IDs, amounts, dates, currencies and bank legs
    remain. PostgreSQL records before/after images of each journal update.
    Exact replay must match the committed review and all protected fields.
    """
    if source_category not in SUSPENSE_ACCOUNTS:
        raise ValueError('Only provisional suspense or clearing mappings can be reclassified')
    if digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})!=snapshot['snapshot_sha256']:
        raise ValueError('Snapshot fingerprint mismatch')
    if review.get('snapshot_sha256')!=snapshot['snapshot_sha256']:
        raise ValueError('Review snapshot mismatch')
    if not review.get('reviewer') or not review.get('authorization'):
        raise ValueError('Reviewer and user authorization required')
    approvals=review.get('approvals',[])
    if not approvals or len({a['mapping_id'] for a in approvals})!=len(approvals):
        raise ValueError('Empty or duplicate mapping approvals')
    original={m['id']:m for m in snapshot['mappings']}
    ids=[a['mapping_id'] for a in approvals]
    if set(ids)!=set(original):
        raise ValueError('Review must cover exactly the snapshotted mappings')
    if any(not a.get('reason') or not a.get('proposal_sha256') for a in approvals):
        raise ValueError('Independent reason and proposal evidence required')
    batch_hash=digest(review)
    label='suspense' if source_category=='Bookkeeping Suspense' else 'clearing'
    try:
        db.session.execute(text("SET LOCAL lock_timeout='10s'"))
        db.session.execute(text("SET LOCAL statement_timeout='120s'"))
        db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
        db.session.execute(text('LOCK TABLE bookkeeping.journal_entries IN SHARE ROW EXCLUSIVE MODE'))
        db.session.execute(text('''LOCK TABLE ofx.stmttrn,ofx.acctfrom,bookkeeping.bank_feed_accounts,
            bookkeeping.subaccounts,bookkeeping.accounts,bookkeeping.classifications,
            bookkeeping.elements IN SHARE MODE'''))
        db.session.execute(text('LOCK TABLE admin.mappings IN SHARE ROW EXCLUSIVE MODE'))
        live=snapshot_mappings(ids)
        if live['chart']!=snapshot['chart'] or live['sources']!=snapshot['sources']:
            raise ValueError('Chart or sources changed since review')
        live_maps={m['id']:m for m in live['mappings']}
        live_entries={j['id']:j for j in live['journals']}
        originals={j['id']:j for j in snapshot['journals']}
        if set(live_maps)!=set(original) or set(live_entries)!=set(originals):
            raise ValueError('Mapping coverage changed since review')
        sources={s['id']:s for s in snapshot['sources']}
        chart={r['name']:r for r in snapshot['chart']}
        closed=db.session.execute(text('SELECT max(period) FROM bookkeeping.closed_periods')).scalar()
        matched={r[0] for r in db.session.execute(text('''SELECT journal_id FROM bookkeeping.transfer_match_legs
            WHERE journal_id=ANY(:ids)'''),{'ids':list(live_entries)})}
        if matched:
            raise ValueError('A selected journal belongs to a saved transfer match')
        changes=[];expected_maps={};expected_entries={}
        for approval in approvals:
            mid=approval['mapping_id'];old=original[mid];category=approval['category']
            if old['source']!='ofx' or old['match_mode']!='exact' or not old['review_required']:
                raise ValueError('Only exact provisional OFX mappings can be reclassified')
            if old['positive_debit_subaccount_id'] is not None or old['negative_credit_subaccount_id'] is not None:
                raise ValueError('Source account overrides require separate review')
            if old['positive_credit_subaccount_id']!=source_category or old['negative_debit_subaccount_id']!=source_category:
                raise ValueError('Original mapping is not entirely in '+source_category.lower())
            if (category not in chart or category in ('Bookkeeping Suspense','Amazon Suspense Account','Discretionary Costs',source_category)
                or not (chart[category]['element']=='Expenses' or category in ('Salary','Transfer Clearing'))):
                raise ValueError('Unsupported destination category')
            if category=='Salary' and chart[category]['element']!='Revenues':
                raise ValueError('Salary must remain income')
            if category=='Transfer Clearing' and chart[category]['element']!='Assets':
                raise ValueError('Clearing must remain a balance sheet account')
            desired=dict(old,**mapping_values(category),review_required=category=='Transfer Clearing',
                         review_note=approval['reason'])
            desired['provenance']=dict(old.get('provenance') or {},suspense_review={
                'batch_sha256':batch_hash,'proposal_sha256':approval['proposal_sha256'],
                'reviewer':review['reviewer'],'previous_category':source_category,
                'model':review.get('model'),'policy_sha256':review.get('policy_sha256')})
            if approval.get('reviewer_override') is True:
                if not approval.get('override_reason'):
                    raise ValueError('Independent override reason required')
                desired['provenance']['suspense_review'].update(
                    reviewer_override=True,override_reason=approval['override_reason'],
                    model_category=approval.get('model_category'),
                    model_validation_errors=approval.get('model_validation_errors'))
            expected_maps[mid]=desired
            linked=[j for j in snapshot['journals'] if j['mapping_id']==mid]
            if not linked: raise ValueError('Mapping has no reviewed journals')
            for old_entry in linked:
                if old_entry['transaction_source']!='ofx' or old_entry['allocation_key']!='0':
                    raise ValueError('Only unsplit OFX entries are supported')
                source=sources.get(old_entry['transaction_id'])
                if source is None or source['currency']!='USD' or source['description'].lower()!=old['keyword'].lower():
                    raise ValueError('Source does not match exact reviewed mapping')
                from decimal import Decimal
                amount=Decimal(source['amount'])
                if amount==0 or (category=='Salary' and amount<0):
                    raise ValueError('Invalid amount or outgoing salary')
                bank_side='debit_subaccount' if amount>0 else 'credit_subaccount'
                other_side='credit_subaccount' if amount>0 else 'debit_subaccount'
                if (old_entry[bank_side]!=source['account'] or old_entry[other_side]!=source_category
                    or Decimal(old_entry['functional_amount'])!=abs(amount)
                    or Decimal(old_entry['source_amount'])!=abs(amount)
                    or old_entry['functional_currency']!='USD' or old_entry['source_currency']!='USD'):
                    raise ValueError('Original journal does not preserve source movement')
                source_date=datetime.fromisoformat(source['date'])
                if source_date.tzinfo is None:source_date=source_date.replace(tzinfo=timezone.utc)
                if datetime.fromisoformat(old_entry['timestamp'])!=source_date:
                    raise ValueError('Original journal does not preserve source date')
                expected=dict(old_entry)
                expected[other_side]=category
                expected['memo']=(old_entry.get('memo') or '')+' | Reviewed {} correction '.format(label)+batch_hash
                expected_entries[old_entry['id']]=expected
                if live_entries[old_entry['id']] not in (old_entry,expected):
                    raise ValueError('Journal changed since review')
                if live_entries[old_entry['id']]!=expected:
                    if closed and old_entry['timestamp'][:7]<=closed:
                        raise ValueError('Cannot reclassify a closed period')
                    changes.append((old_entry['id'],other_side,category,expected['memo']))
            if live_maps[mid] not in (old,desired):
                raise ValueError('Mapping changed since review')
        changed_maps=[mid for mid in ids if live_maps[mid]!=expected_maps[mid]]
        # Partial replay is not a normal outcome of the atomic operation.
        if changes and len(changes)!=len(originals):
            raise ValueError('Partially changed batch requires a new review')
        if bool(changes)!=bool(changed_maps):
            raise ValueError('Inconsistent mapping and journal replay')
        result={'batch_sha256':batch_hash,'selected_entries':len(originals),'selected_mappings':len(ids),
                'would_change_entries':len(changes),'would_change_mappings':len(changed_maps),
                'changed_entries':0,'changed_mappings':0}
        if not commit:
            db.session.rollback();return result
        if not changes:
            db.session.rollback();return result
        db.session.execute(text("SELECT set_config('pacioli.actor',:actor,true)"),{'actor':review['reviewer'][:240]})
        db.session.execute(text("SELECT set_config('pacioli.reason',:reason,true)"),
                           {'reason':'Reviewed {} reclassification '.format(label)+batch_hash})
        state=db.session.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
            "'bookkeeping.journal_entries'::regclass AND tgname='subaccount_insert_trigger'")).scalar()
        if state!='O':raise ValueError('Derived balance trigger must be enabled')
        db.session.execute(text('ALTER TABLE bookkeeping.journal_entries DISABLE TRIGGER subaccount_insert_trigger'))
        for mid in changed_maps:
            mapping=db.session.get(Mappings, mid)
            for field in (*mapping_values('unused'),'review_required','review_note','provenance'):
                setattr(mapping,field,expected_maps[mid][field])
        for jid,side,category,memo in changes:
            entry=db.session.get(JournalEntries, jid)
            setattr(entry,side,category);entry.memo=memo
        db.session.flush()
        rebuild_trial_balances()
        db.session.execute(text('ALTER TABLE bookkeeping.journal_entries ENABLE TRIGGER subaccount_insert_trigger'))
        issues=audit_ledger(db.session)
        if blocking_batch_issues(db.session, issues, {('ofx', identifier) for identifier in sources}):
            raise ValueError('Ledger audit failed; correction rolled back')
        result.update(changed_entries=len(changes),changed_mappings=len(changed_maps),
                      audit_issue_counts=dict(Counter(i['kind'] for i in issues)))
        if finalize:
            db.session.commit()
        return result
    except Exception:
        db.session.rollback();raise
