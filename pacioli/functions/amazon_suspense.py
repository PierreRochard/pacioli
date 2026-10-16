"""Source-linked suspense remediation; every action here is review-only.

The database owns imported evidence, owner facts and exact-product suggestions.
Neither plaintext rollout files nor a model are needed to serve this workbench.
Explicit financial approval remains in amazon_item_workflow.post_reviewed.
"""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import re

from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError

from pacioli.extensions import db
from pacioli.models import (AccountingEvidence, AmazonEvidenceBundles, AmazonItemJobs, AmazonItemPostRuns,
    AmazonItemRuns, AmazonProductRules, AmazonSuspenseFacts, AmazonSuspenseItems,
    AmazonSuspensePlans, JournalEntries)
from pacioli.functions import amazon_item_advisor as advisor
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions.accounting_report import chart_accounts
from pacioli.functions.reconciliation import digest, dump, evidence, money


VERSION = 'amazon-suspense-plan-v1'
ELIGIBILITY_VERSION = 'amazon-item-eligibility-v1'
PAGE_SIZE = 25
GROUPS = {
    'category_and_treatment': 'Category and equipment treatment',
    'conditional_purpose_or_project_fact': 'Purpose or project facts',
    'existing_category_boundary': 'Existing category boundaries',
}
GATES = ('ordinary', 'needs_fact', 'needs_property')


