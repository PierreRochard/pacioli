"""Resumable local item proposals and explicit, source-preserving approvals."""
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
import csv
import fcntl
import io
import json
import os
from pathlib import Path
import time

from sqlalchemy import text, func, case
from sqlalchemy.orm import defer
from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonEvidenceBundles, AmazonItemJobs, AmazonItemRuns, JournalEntries
from pacioli.functions import amazon_evidence as allocation
from pacioli.functions import amazon_item_advisor as advisor
from pacioli.functions.amazon_item_evidence import build_projection, model_item, project_records
from pacioli.functions.amazon_reconciliation import build_report, _source_tables
from pacioli.functions.accounting_report import chart_accounts
from pacioli.functions.reconciliation import digest, dump, evidence, lock_ledger, money


VERSION = 'amazon-item-workflow-v1'
PAGE_SIZE = 25


def _now():
    return datetime.now(timezone.utc)


def _policy():
    return digest(dict(version=advisor.POLICY_VERSION, text=advisor.POLICY,
                       categories=advisor.CATEGORY_DEFINITIONS))


def _document(identifier, kind):
    doc = db.session.get(AccountingEvidence, identifier)
    if not doc or doc.kind != kind:
        raise ValueError('Missing item review evidence')
    body = json.loads(doc.payload)
    if digest(body) != doc.sha256:
        raise ValueError('Item review evidence changed')
    return body


def _manifest(run):
    body = _document(run.manifest_id, 'amazon_item_manifest')
    if digest(body) != run.input_digest:
        raise ValueError('Item run manifest changed')
    return body


def _jobs(run_id):
    return db.session.query(AmazonItemJobs).options(defer(AmazonItemJobs.proposal)).filter_by(
        run_id=run_id).order_by(AmazonItemJobs.id).all()


def _items(manifest):
    return {i['item_id']: i for s in manifest['projection']['settlements'] for i in s['items']}


def _decision(job):
    if job.decision_id is None:
        return None
    body = _document(job.decision_id, 'amazon_item_decision')
    if body['job_id'] != job.id or body['item_id'] != job.item_id or body['run_id'] != job.run_id:
        raise ValueError('Decision belongs to another item')
    return dict(body, evidence_id=job.decision_id)


def _external(manifest, record_key):
    return 'archive-item:' + digest(dict(archive=manifest['projection']['archive_digest'], record=record_key))


ITEM_CATEGORY_NAMES = (
    'Household Supplies', 'Office and Craft Supplies', 'Garden Supplies',
    'Personal Wellness and Supplements', 'Vehicle Accessories', 'Personal Accessories',
    'Tools and Small Equipment', 'Baby Supplies', 'Pet Care',
    'Electronics and Accessories', 'Personal Security Supplies',
    'Household Equipment and Furnishings', 'Veterinary Care',
)


def install_item_categories(session):
    """Add only the ordinary-item chart nodes; caller owns commit/rollback.

    Existing names must have precisely the expected hierarchy and cash-flow
    treatment. Never reparent existing accounts or imply tax deductibility.
    """
    from pacioli.models import Elements, Classifications, Accounts, Subaccounts
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_item_categories'))"))
    element = session.query(Elements).filter_by(name='Expenses').one_or_none()
    if element is None:
        raise ValueError('The Expenses chart element must exist before adding item categories')
    classification_name = 'Ordinary Personal Purchases'
    classification = session.query(Classifications).populate_existing().filter_by(
        name=classification_name).one_or_none()
    if classification and classification.parent != 'Expenses':
        raise ValueError('Item category classification has a conflicting chart structure')
    additions = []
    for name in ITEM_CATEGORY_NAMES:
        parent = name + ' Expense'
        account = session.query(Accounts).populate_existing().filter_by(name=parent).one_or_none()
        subaccount = session.query(Subaccounts).populate_existing().filter_by(name=name).one_or_none()
        if account and (account.parent != classification_name or account.is_cash is not False
                        or account.cash_source != 'Operating'):
            raise ValueError('Item category account has conflicting chart or cash-flow metadata: ' + parent)
        if subaccount and subaccount.parent != parent:
            raise ValueError('Item category subaccount has a conflicting parent: ' + name)
        additions.append((name, parent, account, subaccount))
    # Validate every collision before adding anything to the caller's session.
    created = []
    if classification is None:
        session.add(Classifications(name=classification_name, parent='Expenses'))
        session.flush()
    for name, parent, account, subaccount in additions:
        if account is None:
            session.add(Accounts(name=parent, parent=classification_name,
                                 cash_source='Operating', is_cash=False))
            session.flush()
        if subaccount is None:
            session.add(Subaccounts(name=name, parent=parent,
                                    description=advisor.CATEGORY_DEFINITIONS[name]))
            created.append(name)
    session.flush()
    return created


