"""Bounded owner reviews and independent merchant-disjoint evaluation cases."""
from datetime import datetime, timezone
import json
import re
from sqlalchemy import text
from pacioli.extensions import db
from pacioli.models import AccountingReviews, AccountingEvaluationCases, Mappings
from pacioli.functions.reconciliation import dump, digest, evidence
from pacioli.functions.suspense_posting import snapshot_mappings, reclassify
from pacioli.functions.suspense_advisor import normalized_description


def create_review(mapping_id, fact, fact_source, reviewer):
    mapping = db.session.get(Mappings, mapping_id)
    if not mapping or mapping.source!='ofx' or mapping.match_mode!='exact' or not mapping.review_required:
        raise ValueError('Choose an exact provisional OFX mapping')
    if len(fact)>4000 or len(fact_source)>1000 or (fact and not fact_source.strip()):
        raise ValueError('A merchant fact needs a source and bounded text')
    snap = snapshot_mappings([mapping_id])
    if not snap['journals'] or any('Bookkeeping Suspense' not in (j['debit_subaccount'],j['credit_subaccount']) for j in snap['journals']):
        raise ValueError('This queue reviews suspense; use transfer or item reconciliation for other entries')
    facts = dict(text=fact, source=fact_source, verified_by=reviewer)
    # A reload/double-click never creates a second identical review job.
    for old in db.session.query(AccountingReviews).filter_by(mapping_id=mapping_id).order_by(AccountingReviews.id.desc()).limit(20):
        if old.snapshot==dump(snap) and old.facts==dump(facts):
            return old
    row = AccountingReviews(mapping_id=mapping_id,snapshot=dump(snap),facts=dump(facts),reviewer=reviewer,state='ready')
    db.session.add(row)
    db.session.flush()
    return row


def model_record(review):
    snap = json.loads(review.snapshot)
    examples = []
    seen=set()
    for s in snap['sources']:
        sign='credit' if __import__('decimal').Decimal(s['amount'])>0 else 'debit'
        key=(s['description'],sign,s['currency'])
        if key not in seen:
            examples.append(dict(description=s['description'],sign=sign,currency=s['currency']))
            seen.add(key)
    if len(examples)>24 or len(dump(examples))>12000:
        raise ValueError('Review group exceeds the bounded model context')
    facts=json.loads(review.facts)
    return dict(examples=examples,verified_merchant_facts=[facts] if facts['text'] else [])


def run_advisor(review_id, infer_fn=None):
    """One worker, one bounded job. Model output is evidence, never authorization."""
    row=db.session.get(AccountingReviews, review_id)
    if not row or row.state!='queued':
        return
    row.state='running';row.updated_at=datetime.now(timezone.utc);db.session.commit()
    try:
        record=model_record(row)
        snap=json.loads(row.snapshot)
        categories=sorted({c['name'] for c in snap['chart']
            if (c['element']=='Expenses' and c['name'] not in ('Amazon Suspense Account','Discretionary Costs'))
            or c['name'] in ('Salary','Transfer Clearing','Bookkeeping Suspense')})
        if infer_fn is None:
            from pacioli.functions.chase_local_model import LocalModel
            from pacioli.functions.suspense_advisor import PROFILES
            from scripts.evaluate_suspense_advisor import infer
            name,sha,minimum=PROFILES['text14']
            with LocalModel(model=name,digest=sha,minimum_free_mib=minimum) as model:
                proposal=infer(model,record,'text14','system',think=True,temperature=.6,categories=categories)
        else:
            proposal=infer_fn(record,categories)
        row.proposal=dump(proposal);row.state='suggested'
    except Exception as exc:
        # Never persist traceback, credentials, or arbitrary HTTP response bodies.
        row.state='failed';row.proposal=dump({'error_type':type(exc).__name__,
            'message':'Local model could not finish. Retry when the GPU is available.'})
    row.updated_at=datetime.now(timezone.utc);db.session.commit()


def approve_review(review_id, category, reason, reviewer):
    row=db.session.get(AccountingReviews, review_id)
    if not row or not reason.strip() or len(reason)>2000:
        raise ValueError('A review and independent reason are required')
    if row.state=='approved':
        decision=json.loads(row.decision)
        if decision['category']!=category or decision['reason']!=reason:
            raise ValueError('Review already has a different decision')
        return decision
    if row.state in ('queued','running'):
        raise ValueError('Wait for the pending suggestion')
    snap=json.loads(row.snapshot)
    if category=='Bookkeeping Suspense':
        raise ValueError('Leave unresolved transactions pending until purpose is established')
    review=dict(snapshot_sha256=snap['snapshot_sha256'],reviewer=reviewer,
        authorization='Owner approved the displayed exact rule and every linked journal in Pacioli',
        approvals=[dict(mapping_id=row.mapping_id,category=category,reason=reason,
            proposal_sha256=digest(dict(proposal=json.loads(row.proposal) if row.proposal else None,
                                        facts=json.loads(row.facts))))])
    result=reclassify(snap,review,commit=True,finalize=False)
    decision=dict(category=category,reason=reason,reviewer=reviewer,result=result)
    row.state='approved';row.decision=dump(decision);row.updated_at=datetime.now(timezone.utc)
    evidence('merchant_review',dict(snapshot=snap,facts=json.loads(row.facts),decision=decision),reviewer)
    db.session.flush()
    return decision


def add_gold_case(source_id, merchant, partition, expected, reason, reviewer):
    """Gold is supplied independently; it is never copied from model proposals."""
    merchant=normalized_description(merchant)
    if not merchant or partition not in ('development','holdout') or not reason.strip():
        raise ValueError('Merchant family, partition and independent label evidence are required')
    from pacioli.functions.accounting_report import chart_accounts
    if expected not in chart_accounts(db.session):
        raise ValueError('Gold category is absent from the chart')
    existing=db.session.query(AccountingEvaluationCases).filter_by(merchant=merchant).all()
    if any(c.partition!=partition for c in existing):
        raise ValueError('A merchant family cannot cross the development/holdout boundary')
    source=db.session.execute(text('SELECT id, description, amount, account_id FROM ofx.transactions WHERE id=:id'),{'id':source_id}).first()
    if not source:
        raise ValueError('Missing real source transaction')
    from pacioli.functions.reconciliation import account
    account(source.account_id)
    record=dict(examples=[dict(description=source.description,sign='credit' if source.amount>0 else 'debit',currency='USD')])
    old=db.session.query(AccountingEvaluationCases).filter_by(source_id=source_id).one_or_none()
    if old:
        if (old.merchant,old.partition,old.expected,old.record,old.evidence)!=(merchant,partition,expected,dump(record),reason):
            raise ValueError('Gold case is frozen; conflicting labels require a new version')
        return old
    row=AccountingEvaluationCases(source_id=source_id,merchant=merchant,partition=partition,
        record=dump(record),expected=expected,evidence=reason,reviewer=reviewer)
    db.session.add(row);db.session.flush()
    return row


def export_gold():
    rows=list(db.session.query(AccountingEvaluationCases).order_by(AccountingEvaluationCases.id))
    partitions={}
    for row in rows:
        if row.merchant in partitions and partitions[row.merchant]!=row.partition:
            raise ValueError('Merchant leakage detected')
        partitions[row.merchant]=row.partition
    return [dict(id=r.id,merchant=r.merchant,partition=r.partition,record=json.loads(r.record),
                 expected=r.expected,evidence=r.evidence,reviewer=r.reviewer) for r in rows]