def _id(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise ValueError('Choose a valid suspense plan or page')
    return value


def _actor(reviewer, reason=None):
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 240:
        raise ValueError('A named reviewer is required')
    if reason is not None and (not isinstance(reason, str) or not reason.strip() or len(reason) > 2000):
        raise ValueError('Supply a bounded source fact or review reason')


def _hex(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _document(identifier, kind):
    return workflow._document(identifier, kind)


def _chart_digest():
    return digest(chart_accounts(db.session))


def _posting_active():
    return db.session.query(AmazonItemPostRuns.id).filter(AmazonItemPostRuns.state.in_(
        ('queued', 'running'))).first() is not None


def _require_posting_idle():
    if _posting_active():
        raise ValueError('An approved posting is queued or running; wait before changing item reviews')


def _lock():
    db.session.execute(text("SET LOCAL lock_timeout='5s'"))
    try:
        db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_suspense'))"))
    except DBAPIError as exc:
        if getattr(exc.orig, 'pgcode', None) == '55P03':
            raise ValueError('Another accounting action is in progress; wait, refresh this page and retry') from None
        raise


def _plan(plan_id, lock=False):
    query = db.session.query(AmazonSuspensePlans).populate_existing().filter_by(id=_id(plan_id))
    if lock:
        _lock()
        query = query.with_for_update()
    plan = query.one_or_none()
    if not plan:
        raise ValueError('Missing suspense plan')
    body = _document(plan.evidence_id, 'amazon_suspense_plan')
    if (body.get('version') != VERSION or body.get('source_run_id') != plan.source_run_id
            or digest(body.get('triage')) != plan.document_digest):
        raise ValueError('Suspense plan evidence changed')
    source = db.session.get(AmazonItemRuns, plan.source_run_id, populate_existing=True)
    if not source or source.input_digest != body['triage']['source_manifest_digest']:
        raise ValueError('Suspense source manifest changed')
    manifest = workflow._manifest(source)
    indexed = db.session.query(AmazonSuspenseItems).filter_by(plan_id=plan.id).all()
    originals = {row['item_id']: row for row in body['triage']['items']}
    if len(indexed) != len(originals):
        raise ValueError('Suspense item index changed')
    for row in indexed:
        item = originals.get(row.item_id)
        if (not item or row.source_job_id != item['job_id'] or row.amount != money(item['amount'])
                or row.remediation_group != item['remediation_group']
                or row.proposed_category != item.get('proposed_category')):
            raise ValueError('Suspense item index changed')
    return plan, body['triage'], manifest


def import_triage(doc, reviewer):
    """Import explicitly reviewed triage; caller commits. No accounting writes."""
    _actor(reviewer)
    if (not isinstance(doc, dict) or doc.get('version') not in ('amazon-suspense-triage-v1', 'amazon-suspense-triage-v2')
            or not _hex(doc.get('source_manifest_digest')) or not _hex(doc.get('source_snapshot_digest'))
            or not isinstance(doc.get('items'), list) or not 1 <= len(doc['items']) <= 10000
            or len(dump(doc).encode()) > 8 * 1024 * 1024):
        raise ValueError('A bounded source-linked triage document is required')
    _lock()
    fingerprint = digest(doc)
    old = db.session.query(AmazonSuspensePlans).filter_by(document_digest=fingerprint).one_or_none()
    if old:
        _plan(old.id)
        return old
    source = db.session.query(AmazonItemRuns).populate_existing().filter_by(
        input_digest=doc['source_manifest_digest']).with_for_update().one_or_none()
    if not source or source.state in ('queued', 'running'):
        raise ValueError('The source item review is not complete')
    manifest = workflow._manifest(source)
    originals = workflow._items(manifest)
    jobs = {j.item_id: j for j in db.session.query(AmazonItemJobs).populate_existing().filter_by(
        run_id=source.id).with_for_update().all()}
    held = {key for key, job in jobs.items() if not workflow._decision(job)
            or workflow._decision(job)['category'] is None}
    supplied = [row.get('item_id') for row in doc['items'] if isinstance(row, dict)]
    if len(supplied) != len(doc['items']) or len(set(supplied)) != len(supplied) or set(supplied) != held:
        raise ValueError('Triage must identify every held source occurrence exactly once')
    curated = any('policy_gate' in row for row in doc['items'])
    if curated and (doc.get('eligibility_version') != ELIGIBILITY_VERSION
                    or doc.get('eligibility_policy_digest') != workflow._policy()
                    or ('eligibility_chart_digest' in doc and doc['eligibility_chart_digest'] != _chart_digest())):
        raise ValueError('Source-reviewed eligibility must bind the current policy and chart')
    for row in doc['items']:
        item, job = originals[row['item_id']], jobs[row['item_id']]
        if (row.get('job_id') != job.id or row.get('source_decision_id') != job.decision_id
                or money(row.get('amount')) != money(item['amount'])
                or row.get('source_title', row.get('title')) != item['title']
                or row.get('source_refs') != item['source_refs']
                or row.get('components') != item['components']
                or row.get('quantity') != item['quantity']
                or row.get('product_key') != [item.get('marketplace'), item.get('asin')]
                or row.get('remediation_group') not in GROUPS
                or not isinstance(row.get('property_treatment_needed'), bool)
                or (row.get('proposed_category') is not None and not isinstance(row['proposed_category'], str))
                or ('policy_gate' in row and row['policy_gate'] not in GATES)):
            raise ValueError('Triage item does not match its immutable source and current decision')
    document = evidence('amazon_suspense_plan',
                        dict(version=VERSION, source_run_id=source.id, triage=doc), reviewer.strip())
    plan = AmazonSuspensePlans(evidence_id=document.id, document_digest=fingerprint, source_run_id=source.id)
    db.session.add(plan); db.session.flush()
    for row in doc['items']:
        db.session.add(AmazonSuspenseItems(plan_id=plan.id, item_id=row['item_id'],
            source_job_id=row['job_id'], amount=money(row['amount']),
            remediation_group=row['remediation_group'], proposed_category=row.get('proposed_category')))
    db.session.flush()
    return plan


def _run(plan, source_manifest):
    run = db.session.get(AmazonItemRuns, plan.review_run_id or plan.source_run_id, populate_existing=True)
    if not run:
        raise ValueError('Missing current item review')
    manifest = workflow._manifest(run)
    if manifest['projection']['archive_digest'] != source_manifest['projection']['archive_digest']:
        raise ValueError('Review run belongs to a different archive')
    jobs = {j.item_id: j for j in db.session.query(AmazonItemJobs).populate_existing().filter_by(run_id=run.id).all()}
    return run, manifest, jobs


def _facts(plan_id):
    result = defaultdict(list)
    for row in db.session.query(AmazonSuspenseFacts).filter_by(plan_id=plan_id).order_by(AmazonSuspenseFacts.id):
        body = _document(row.evidence_id, 'amazon_suspense_fact')
        if body.get('plan_id') != plan_id or body.get('item_id') != row.item_id:
            raise ValueError('Owner fact evidence changed')
        prior = result[row.item_id]
        if body.get('previous_fact_id') != (prior[-1]['id'] if prior else None):
            raise ValueError('Owner fact ancestry changed')
        prior.append(dict(id=row.id, evidence_id=row.evidence_id, note=body['note'],
                          reviewer=body['reviewer'], created_at=row.created_at.isoformat()))
    return result


def _token(plan, manifest):
    # Refresh identities before workflow.review_digest reads the decision IDs.
    run_ids = {plan.source_run_id, plan.review_run_id or plan.source_run_id}
    db.session.query(AmazonItemJobs).populate_existing().filter(AmazonItemJobs.run_id.in_(run_ids)).all()
    return digest(dict(plan=plan.document_digest, review_run_id=plan.review_run_id,
        reviews={str(i): workflow.review_digest(i) for i in sorted(run_ids)},
        facts=[(f.id, f.evidence_id) for f in db.session.query(AmazonSuspenseFacts).filter_by(
            plan_id=plan.id).order_by(AmazonSuspenseFacts.id)],
        rule_revision=db.session.query(func.max(AmazonProductRules.id)).scalar(),
        policy=workflow._policy(), chart=_chart_digest()))


def _eligibility_current(doc):
    return (doc.get('eligibility_version') == ELIGIBILITY_VERSION
            and doc.get('eligibility_policy_digest') == workflow._policy()
            and ('eligibility_chart_digest' not in doc or doc['eligibility_chart_digest'] == _chart_digest()))


def _gate(row, doc, eligibility_current=None):
    if 'policy_gate' in row:
        if eligibility_current is None:
            eligibility_current = _eligibility_current(doc)
        if not eligibility_current:
            return 'needs_property', 'The source-reviewed eligibility policy changed; import a refreshed review'
        gate = row['policy_gate']
    else:
        gate = ('needs_property' if row['property_treatment_needed'] else
                'needs_fact' if row['remediation_group'] == 'conditional_purpose_or_project_fact' else 'ordinary')
    return gate, ('An approved accounting-policy treatment is still required; see the source-review guidance'
                  if gate == 'needs_property' else '')


@contextmanager
def _run_lease(run_id):
    path = Path('/run/user') / str(os.getuid()) / ('pacioli-amazon-items-{}.lock'.format(run_id))
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('The item worker is active; wait before editing this review') from None
        yield
    finally:
        os.close(fd)


def _manual_prepare(run):
    run = db.session.query(AmazonItemRuns).populate_existing().filter_by(id=run.id).with_for_update().one()
    if run.state == 'running':
        raise ValueError('The item worker is active; wait before editing this review')
    jobs = db.session.query(AmazonItemJobs).populate_existing().filter_by(run_id=run.id).with_for_update().all()
    for job in jobs:
        if job.state == 'running':
            raise ValueError('The item worker is active; wait before editing this review')
        if job.state == 'queued' and job.decision_id is None:
            job.state = 'held'; job.proposal = None; job.updated_at = workflow._now()
    if any(j.decision_id is None and j.proposal is None for j in jobs):
        run.state = 'needs_attention'
        run.progress = 'Prepared for independent manual review; unreviewed items have no model proposal'
        run.updated_at = workflow._now()
    db.session.flush()


def prepare_new_review_run(plan_id, reviewer, think=False):
    """Create/reuse current-policy review and carry proven posted siblings.

    Unreviewed manual placeholders have no proposal. The user-triggered model
    worker may subsequently classify those held/no-proposal/unreviewed jobs.
    """
    _actor(reviewer)
    plan, doc, manifest = _plan(plan_id, lock=True)
    _require_posting_idle()
    with db.session.begin_nested():
        run = workflow.create_run(manifest['projection']['archive_id'], reviewer.strip(), think=think)
        with _run_lease(run.id):
            _manual_prepare(run)
            plan.review_run_id = run.id
            db.session.flush()
    return run


def save_fact(plan_id, item_id, note, expected_digest, reviewer):
    _actor(reviewer, note)
    _require_posting_idle()
    plan, doc, manifest = _plan(plan_id, lock=True)
    _require_posting_idle()
    if item_id not in {r['item_id'] for r in doc['items']}:
        raise ValueError('The item does not belong to this suspense plan')
    if not _hex(expected_digest) or _token(plan, manifest) != expected_digest:
        raise ValueError('The review or owner facts changed; refresh this page')
    prior = _facts(plan.id).get(item_id, [])
    body = dict(plan_id=plan.id, item_id=item_id, source_manifest_digest=doc['source_manifest_digest'],
                note=note.strip(), reviewer=reviewer.strip(), previous_fact_id=prior[-1]['id'] if prior else None)
    document = evidence('amazon_suspense_fact', body, reviewer.strip())
    fact = AmazonSuspenseFacts(plan_id=plan.id, item_id=item_id, evidence_id=document.id)
    db.session.add(fact); db.session.flush()
    return document


def _validate_sources(manifest, item_ids):
    """Recheck exact source occurrences and unique bank identity before reviews."""
    report = workflow.build_report(db.session, manifest['projection']['archive_id'])
    archive, tables = workflow._source_tables(db.session, manifest['projection']['archive_id'])
    if archive['source_sha256'] != manifest['projection']['archive_digest']:
        raise ValueError('Source archive changed')
    selected = [s for s in manifest['projection']['settlements'] if any(i['item_id'] in item_ids for i in s['items'])]
    records = {r['key']: r for r in report['records']}
    fresh = {s['record_key']: s for s in workflow.project_records(
        [records[s['record_key']] for s in selected if s['record_key'] in records], tables)}
    for old in selected:
        record = records.get(old['record_key']); projected = fresh.get(old['record_key'])
        if (not record or record['status'] not in ('unique_candidate', 'already_booked')
                or len(record['candidates']) != 1 or not projected
                or digest(workflow._stable_projection(old)) != digest(workflow._stable_projection(projected))):
            raise ValueError('The source occurrence or unique settlement match changed')
        previous, current = old['source_record']['candidates'][0], record['candidates'][0]
        if any(previous.get(k) != current.get(k) for k in
               ('id', 'source_timestamp', 'amount', 'account', 'account_id', 'currency', 'card_last4', 'description')):
            raise ValueError('The matched bank source changed')


def _product_identity(item):
    marketplace, asin, title = item.get('marketplace'), item.get('asin'), item.get('title')
    if (not isinstance(marketplace, str) or not marketplace.strip()
            or not isinstance(asin, str) or not re.fullmatch('[A-Z0-9]{10}', asin)
            or not isinstance(title, str) or not title.strip()):
        return None
    return dict(marketplace=marketplace, asin=asin, title_digest=digest(title))


def review_control(item, category, run_id, decision=None):
    """Controller gate shared by every review/posting entry point.

    A free-text note cannot authorize property policy. A fact-dependent review
    binds its precise immutable fact so a later correction invalidates posting.
    Items outside an imported plan keep the pre-existing workflow semantics.
    """
    if category is None:
        return None
    indexed = db.session.query(AmazonSuspenseItems).filter_by(item_id=item['item_id']).order_by(
        AmazonSuspenseItems.plan_id.desc()).first()
    if indexed is None:
        return None
    plan, doc, source_manifest = _plan(indexed.plan_id)
    original = workflow._items(source_manifest).get(item['item_id'])
    if original is None or digest(original) != digest(item):
        raise ValueError('The suspense source occurrence changed')
    run = db.session.get(AmazonItemRuns, run_id, populate_existing=True)
    if not run or workflow._manifest(run)['projection']['archive_digest'] != source_manifest['projection']['archive_digest']:
        raise ValueError('The suspense review belongs to a different source archive')
    row = next(r for r in doc['items'] if r['item_id'] == item['item_id'])
    from pacioli.functions import amazon_policy_application as application
    # A chart or prompt revision must not erase an independently proven,
    # previously posted sibling in a partially resolved settlement.
    # The carry proof lives in the immutable current manifest; a fresh manual
    # decision or an unresolved item cannot enter this narrow bridge.
    if decision is not None and decision.get('carry_forward'):
        origin = decision['carry_forward']
        carried = workflow._manifest(run).get('carried_decisions', {}).get(item['item_id'])
        if (carried == origin and origin.get('category') == category
                and origin.get('source_suspense_control') == decision.get('suspense_control')):
            workflow._assert_carried_review_current(decision)
            # Preserve the already posted interpretation, not a fresh approval
            # under an old policy. Its source, latest implementation, owner
            # answers and facts must still match the original exact control.
            implemented = application.control(plan, doc, original, category,
                decision=decision, historical_carried=True)
            if implemented is not None:
                return implemented
        original_gate = row.get('policy_gate')
        expected = dict(plan_id=plan.id, plan_evidence_id=plan.evidence_id,
            source_manifest_digest=doc['source_manifest_digest'],
            policy_digest=doc.get('eligibility_policy_digest'), gate='ordinary', fact_evidence_id=None)
        from pacioli.functions.amazon_policy_questions import _answers
        latest_answers, _ = _answers(plan.id)
        if (carried == origin and origin.get('source_suspense_control') == expected
                and decision.get('suspense_control') == expected
                and origin.get('category') == category and original_gate == 'ordinary'
                and not _facts(plan.id).get(item['item_id'])
                and not any(key[1] == item['item_id'] for key in latest_answers)):
            workflow._assert_carried_review_current(decision)
            return expected
    implemented = application.control(plan, doc, original, category, decision=decision)
    if implemented is not None:
        return implemented
    gate, message = _gate(row, doc)
    if gate == 'needs_property':
        raise ValueError(message)
    facts = _facts(plan.id).get(item['item_id'], []) if gate == 'needs_fact' else []
    if gate == 'needs_fact' and not facts:
        raise ValueError('Record and independently review the missing source fact before classification')
    control = dict(plan_id=plan.id, plan_evidence_id=plan.evidence_id,
        source_manifest_digest=doc['source_manifest_digest'], policy_digest=workflow._policy(),
        gate=gate, fact_evidence_id=facts[-1]['evidence_id'] if facts else None)
    if decision is not None and decision.get('suspense_control') != control:
        raise ValueError('The suspense eligibility or reviewed source fact changed; review this item again')
    return control


def review_control_revision(item_ids):
    """Opaque plan/fact ancestry for a frozen financial approval digest."""
    latest = {}
    for row in db.session.query(AmazonSuspenseItems).filter(AmazonSuspenseItems.item_id.in_(
            list(item_ids))).order_by(AmazonSuspenseItems.plan_id.desc()):
        latest.setdefault(row.item_id, row.plan_id)
    if not latest:
        return []
    plans = {p.id: p for p in db.session.query(AmazonSuspensePlans).filter(
        AmazonSuspensePlans.id.in_(set(latest.values())))}
    facts = {}
    for fact in db.session.query(AmazonSuspenseFacts).filter(AmazonSuspenseFacts.plan_id.in_(
            plans)).order_by(AmazonSuspenseFacts.id):
        facts[(fact.plan_id, fact.item_id)] = fact.evidence_id
    result = [dict(item_id=item_id, plan_id=plan_id, plan_digest=plans[plan_id].document_digest,
                 latest_fact_evidence_id=facts.get((plan_id, item_id)))
            for item_id, plan_id in sorted(latest.items())]
    from pacioli.functions.amazon_policy_application import revision
    implemented = revision(plans, item_ids)
    if implemented:
        result.append(dict(implemented_policy_revision=implemented))
    return result


def _remember(item, decision, reviewer):
    identity = _product_identity(item)
    if identity is None:
        raise ValueError('An exact marketplace, ASIN and full title are required to remember a product')
    key = digest(identity)
    old = db.session.query(AmazonProductRules).filter_by(product_digest=key).order_by(AmazonProductRules.id.desc()).first()
    body = dict(version='amazon-exact-product-rule-v1', product=identity, category=decision['category'],
        source_decision_id=decision['evidence_id'], source_item_id=item['item_id'],
        policy_digest=workflow._policy(), chart_digest=_chart_digest(),
        previous_rule_id=old.id if old else None, previous_evidence_id=old.evidence_id if old else None,
        reviewer=reviewer.strip(), suggestion_only=True)
    document = evidence('amazon_exact_product_rule', body, reviewer.strip())
    rule = AmazonProductRules(product_digest=key, evidence_id=document.id, supersedes_id=old.id if old else None)
    db.session.add(rule); db.session.flush()
    return rule


def product_suggestion(item, categories):
    """An exact, current, explicitly remembered product produces a suggestion."""
    identity = _product_identity(item)
    if identity is None or item.get('blockers'):
        return None
    rule = db.session.query(AmazonProductRules).filter_by(product_digest=digest(identity)).order_by(
        AmazonProductRules.id.desc()).first()
    if not rule:
        return None
    body = _document(rule.evidence_id, 'amazon_exact_product_rule')
    if (body.get('version') != 'amazon-exact-product-rule-v1' or body.get('product') != identity
            or body.get('suggestion_only') is not True or body.get('previous_rule_id') != rule.supersedes_id):
        raise ValueError('Exact-product rule evidence changed')
    ancestor, ancestor_body = rule, body
    seen = set()
    while ancestor.supersedes_id is not None:
        older = db.session.get(AmazonProductRules, ancestor.supersedes_id)
        if (not older or older.id in seen or older.id >= ancestor.id
                or older.product_digest != rule.product_digest
                or ancestor_body.get('previous_evidence_id') != older.evidence_id):
            raise ValueError('Exact-product rule ancestry changed')
        seen.add(older.id)
        ancestor, ancestor_body = older, _document(older.evidence_id, 'amazon_exact_product_rule')
        if (ancestor_body.get('product') != identity or ancestor_body.get('suggestion_only') is not True
                or ancestor_body.get('previous_rule_id') != older.supersedes_id):
            raise ValueError('Exact-product rule ancestry changed')
    if ancestor_body.get('previous_evidence_id') is not None:
        raise ValueError('Exact-product rule ancestry changed')
    if (body.get('policy_digest') != workflow._policy() or body.get('chart_digest') != _chart_digest()
            or body.get('category') not in categories or body['category'] not in advisor.CATEGORY_DEFINITIONS):
        return None
    decision = _document(body['source_decision_id'], 'amazon_item_decision')
    job = db.session.get(AmazonItemJobs, decision.get('job_id'), populate_existing=True)
    if (not job or job.decision_id != body['source_decision_id'] or decision.get('category') != body['category']
            or decision.get('item_id') != body['source_item_id']):
        return None
    source_run = db.session.get(AmazonItemRuns, job.run_id, populate_existing=True)
    if not source_run:
        return None
    source_item = workflow._items(workflow._manifest(source_run)).get(body['source_item_id'])
    if (not source_item or decision.get('manifest_digest') != source_run.input_digest
            or decision.get('item_digest') != digest(source_item) or _product_identity(source_item) != identity):
        return None
    try:
        review_control(source_item, body['category'], job.run_id, decision=decision)
    except ValueError:
        # A newer source-reviewed plan or corrected fact invalidates the rule's
        # authority even if the source decision pointer has not yet changed.
        return None
    view = workflow.model_item(item)
    answer = dict(item_id=item['item_id'], decision='suggest', category=body['category'],
        evidence_refs=['product.title'], policy_refs=[advisor.POLICY_VERSION, 'category:' + body['category']],
        reason='An explicitly reviewed exact-product rule matches this marketplace, ASIN and complete title.', missing_fact=None)
    if advisor.validate(answer, view, categories):
        return None
    return dict(answer=answer, category=body['category'], validation_errors=[],
        origin='reviewed_exact_product_rule', source_rule_id=rule.id,
        source_rule_evidence_id=rule.evidence_id, raw_content=None)


def apply_reviews(plan_id, item_ids, category, reason, expected_digest, reviewer, remember_product=False):
    """Atomic explicit source reviews. This function cannot post a journal."""
    _actor(reviewer, reason)
    if (not isinstance(item_ids, (list, tuple)) or not 1 <= len(item_ids) <= 100
            or any(not isinstance(i, str) for i in item_ids) or len(set(item_ids)) != len(item_ids)
            or type(remember_product) is not bool or (remember_product and category is None)):
        raise ValueError('Select distinct source items and an explicit review action')
    plan, doc, source_manifest = _plan(plan_id, lock=True)
    _require_posting_idle()
    if not _hex(expected_digest) or _token(plan, source_manifest) != expected_digest:
        raise ValueError('The review, source approvals or owner facts changed; refresh this page')
    if plan.review_run_id is None:
        raise ValueError('Prepare the current-policy review first')
    rows = {r['item_id']: r for r in doc['items']}
    if any(i not in rows for i in item_ids):
        raise ValueError('A selected item does not belong to this suspense plan')
    with _run_lease(plan.review_run_id), db.session.begin_nested():
        # Consistent source/chart reads; later financial posting independently
        # revalidates all of this under the accounting ledger lock.
        db.session.execute(text('LOCK TABLE ofx.stmttrn, ofx.acctfrom, ofx.ccacctfrom, ofx.bankacctfrom, '
            'bookkeeping.bank_feed_accounts, bookkeeping.subaccounts, bookkeeping.accounts, '
            'bookkeeping.classifications, bookkeeping.elements IN SHARE MODE'))
        run, manifest, jobs = _run(plan, source_manifest)
        _manual_prepare(run)
        db.session.query(AmazonItemJobs).populate_existing().filter(AmazonItemJobs.run_id.in_(
            {plan.source_run_id, run.id})).with_for_update().all()
        if _token(plan, source_manifest) != expected_digest:
            raise ValueError('The review or source approvals changed; refresh this page')
        if (manifest['policy_digest'] != workflow._policy() or digest(manifest['chart']) != _chart_digest()
                or (category is not None and category not in manifest['categories'])):
            raise ValueError('The category chart or policy changed; prepare a fresh review')
        items = workflow._items(manifest); facts = _facts(plan.id)
        eligibility_current = _eligibility_current(doc)
        for identifier in item_ids:
            if identifier not in items or identifier not in jobs:
                raise ValueError('A selected occurrence is no longer in the current review')
            original = workflow._items(source_manifest)[identifier]
            if digest(items[identifier]) != digest(original):
                raise ValueError('The selected source item changed')
            gate, message = _gate(rows[identifier], doc, eligibility_current)
            if category is not None:
                if gate == 'needs_property':
                    raise ValueError(message)
                if gate == 'needs_fact' and (not facts.get(identifier) or len(item_ids) != 1):
                    raise ValueError('Review fact-dependent items individually after recording a source fact')
                if items[identifier]['blockers']:
                    raise ValueError('The source item still has a controller blocker')
                if remember_product and (gate != 'ordinary' or _product_identity(items[identifier]) is None):
                    raise ValueError('Only ordinary, fully identified products can become reusable suggestions')
        _validate_sources(manifest, set(item_ids))
        reviewed, remembered = [], []
        for identifier in item_ids:
            decision_doc = workflow.review_item(jobs[identifier].id, category, reason, reviewer.strip())
            reviewed.append(decision_doc.id)
            if remember_product:
                remembered.append(_remember(items[identifier], workflow._decision(jobs[identifier]), reviewer).id)
        event = evidence('amazon_suspense_review', dict(plan_id=plan.id, run_id=run.id,
            before_digest=expected_digest, item_ids=list(item_ids), decision_ids=reviewed,
            fact_evidence_ids={i: facts[i][-1]['evidence_id'] for i in item_ids if facts.get(i)},
            product_rule_ids=remembered, reviewer=reviewer.strip(), reason=reason.strip()), reviewer.strip())
        db.session.flush()
        return dict(reviewed=len(reviewed), run_id=run.id, decision_ids=reviewed,
                    remembered=len(remembered), evidence_id=event.id, token=_token(plan, source_manifest))


def _posted_items(manifest, wanted):
    """Identify item categories from current immutable allocation evidence.

    A posted settlement alone never proves that a particular item was classified.
    Missing or inconsistent item proof yields no posted assertion on this page.
    """
    result = {}
    report = workflow.build_report(db.session, manifest['projection']['archive_id'])
    current_records = {r['key']: r for r in report['records']}
    item_proofs = []
    for document in db.session.query(AccountingEvidence).filter_by(kind='amazon_item_allocation'):
        body = json.loads(document.payload)
        if digest(body) == document.sha256:
            item_proofs.append(body)
    allocations = {}
    proof_documents = defaultdict(list)
    for document in db.session.query(AccountingEvidence).populate_existing().filter_by(kind='amazon_allocation').all():
        body = json.loads(document.payload); preview = body.get('preview', {})
        if (digest(body) == document.sha256 and preview.get('digest') ==
                digest({k: v for k, v in preview.items() if k != 'digest'})):
            allocations[preview['digest']] = preview
            proof_documents[preview['bundle_id']].append(document)
    for settlement in manifest['projection']['settlements']:
        if not any(i['item_id'] in wanted for i in settlement['items']):
            continue
        bundle = db.session.query(AmazonEvidenceBundles).populate_existing().filter_by(
            external_id=workflow._external(manifest, settlement['record_key'])).one_or_none()
        if not bundle or not bundle.posting_digest:
            candidates = settlement['source_record'].get('candidates', [])
            if len(candidates) != 1:
                continue
            source = candidates[0]
            current = current_records.get(settlement['record_key'])
            if (not current or current['status'] != 'unique_candidate' or len(current['candidates']) != 1
                    or any(current['candidates'][0].get(k) != source.get(k) for k in
                           ('id', 'source_timestamp', 'amount', 'account', 'account_id', 'currency', 'card_last4', 'description'))):
                continue
            entries = db.session.query(JournalEntries).filter_by(transaction_source='ofx',
                transaction_id=source['id']).all()
            amount = money(settlement['amount'])
            if len(entries) != 1:
                continue
            entry = entries[0]
            if (entry.allocation_key != '0' or entry.debit_subaccount != 'Bookkeeping Suspense'
                    or entry.credit_subaccount != source['account']
                    or entry.functional_amount != amount or entry.source_amount != amount
                    or entry.source_currency != 'USD' or entry.functional_currency != 'USD'
                    or entry.timestamp != datetime.fromisoformat(source['source_timestamp'])
                    or money(source['amount']) != -amount
                    or sum((money(i['amount']) for i in settlement['items']), Decimal(0)) != amount
                    or any(getattr(entry, k) is not None for k in ('conversion_rate', 'conversion_source', 'conversion_date'))):
                continue
            for item in settlement['items']:
                result[item['item_id']] = dict(category='Bookkeeping Suspense', amount=item['amount'],
                    decision_id=None, bundle_id=None, provenance='verified_original_suspense_source')
            continue
        matching = [p for p in item_proofs if p.get('bundle_id') == bundle.id
                    and p.get('posting_digest') == bundle.posting_digest and p.get('record_key') == settlement['record_key']]
        preview = allocations.get(bundle.posting_digest)
        if len(matching) != 1 or not preview or preview.get('bundle_id') != bundle.id:
            continue
        try:
            entries = db.session.query(JournalEntries).filter_by(transaction_source='ofx',
                transaction_id=bundle.transaction_id).order_by(JournalEntries.allocation_key).all()
            _, live_preview = workflow.allocation._current_proof(bundle, entries,
                proof_documents=proof_documents.get(bundle.id, []))
            if live_preview['digest'] != preview['digest']:
                continue
            proof = matching[0]
            posted_run = db.session.get(AmazonItemRuns, proof['run_id'])
            if not posted_run or posted_run.input_digest != proof['manifest_digest']:
                continue
            posted_manifest = workflow._manifest(posted_run)
            posted_settlement = next(s for s in posted_manifest['projection']['settlements']
                                     if s['record_key'] == settlement['record_key'])
            if digest(workflow._stable_projection(posted_settlement)) != digest(workflow._stable_projection(settlement)):
                continue
            decisions = {}; amounts = defaultdict(Decimal)
            for identifier in proof['decisions']:
                decision = _document(identifier, 'amazon_item_decision')
                if decision['run_id'] != posted_run.id or decision['item_id'] in decisions:
                    raise ValueError('Posted item evidence differs')
                decisions[decision['item_id']] = (identifier, decision)
            for item in settlement['items']:
                identifier, decision = decisions.get(item['item_id'], (None, None))
                if decision and (decision['item_digest'] != digest(item)
                                 or decision['manifest_digest'] != posted_run.input_digest):
                    raise ValueError('Posted item source differs')
                category = (decision or {}).get('category') or 'Bookkeeping Suspense'
                amounts[category] += money(item['amount'])
            if {k: v for k, v in amounts.items() if v} != {a['category']: money(a['amount']) for a in preview['allocations']}:
                continue
            for item in settlement['items']:
                identifier, decision = decisions.get(item['item_id'], (None, None))
                result[item['item_id']] = dict(category=(decision or {}).get('category') or 'Bookkeeping Suspense',
                    amount=item['amount'], decision_id=identifier, bundle_id=bundle.id)
        except (KeyError, ValueError, StopIteration):
            continue
    return result


def page(plan_id=None, group='', category='', page=1):
    number = _id(page)
    if not isinstance(group, str) or not isinstance(category, str) or (group and group not in GROUPS):
        raise ValueError('Choose a valid suspense filter')
    if plan_id is None:
        last = db.session.query(AmazonSuspensePlans).order_by(AmazonSuspensePlans.id.desc()).first()
        if not last:
            return dict(plan=None, token='', totals=dict(count=0, amount='0.00', fact_count=0, property_count=0),
                groups=[], categories=[], items=[], page=number, pages=1, total=0,
                group=group, category=category, available_categories=[])
        plan_id = last.id
    plan, doc, source_manifest = _plan(plan_id)
    run, manifest, jobs = _run(plan, source_manifest)
    originals = workflow._items(source_manifest); facts = _facts(plan.id)
    eligibility_current = _eligibility_current(doc)
    current = manifest['policy_digest'] == workflow._policy() and digest(manifest['chart']) == _chart_digest()
    chart = chart_accounts(db.session)
    available = sorted(k for k, v in chart.items() if v[0] == 'Expenses' and k in advisor.CATEGORY_DEFINITIONS)
    all_rows = doc['items']
    def aggregates(field):
        values = defaultdict(lambda: [0, Decimal(0)])
        for row in all_rows:
            key = row.get(field) or '__unassigned__'
            values[key][0] += 1; values[key][1] += money(row['amount'])
        return [dict(key=k, label=GROUPS.get(k, 'Treatment undecided' if k == '__unassigned__' else k), count=v[0], amount=str(v[1]))
                for k, v in sorted(values.items(), key=lambda pair: (-pair[1][1], pair[0]))]
    categories = aggregates('proposed_category')
    if category and category not in {r['key'] for r in categories}:
        raise ValueError('Choose a valid proposed category filter')
    selected = [r for r in all_rows if (not group or r['remediation_group'] == group)
                and (not category or (r.get('proposed_category') or '__unassigned__') == category)]
    selected.sort(key=lambda r: (-money(r['amount']), r['item_id']))
    pages = max(1, (len(selected) + PAGE_SIZE - 1) // PAGE_SIZE)
    if number > pages:
        raise ValueError('Invalid suspense page')
    selected_page = selected[(number - 1) * PAGE_SIZE:number * PAGE_SIZE]
    posted = _posted_items(source_manifest, {r['item_id'] for r in all_rows})
    posting_active = _posting_active()
    live_totals = {key + suffix: (0 if suffix == '_count' else Decimal(0))
                   for key in ('remaining', 'resolved', 'unverified', 'reviewed_unposted')
                   for suffix in ('_count', '_amount')}
    for row in all_rows:
        identifier = row['item_id']; financial = posted.get(identifier)
        key = ('unverified' if financial is None else
               'remaining' if financial['category'] == 'Bookkeeping Suspense' else 'resolved')
        live_totals[key + '_count'] += 1
        live_totals[key + '_amount'] += money(row['amount'])
        decision = workflow._decision(jobs[identifier]) if identifier in jobs else None
        if financial and decision and decision['category'] is not None and decision['category'] != financial['category']:
            live_totals['reviewed_unposted_count'] += 1
            live_totals['reviewed_unposted_amount'] += money(row['amount'])
    items = []
    for row in selected_page:
        identifier = row['item_id']; job = jobs.get(identifier)
        gate, hold_reason = _gate(row, doc, eligibility_current)
        if not hold_reason and gate == 'needs_fact' and not facts.get(identifier):
            hold_reason = 'Record the missing source or owner fact before individual review'
        if not current or plan.review_run_id is None:
            hold_reason = 'Prepare a current-policy review before classifying these items'
        elif not job:
            hold_reason = 'This source occurrence is no longer in the open review scope'
        elif run.state == 'running' or job.state in ('queued', 'running'):
            hold_reason = 'Wait for the item worker or prepare independent manual review'
        elif originals[identifier]['blockers']:
            hold_reason = 'The source item has a controller blocker'
        if posting_active:
            hold_reason = 'An approved posting is queued or running; wait before changing item reviews'
        items.append(dict(item_id=identifier, item=originals[identifier], group=row['remediation_group'],
            group_label=GROUPS[row['remediation_group']], proposed_category=row.get('proposed_category'),
            proposed_definition_available=row.get('proposed_category') in available,
            reason=row.get('reason', ''), action_needed=row.get('action_needed', ''),
            current_decision=workflow._decision(job) if job else None, posted=posted.get(identifier),
            can_review=not bool(hold_reason), hold_reason=hold_reason, needs_fact=gate == 'needs_fact',
            property_treatment_needed=gate == 'needs_property', policy_gate=gate,
            facts=facts.get(identifier, []), job_id=job.id if job else None,
            manual_source_review=bool(job and job.proposal is None)))
    return dict(plan=dict(id=plan.id, source_run_id=plan.source_run_id, review_run_id=plan.review_run_id,
            archive_id=source_manifest['projection']['archive_id'], current_policy=current,
            review_state=run.state, review_progress=run.progress, posting_active=posting_active), token=_token(plan, source_manifest),
        totals=dict(count=len(all_rows), amount=str(sum((money(r['amount']) for r in all_rows), Decimal(0))),
            fact_count=sum(_gate(r, doc, eligibility_current)[0] == 'needs_fact' for r in all_rows),
            saved_fact_count=sum(len(v) for v in facts.values()),
            property_count=sum(_gate(r, doc, eligibility_current)[0] == 'needs_property' for r in all_rows),
            **{k: str(v) if k.endswith('_amount') else v for k, v in live_totals.items()}),
        groups=aggregates('remediation_group'), categories=categories, items=items, page=number,
        pages=pages, total=len(selected), group=group, category=category, available_categories=available)