def _carry_forward(settlement, bundle, categories):
    """Recover decisions proven by the current posted allocation, never a model.

    Immutable item decisions must reconstruct the complete current allocation.
    Changed source occurrences and superseded unposted reviews cannot inherit an
    approval merely because a title, ASIN or category happens to match.
    """
    entries = db.session.query(JournalEntries).populate_existing().filter_by(
        transaction_source='ofx', transaction_id=bundle.transaction_id).order_by(
            JournalEntries.allocation_key).all()
    allocation_document, preview = allocation._current_proof(bundle, entries)
    proven = []
    for document in db.session.query(AccountingEvidence).filter_by(kind='amazon_item_allocation'):
        body = json.loads(document.payload)
        if body.get('bundle_id') == bundle.id and body.get('posting_digest') == bundle.posting_digest:
            if digest(body) != document.sha256 or body.get('record_key') != settlement['record_key']:
                raise ValueError('Posted item review evidence changed')
            proven.append((document, body))
    if len(proven) != 1:
        raise ValueError('A refreshed partial allocation requires one original item-review proof')
    item_document, proof = proven[0]
    old_run = db.session.get(AmazonItemRuns, proof['run_id'], populate_existing=True)
    if not old_run or old_run.input_digest != proof['manifest_digest']:
        raise ValueError('The original posted item manifest changed')
    old_manifest = _manifest(old_run)
    old_settlements = [s for s in old_manifest['projection']['settlements']
                       if s['record_key'] == settlement['record_key']]
    if (len(old_settlements) != 1 or digest(_stable_projection(old_settlements[0])) !=
            digest(_stable_projection(settlement))):
        raise ValueError('Posted item occurrences differ from the refreshed source evidence')
    decisions = {}
    old_items = {item['item_id']: item for item in old_settlements[0]['items']}
    for identifier in proof['decisions']:
        decision = _document(identifier, 'amazon_item_decision')
        item = old_items.get(decision.get('item_id'))
        job = db.session.get(AmazonItemJobs, decision.get('job_id'), populate_existing=True)
        if (not item or item['item_id'] in decisions or not job
                or decision.get('run_id') != old_run.id or job.run_id != old_run.id
                or job.item_id != item['item_id'] or decision.get('item_digest') != digest(item)
                or decision.get('manifest_digest') != old_run.input_digest
                or (decision.get('category') is not None and
                    decision['category'] not in old_manifest['categories'])):
            raise ValueError('Original item decision does not belong to the posted source')
        decisions[item['item_id']] = (identifier, decision, job.decision_id == identifier)
    amounts = defaultdict(lambda: Decimal('0'))
    for item in old_items.values():
        prior = decisions.get(item['item_id'])
        category = prior[1]['category'] if prior else None
        amounts[category or 'Bookkeeping Suspense'] += money(item['amount'])
    expected = {a['category']: money(a['amount']) for a in preview['allocations']}
    if len(expected) != len(preview['allocations']) or {k: v for k, v in amounts.items() if v} != expected:
        raise ValueError('Original item decisions do not reproduce the posted allocation')
    carried = {}
    for item in settlement['items']:
        prior = decisions.get(item['item_id'])
        if (not prior or not prior[2] or prior[1]['category'] is None or item['blockers']
                or prior[1]['category'] not in categories):
            continue
        identifier, decision, _ = prior
        carried[item['item_id']] = dict(category=decision['category'],
            source_decision_id=identifier, source_run_id=old_run.id,
            source_manifest_digest=old_run.input_digest, source_item_digest=digest(item),
            source_policy_digest=old_manifest['policy_digest'], source_reviewer=decision['reviewer'],
            source_reason=decision['reason'], item_allocation_evidence_id=item_document.id,
            allocation_evidence_id=allocation_document.id, posting_digest=bundle.posting_digest)
        if decision.get('suspense_control') is not None:
            carried[item['item_id']]['source_suspense_control'] = decision['suspense_control']
    return carried


def _assert_carried_review_current(decision):
    """An unposted withdrawal of an ancestor review invalidates its copies."""
    seen = set()
    while decision.get('carry_forward'):
        origin = decision['carry_forward']
        identifier = origin['source_decision_id']
        if identifier in seen or len(seen) >= 1000:
            raise ValueError('Carried review ancestry is cyclic or too long')
        seen.add(identifier)
        prior = _document(identifier, 'amazon_item_decision')
        job = db.session.query(AmazonItemJobs).populate_existing().filter_by(
            id=prior['job_id']).with_for_update().one_or_none()
        if (not job or job.decision_id != identifier or job.run_id != origin['source_run_id']
                or prior['run_id'] != origin['source_run_id'] or job.item_id != decision['item_id']
                or prior['item_id'] != decision['item_id'] or prior['category'] != decision['category']
                or prior['item_digest'] != decision['item_digest']
                or prior['manifest_digest'] != origin['source_manifest_digest']):
            raise ValueError('A previously carried item review changed; inspect the source again')
        decision = prior


def create_run(archive_id, reviewer, think=False):
    if not reviewer or type(think) is not bool:
        raise ValueError('A reviewer and explicit model mode are required')
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_item_runs'))"))
    projection = build_projection(db.session, archive_id)
    # A new policy must be able to revisit the residual items in our own valid
    # partial allocations. Other/manual bundles remain outside this workflow.
    partial_ids = {row.transaction_id for row in db.session.query(JournalEntries).filter(
        JournalEntries.transaction_source == 'ofx',
        (JournalEntries.debit_subaccount == 'Bookkeeping Suspense') |
        (JournalEntries.credit_subaccount == 'Bookkeeping Suspense'))}
    owned = {b.transaction_id: b for b in db.session.query(AmazonEvidenceBundles).populate_existing().filter(
        AmazonEvidenceBundles.transaction_id.in_(partial_ids),
        AmazonEvidenceBundles.external_id.like('archive-item:%'))} if partial_ids else {}
    if owned:
        report = build_report(db.session, archive_id)
        _, tables = _source_tables(db.session, archive_id)
        refresh = []
        for record in report['records']:
            if record['status'] != 'already_booked' or record['kind'] != 'retail_purchase' or len(record['candidates']) != 1:
                continue
            bundle = owned.get(record['candidates'][0]['id'])
            expected = _external(dict(projection=projection), record['key'])
            if bundle and bundle.external_id == expected:
                refresh.append(record)
        projection['settlements'].extend(project_records(refresh, tables))
        all_items = [i for s in projection['settlements'] for i in s['items']]
        projection['summary'].update(settlements=len(projection['settlements']), items=len(all_items),
            amount=str(sum((money(s['amount']) for s in projection['settlements']), Decimal('0'))),
            blocked_items=sum(bool(i['blockers']) for i in all_items),
            blockers=dict(Counter(b for i in all_items for b in i['blockers'])))
    if not projection['settlements']:
        raise ValueError('No new unique retail settlements are ready for item classification')
    chart = chart_accounts(db.session)
    categories = sorted(name for name, (element, cash) in chart.items()
                        if element == 'Expenses' and name in advisor.CATEGORY_DEFINITIONS)
    if not categories:
        raise ValueError('No supported item categories exist in the chart')
    carried, baseline = {}, {}
    for settlement in projection['settlements']:
        if settlement['source_record']['status'] != 'already_booked':
            continue
        source = settlement['source_record']['candidates'][0]
        bundle = owned[source['id']]
        carried.update(_carry_forward(settlement, bundle, categories))
        baseline[bundle.external_id] = bundle.posting_digest
    body = json.loads(dump(dict(version=VERSION, projection=projection, chart=chart,
        categories=categories, policy_digest=_policy(), think=think,
        model=advisor.PROFILES['text14'][:2])))
    if baseline:
        body.update(carried_decisions=carried, baseline_postings=baseline)
    key = digest(body)
    old = db.session.query(AmazonItemRuns).filter_by(input_digest=key).one_or_none()
    if old:
        return old
    doc = evidence('amazon_item_manifest', body, reviewer)
    run = AmazonItemRuns(manifest_id=doc.id, input_digest=key, state='queued',
                        progress='Waiting for the local model', reviewer=reviewer)
    db.session.add(run)
    db.session.flush()
    for settlement in projection['settlements']:
        for item in settlement['items']:
            job = AmazonItemJobs(run_id=run.id, item_id=item['item_id'],
                                 record_key=settlement['record_key'], state='queued')
            db.session.add(job)
            if item['item_id'] in carried:
                db.session.flush()
                previous = carried[item['item_id']]
                decision = dict(run_id=run.id, job_id=job.id, item_id=job.item_id,
                    category=previous['category'], reason='Preserved the exact previously reviewed and posted item allocation.',
                    reviewer=reviewer, manifest_digest=run.input_digest, item_digest=digest(item),
                    proposal=None, proposal_digest=None, previous_decision_id=None,
                    carry_forward=previous)
                if previous.get('source_suspense_control') is not None:
                    decision['suspense_control'] = previous['source_suspense_control']
                doc = evidence('amazon_item_decision', decision, reviewer)
                job.decision_id = doc.id
                job.state = 'held'  # Reviewed evidence, with no claimed model response.
    db.session.flush()
    return run


def run_status(run_id):
    run = db.session.get(AmazonItemRuns, run_id)
    if not run:
        raise ValueError('Missing item run')
    rows = db.session.query(AmazonItemJobs.state, func.count(AmazonItemJobs.id),
        func.sum(case((AmazonItemJobs.decision_id.isnot(None), 1), else_=0))).filter_by(
            run_id=run_id).group_by(AmazonItemJobs.state).all()
    counts = {state: int(count) for state, count, reviewed in rows}
    counts.update(total=sum(int(count) for _,count,_ in rows),
                  reviewed=sum(int(reviewed) for _,_,reviewed in rows))
    return dict(id=run.id, state=run.state, progress=run.progress, counts=counts,
                revision=run.updated_at.isoformat() if run.updated_at else '')


def run_worker(run_id, infer_fn=None):
    """No financial writes. OS lease survives DB commits and dies with process."""
    permanent_request_codes = frozenset((
        'invalid_item', 'invalid_item_identity_or_title', 'invalid_item_context',
        'invalid_item_quantity', 'invalid_item_blockers', 'invalid_item_evidence_refs',
        'missing_title_reference', 'item_exceeds_byte_budget', 'messages_exceed_byte_budget',
    ))
    path = Path('/run/user') / str(os.getuid()) / ('pacioli-amazon-items-{}.lock'.format(int(run_id)))
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    active_job_id = None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        run = db.session.get(AmazonItemRuns, run_id)
        if not run:
            raise ValueError('Missing item run')
        manifest = _manifest(run)
        if manifest['policy_digest'] != _policy():
            raise ValueError('Item policy changed; prepare a new run')
        if manifest['model'] != list(advisor.PROFILES['text14'][:2]):
            raise ValueError('The pinned model changed; prepare a new run')
        items = _items(manifest)
        jobs = _jobs(run_id)
        pending = []
        for job in jobs:
            manual_pending = job.state == 'held' and job.proposal is None
            if (job.state not in ('queued', 'running', 'failed') and not manual_pending) or job.decision_id is not None:
                continue
            saved = json.loads(job.proposal) if job.state == 'failed' and job.proposal else {}
            if (isinstance(saved, dict) and saved.get('failure_phase') == 'request_validation'
                    and saved.get('failure_code') in permanent_request_codes and saved.get('retryable') is False):
                continue
            pending.append(job.id)
        if not pending:
            failures = sum(job.state == 'failed' and job.decision_id is None for job in jobs)
            run.state = 'needs_attention' if failures else 'completed'
            run.progress = ('{} item requests need source correction or independent review'.format(failures)
                            if failures else 'All item proposals are ready for review')
            run.updated_at = _now(); db.session.commit(); return
        run.state = 'running'; run.progress = 'Checking local model availability'
        run.updated_at = _now(); db.session.commit()
        from pacioli.functions.chase_local_model import LocalModel
        model_name, model_sha, minimum = advisor.PROFILES['text14']
        last = [0.0]
        current = [0]

        def progress(stage, **details):
            if time.monotonic() - last[0] < 1 and stage == 'model_output':
                return
            last[0] = time.monotonic()
            message = 'Item {} of {}: {}'.format(current[0], len(pending), stage.replace('_', ' '))
            if stage == 'model_output':
                message += ' ({} chunks; {} response characters)'.format(
                    details.get('chunks', 0), details.get('characters', 0))
            db.session.query(AmazonItemRuns).filter_by(id=run_id).update(
                dict(progress=message[:300], updated_at=_now()), synchronize_session=False)
            db.session.commit()

        # A reviewed exact-product rule is a proposal, never a carried approval.
        # Open the GPU runtime only when at least one item actually needs it.
        from pacioli.functions.amazon_suspense import product_suggestion
        with ExitStack() as resources:
            model = None
            for index, job_id in enumerate(pending, 1):
                current[0] = index
                job = db.session.query(AmazonItemJobs).populate_existing().filter_by(id=job_id).with_for_update().one()
                if job.decision_id is not None:
                    db.session.commit()
                    continue
                active_job_id = job_id
                job.state = 'running'; job.updated_at = _now(); db.session.commit()
                progress('preparing_evidence')
                item = model_item(items[job.item_id])
                # This pure preflight is the only per-item exception boundary.
                # Never classify a network/GPU/runtime ValueError as bad input.
                try:
                    advisor.request_for(item, manifest['categories'], think=manifest['think'])
                except ValueError as exc:
                    code = str(exc)
                    if code not in permanent_request_codes:
                        raise
                    job.proposal = dump(dict(answer=None, category=None,
                        validation_errors=['request_' + code], failure_phase='request_validation',
                        failure_code=code, retryable=False, policy_version=advisor.POLICY_VERSION))
                    job.state = 'failed'; job.updated_at = _now(); db.session.commit()
                    active_job_id = None
                    progress('source_request_needs_review')
                    continue
                proposal = product_suggestion(items[job.item_id], manifest['categories'])
                if proposal is None:
                    if infer_fn:
                        proposal = infer_fn(item, manifest['categories'])
                    else:
                        if model is None:
                            model = resources.enter_context(LocalModel(model=model_name,
                                digest=model_sha, minimum_free_mib=minimum, report=progress))
                        proposal = advisor.infer(model, item, manifest['categories'], think=manifest['think'])
                # Validate independently of the transport, including injected test transports.
                errors = list(proposal.get('validation_errors', []))
                errors += advisor.validate(proposal.get('answer'), item, manifest['categories'])
                proposal['validation_errors'] = sorted(set(errors))
                job.proposal = dump(proposal)
                job.state = ('failed' if errors else 'suggested' if
                             proposal['answer']['decision'] == 'suggest' else 'held')
                job.updated_at = _now(); db.session.commit()
                active_job_id = None
                progress('validated_' + job.state)
        run = db.session.get(AmazonItemRuns, run_id)
        failures = sum(j.state == 'failed' and j.decision_id is None for j in _jobs(run_id))
        run.state = 'needs_attention' if failures else 'completed'
        run.progress = ('{} item responses need retry or review'.format(failures) if failures else
                        'All item proposals are ready for review')
        run.updated_at = _now(); db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if active_job_id is not None:
            stopped = db.session.query(AmazonItemJobs).populate_existing().filter_by(
                id=active_job_id).with_for_update().one_or_none()
            if stopped and stopped.state == 'running' and stopped.decision_id is None:
                stopped.state = 'failed'
                stopped.proposal = dump(dict(answer=None, category=None,
                    validation_errors=['worker_runtime_failure'], failure_phase='runtime',
                    failure_code=type(exc).__name__, retryable=True))
                stopped.updated_at = _now()
        run = db.session.get(AmazonItemRuns, run_id)
        if run:
            run.state = 'needs_attention'
            run.progress = 'Item worker stopped ({}). Saved proposals are retained; retry incomplete items.'.format(type(exc).__name__)
            run.updated_at = _now(); db.session.commit()
        raise
    finally:
        os.close(fd)


def review_item(job_id, category, reason, reviewer):
    if not reviewer or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('Supply the evidence supporting this item decision')
    job = db.session.query(AmazonItemJobs).populate_existing().filter_by(id=job_id).with_for_update().one_or_none()
    if not job:
        raise ValueError('Missing item')
    if job.state in ('queued', 'running'):
        raise ValueError('Wait for this item proposal before reviewing it')
    run = db.session.get(AmazonItemRuns, job.run_id)
    manifest = _manifest(run)
    item = _items(manifest)[job.item_id]
    category = category or None
    if category is not None and (category not in manifest['categories'] or item['blockers']):
        raise ValueError('The category or item evidence is not eligible')
    if manifest['policy_digest'] != _policy():
        raise ValueError('Item policy changed; prepare a fresh review')
    from pacioli.functions.amazon_suspense import review_control
    control = review_control(item, category, run.id)
    previous = _decision(job)
    if (previous and previous['category'] == category and previous['reason'] == reason.strip()
            and previous.get('suspense_control') == control):
        return db.session.get(AccountingEvidence, job.decision_id)
    body = dict(run_id=run.id, job_id=job.id, item_id=job.item_id, category=category,
        reason=reason.strip(), reviewer=reviewer, manifest_digest=run.input_digest,
        item_digest=digest(item), proposal_digest=digest(json.loads(job.proposal)) if job.proposal else None,
        proposal=json.loads(job.proposal) if job.proposal else None,
        previous_decision_id=job.decision_id)
    if control is not None:
        body['suspense_control'] = control
    doc = evidence('amazon_item_decision', body, reviewer)
    job.decision_id = doc.id; job.updated_at = _now()
    db.session.flush()
    return doc


def review_digest(run_id):
    run = db.session.get(AmazonItemRuns, run_id)
    if not run:
        raise ValueError('Missing item run')
    manifest = _manifest(run)
    bundles = {b.external_id: b.posting_digest for b in db.session.query(AmazonEvidenceBundles).populate_existing()
               .filter(AmazonEvidenceBundles.external_id.in_(
                   [_external(manifest, s['record_key']) for s in manifest['projection']['settlements']]))}
    approval = dict(manifest=run.input_digest,
                    decisions=[(j.id, j.decision_id) for j in _jobs(run_id)], bundles=bundles)
    from pacioli.functions.amazon_suspense import review_control_revision
    controls = review_control_revision(list(_items(manifest)))
    if controls:
        approval['suspense_controls'] = controls
    return digest(approval)


def _stable_projection(settlement):
    return {k: v for k, v in settlement.items() if k != 'source_record'}


def post_reviewed(run_id, expected_digest, reviewer, reason, report=None):
    """Post one approved batch atomically, even if its caller catches a failure.

    The savepoint includes derived-trigger DDL, journals, balances and immutable
    posting evidence. Releasing it never commits the caller's transaction.
    """
    with db.session.begin_nested():
        return _post_reviewed(run_id, expected_digest, reviewer, reason, report)


def _post_reviewed(run_id, expected_digest, reviewer, reason, report):
    """Unknown items remain exactly in suspense; callers use post_reviewed."""
    if report is not None:
        report('waiting_for_ledger', processed=0, total=0)
    # Use the workbench's lock order before taking any ledger/run/job lock.
    # Facts or a newly imported eligibility plan must not change between the
    # frozen approval check and the financial commit.
    from pacioli.functions.amazon_suspense import _lock as lock_suspense_reviews
    lock_suspense_reviews()
    lock_ledger(reviewer, reason)
    trigger_state = db.session.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
        "'bookkeeping.journal_entries'::regclass AND tgname='subaccount_insert_trigger'")).scalar()
    if trigger_state != 'O':
        raise ValueError('Trial-balance trigger must be enabled before item posting')
    db.session.execute(text('LOCK TABLE ofx.stmttrn, ofx.acctfrom, ofx.ccacctfrom, ofx.bankacctfrom, '
                            'bookkeeping.bank_feed_accounts, bookkeeping.subaccounts, bookkeeping.accounts, '
                            'bookkeeping.classifications, bookkeeping.elements IN SHARE MODE'))
    run = db.session.query(AmazonItemRuns).populate_existing().filter_by(id=run_id).with_for_update().one_or_none()
    if not run or run.state in ('queued', 'running'):
        raise ValueError('Complete the item run before posting reviewed allocations')
    # Locks also serialize review edits with this frozen decision selection.
    db.session.query(AmazonItemJobs).populate_existing().filter_by(run_id=run_id).with_for_update().all()
    current_digest = review_digest(run_id)
    if current_digest != expected_digest:
        replay = False
        for doc in db.session.query(AccountingEvidence).filter_by(kind='amazon_item_batch'):
            body = json.loads(doc.payload)
            if (digest(body) == doc.sha256 and body['run_id'] == run_id and
                    body['before_digest'] == expected_digest and body['after_digest'] == current_digest):
                replay = True
                break
        if not replay:
            raise ValueError('Item decisions or current allocations changed; reload the review')
    manifest = _manifest(run)
    if (manifest['policy_digest'] != _policy() or
            digest(chart_accounts(db.session)) != digest(manifest['chart'])):
        raise ValueError('The category chart or policy changed since item review')
    total_settlements = len(manifest['projection']['settlements'])
    if report is not None:
        report('checking_sources', processed=0, total=total_settlements)
    settlement_report = build_report(db.session, manifest['projection']['archive_id'])
    archive, tables = _source_tables(db.session, manifest['projection']['archive_id'])
    if archive['source_sha256'] != manifest['projection']['archive_digest']:
        raise ValueError('Source archive changed')
    records = {r['key']: r for r in settlement_report['records']}
    wanted = {s['record_key'] for s in manifest['projection']['settlements']}
    projected = {s['record_key']: s for s in project_records(
        [r for r in settlement_report['records'] if r['key'] in wanted], tables)}
    jobs = {j.item_id: j for j in _jobs(run_id)}
    result = dict(posted=0, revised=0, unchanged=0, held_settlements=0,
                  classified_amount='0.00', suspense_amount='0.00', bundles=[])
    classified = suspense = Decimal('0')
    balances_deferred = False
    def progress(stage, processed):
        if report is not None:
            report(stage, processed=processed, total=total_settlements,
                   prepared_new=result['posted'], prepared_revisions=result['revised'],
                   unchanged=result['unchanged'], held=result['held_settlements'])
    for settlement_index, original in enumerate(manifest['projection']['settlements'], 1):
        progress('preparing_settlement', settlement_index - 1)
        allocations = defaultdict(lambda: Decimal('0'))
        decisions = []
        if original['source_record']['status'] == 'already_booked' and any(
                jobs[i['item_id']].decision_id is None for i in original['items']):
            # A refresh must never turn absence of a new decision into an
            # implicit withdrawal of an earlier supported classification.
            if any(jobs[i['item_id']].decision_id is not None for i in original['items']):
                raise ValueError('Review every item in a refreshed posted settlement before revising its allocation')
            result['held_settlements'] += 1
            progress('settlement_ready', settlement_index)
            continue
        for item in original['items']:
            job = jobs[item['item_id']]
            decision = _decision(job)
            category = decision['category'] if decision else None
            if decision and (decision['manifest_digest'] != run.input_digest or
                             decision['item_digest'] != digest(item)):
                raise ValueError('Reviewed item evidence changed')
            if decision:
                _assert_carried_review_current(decision)
            if category is not None and (category not in manifest['categories'] or item['blockers']):
                raise ValueError('Reviewed category is no longer eligible')
            if category is not None:
                from pacioli.functions.amazon_suspense import review_control
                review_control(item, category, run_id, decision=decision)
            if money(item['amount']) < 0:
                raise ValueError('Negative item attribution requires separate review')
            allocations[category or 'Bookkeeping Suspense'] += money(item['amount'])
            if decision:
                decisions.append(decision['evidence_id'])
        allocations = {k: v for k, v in allocations.items() if v}
        external = _external(manifest, original['record_key'])
        bundle = db.session.query(AmazonEvidenceBundles).populate_existing().filter_by(external_id=external).one_or_none()
        baseline = manifest.get('baseline_postings', {}).get(external)
        if baseline and (not bundle or bundle.posting_digest != baseline):
            # After this run posts, its own immutable item-allocation proof
            # permits later review/replay. Another writer's intervening revision
            # cannot silently inherit approvals carried from an older version.
            own_proof = False
            for document in db.session.query(AccountingEvidence).filter_by(kind='amazon_item_allocation'):
                body = json.loads(document.payload)
                if (bundle and body.get('bundle_id') == bundle.id
                        and body.get('posting_digest') == bundle.posting_digest
                        and body.get('run_id') == run_id and body.get('record_key') == original['record_key']
                        and body.get('manifest_digest') == run.input_digest and digest(body) == document.sha256):
                    own_proof = True
                    break
            if not own_proof:
                raise ValueError('The partial allocation changed since its approved items were carried forward')
        if not any(k != 'Bookkeeping Suspense' for k in allocations):
            if bundle and bundle.posting_digest:
                raise ValueError('Returning an entire posted settlement to suspense requires a separate reviewed reversal')
            result['held_settlements'] += 1
            progress('settlement_ready', settlement_index)
            continue
        record = records.get(original['record_key'])
        if not record or record['status'] not in ('unique_candidate', 'already_booked'):
            raise ValueError('Settlement is no longer a unique eligible match')
        fresh = projected[original['record_key']]
        if digest(_stable_projection(fresh)) != digest(_stable_projection(original)):
            raise ValueError('Item or cost evidence changed since review')
        old_source = original['source_record']['candidates'][0]
        if len(record['candidates']) != 1:
            raise ValueError('Settlement candidate changed')
        live_source = record['candidates'][0]
        for key in ('id', 'source_timestamp', 'amount', 'account', 'account_id', 'currency', 'card_last4', 'description'):
            if live_source.get(key) != old_source.get(key):
                raise ValueError('Bank source changed since item review')
        if record['status'] == 'already_booked' and (not bundle or bundle.transaction_id != live_source['id']):
            raise ValueError('Another reviewed bundle already owns this settlement')
        if bundle is None:
            stream = io.StringIO(newline=''); writer = csv.writer(stream)
            writer.writerow(allocation.FIELDS)
            for line_index, line in enumerate(record['lines']):
                writer.writerow([external, str(line_index), original['order_id'], original['date'],
                                 line['kind'], line['title'], line['amount'], original['currency']])
            bundle = allocation.import_items_csv(stream.getvalue().encode(), reviewer)[0]
        pairs = [dict(category=k, amount=str(v)) for k, v in sorted(allocations.items())]
        preview = allocation.allocation_preview(bundle.id, live_source['id'], pairs,
                                               allow_suspense='Bookkeeping Suspense' in allocations)
        if not balances_deferred:
            # Historical DELETE/INSERT splits otherwise recalculate every later
            # period for every journal row. Defer only this derived projection;
            # audit, closed-period, source and currency controls remain active.
            # The enclosing savepoint restores the DDL as well as financial
            # writes if any validation, progress callback or rebuild fails.
            db.session.execute(text('ALTER TABLE bookkeeping.journal_entries '
                                    'DISABLE TRIGGER subaccount_insert_trigger'))
            balances_deferred = True
        previous = bundle.posting_digest
        if previous:
            allocation.revise_allocation(preview, reviewer, reason, expected_posting_digest=previous)
            outcome = 'unchanged' if bundle.posting_digest == previous else 'revised'
        else:
            allocation.book_allocation(preview, reviewer, reason)
            outcome = 'posted'
        if outcome != 'unchanged':
            evidence('amazon_item_allocation', dict(run_id=run_id, record_key=original['record_key'],
                bundle_id=bundle.id, posting_digest=bundle.posting_digest, decisions=decisions,
                manifest_digest=run.input_digest, reason=reason), reviewer)
        result[outcome] += 1; result['bundles'].append(bundle.id)
        suspense += allocations.get('Bookkeeping Suspense', Decimal('0'))
        classified += sum(v for k,v in allocations.items() if k != 'Bookkeeping Suspense')
        progress('settlement_ready', settlement_index)
    if result['posted'] or result['revised']:
        progress('rebuilding_balances', total_settlements)
        from pacioli.functions.mapping_workflow import rebuild_trial_balances
        rebuild_trial_balances()
    if balances_deferred:
        db.session.execute(text('ALTER TABLE bookkeeping.journal_entries '
                                'ENABLE TRIGGER subaccount_insert_trigger'))
    if result['posted'] or result['revised']:
        from pacioli.functions.accounting_report import trial_balance_differences
        if trial_balance_differences(db.session):
            raise ValueError('Rebuilt trial balances failed independent validation')
    result.update(classified_amount=str(classified), suspense_amount=str(suspense))
    db.session.flush()
    if result['posted'] or result['revised']:
        evidence('amazon_item_batch', dict(run_id=run_id, before_digest=expected_digest,
                 after_digest=review_digest(run_id), result=result, reason=reason), reviewer)
    progress('ready_to_commit', total_settlements)
    return result


def page(run_id=None, page=1):
    try:
        page = int(page)
        if page < 1:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Invalid item page')
    runs = db.session.query(AmazonItemRuns).order_by(AmazonItemRuns.id.desc()).limit(20).all()
    run = db.session.get(AmazonItemRuns, int(run_id)) if run_id is not None else (runs[0] if runs else None)
    if run_id is not None and not run:
        raise ValueError('Missing item run')
    archives = [dict(id=d.id, filename=json.loads(d.payload)['filename']) for d in
                db.session.query(AccountingEvidence).filter_by(kind='amazon_csv_import').order_by(AccountingEvidence.id.desc()).limit(20)]
    result = dict(run=None, runs=runs, rows=[], categories=[], page=page, pages=1, total=0, archives=archives)
    if not run:
        return result
    manifest = _manifest(run); items = _items(manifest); jobs = _jobs(run.id)
    settlements = {s['record_key']: s for s in manifest['projection']['settlements']}
    pages = max(1, (len(jobs) + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        raise ValueError('Invalid item page')
    data = run_status(run.id)
    data.update(manifest_id=run.manifest_id, archive_id=manifest['projection']['archive_id'], review_digest=review_digest(run.id))
    decision_map = {j.item_id: _decision(j) for j in jobs}
    totals = defaultdict(lambda: Decimal('0'))
    selected = held = 0
    for settlement in settlements.values():
        costs = defaultdict(lambda: Decimal('0'))
        for item in settlement['items']:
            decision = decision_map[item['item_id']]
            category = decision['category'] if decision else None
            costs[category or 'Bookkeeping Suspense'] += money(item['amount'])
        if not any(k != 'Bookkeeping Suspense' and v for k, v in costs.items()):
            held += 1
        else:
            selected += 1
            for k, v in costs.items():
                totals[k] += v
    data['posting_summary'] = dict(settlements=selected, held_settlements=held,
        classified_amount=str(sum(v for k,v in totals.items() if k != 'Bookkeeping Suspense')),
        suspense_amount=str(totals.get('Bookkeeping Suspense', Decimal('0'))),
        categories=[dict(category=k, amount=str(v)) for k,v in sorted(totals.items()) if v])
    rows = []
    for job in jobs[(page-1)*PAGE_SIZE:page*PAGE_SIZE]:
        bundle = db.session.query(AmazonEvidenceBundles).filter_by(external_id=_external(manifest, job.record_key)).one_or_none()
        rows.append(dict(id=job.id, item=items[job.item_id], record_key=job.record_key, state=job.state,
                         settlement=settlements[job.record_key],
                         proposal=json.loads(job.proposal) if job.proposal else None, decision=_decision(job),
                         posted=bool(bundle and bundle.posting_digest), bundle_id=bundle.id if bundle else None))
    result.update(run=data, rows=rows, categories=manifest['categories'], pages=pages, total=len(jobs))
    return result
