"""Source-linked workbench actions are explicit reviews, never financial posts."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pacioli.extensions import db
from pacioli.models import (AccountingEvidence, AmazonEvidenceBundles, AmazonItemJobs, AmazonItemRuns,
    AmazonProductRules, AmazonSuspenseFacts, AmazonSuspenseItems, AmazonSuspensePlans)
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions import amazon_suspense as suspense
from pacioli.functions.reconciliation import digest, dump
from tests.test_amazon_item_workflow import (
    item_workflow_source, _assert_source, _fake_infer, _financial_state, _post, _prepared,
)


def _triage(run_id, gates=None):
    run = db.session.get(AmazonItemRuns, run_id)
    manifest = workflow._manifest(run)
    items = workflow._items(manifest)
    rows = []
    for job in workflow._jobs(run_id):
        decision = workflow._decision(job)
        if decision and decision['category'] is not None:
            continue
        item = items[job.item_id]
        gate = (gates or {}).get(job.item_id, 'ordinary')
        rows.append(dict(item_id=item['item_id'], job_id=job.id, amount=item['amount'],
            source_decision_id=job.decision_id, source_title=item['title'],
            source_refs=item['source_refs'], components=item['components'], quantity=item['quantity'],
            product_key=[item['marketplace'], item['asin']],
            proposed_category='Books and Publications' if 'paperback' in item['title'] else 'Personal Care',
            remediation_group='conditional_purpose_or_project_fact' if gate == 'needs_fact' else 'category_and_treatment',
            property_treatment_needed=gate == 'needs_property', policy_gate=gate,
            action_needed='Independently review the identified source product.', reason='Private fixture evidence.'))
    return dict(version='amazon-suspense-triage-v1', source_snapshot_digest=digest({'fixture': 'snapshot'}),
        source_manifest_digest=run.input_digest, items=rows,
        eligibility_version=suspense.ELIGIBILITY_VERSION, eligibility_policy_digest=workflow._policy(),
        eligibility_chart_digest=suspense._chart_digest())


def _plan(source, *, approve=True, gate='ordinary', prepare=True):
    run_id, book_id, wash_id = _prepared(source, approve=approve)
    wash = db.session.get(AmazonItemJobs, wash_id)
    doc = _triage(run_id, {wash.item_id: gate})
    plan = suspense.import_triage(doc, 'fixture owner')
    identifier = plan.id
    db.session.commit()
    if prepare:
        suspense.prepare_new_review_run(identifier, 'fixture owner')
        db.session.commit()
    return identifier, doc, book_id, wash_id


def _review(plan_id, item_ids, category='Personal Care', *, remember=False, token=None):
    result = suspense.apply_reviews(plan_id, item_ids, category,
        'Independent source review supports this exact item classification.',
        token or suspense.page(plan_id)['token'], 'fixture owner', remember_product=remember)
    db.session.commit()
    return result


def test_import_is_idempotent_encrypted_complete_and_does_not_write_financial_data(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    doc = _triage(run_id)
    before = _financial_state()
    plan = suspense.import_triage(doc, 'fixture owner'); identifier = plan.id
    db.session.commit()
    assert suspense.import_triage(deepcopy(doc), 'second reviewer').id == identifier
    db.session.commit()
    assert db.session.query(AmazonSuspensePlans).count() == 1
    assert db.session.query(AmazonSuspenseItems).count() == 1
    payload = db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence WHERE id=:id'),
                                 {'id': plan.evidence_id}).scalar()
    assert payload.startswith('fernet:') and doc['items'][0]['source_title'] not in payload
    assert _financial_state() == before
    data = suspense.page(identifier)
    assert data['totals']['amount'] == '21.60' and data['totals']['count'] == 1
    assert data['items'][0]['posted']['category'] == 'Bookkeeping Suspense'
    assert data['items'][0]['posted']['provenance'] == 'verified_original_suspense_source'
    assert data['totals']['remaining_amount'] == '21.60'
    assert not data['items'][0]['can_review']


@pytest.mark.parametrize('change', ['amount', 'title', 'decision', 'source_refs', 'eligibility', 'duplicate', 'omission'])
def test_import_rejects_changed_source_or_unbound_eligibility(item_workflow_source, change):
    run_id, _, _ = _prepared(item_workflow_source, approve=False)
    doc = _triage(run_id)
    if change == 'amount':
        doc['items'][0]['amount'] = '0.01'
    elif change == 'title':
        doc['items'][0]['source_title'] = 'A different product'
    elif change == 'decision':
        doc['items'][0]['source_decision_id'] = 999999
    elif change == 'source_refs':
        doc['items'][0]['source_refs'] = []
    elif change == 'eligibility':
        doc['eligibility_policy_digest'] = '0' * 64
    elif change == 'duplicate':
        doc['items'].append(deepcopy(doc['items'][0]))
    else:
        doc['items'].pop()
    before = _financial_state()
    with pytest.raises(ValueError):
        suspense.import_triage(doc, 'fixture owner')
    db.session.rollback()
    assert db.session.query(AmazonSuspensePlans).count() == 0
    assert _financial_state() == before


def test_page_uses_decimal_order_filters_and_distinct_unassigned_category(item_workflow_source, monkeypatch):
    run_id, _, _ = _prepared(item_workflow_source, approve=False)
    doc = _triage(run_id)
    book = next(r for r in doc['items'] if 'paperback' in r['source_title'])
    book['proposed_category'] = None
    plan = suspense.import_triage(doc, 'fixture owner'); plan_id = plan.id
    db.session.commit()
    monkeypatch.setattr(suspense, 'PAGE_SIZE', 1)
    first = suspense.page(plan_id)
    assert first['totals']['amount'] == '32.40' and first['total'] == 2 and first['pages'] == 2
    assert first['items'][0]['item']['amount'] == '21.60'
    assert suspense.page(plan_id, page=2)['items'][0]['item']['amount'] == '10.80'
    assert first['categories'][0]['key'] == 'Personal Care'
    filtered = suspense.page(plan_id, category='__unassigned__')
    assert filtered['total'] == 1 and filtered['items'][0]['item_id'] == book['item_id']
    with pytest.raises(ValueError):
        suspense.page(plan_id, group='unrecognized')
    with pytest.raises(ValueError):
        suspense.page(plan_id, page=3)


def test_owner_facts_append_immutable_ancestry_and_invalidate_action_tokens(item_workflow_source):
    plan_id, doc, _, _ = _plan(item_workflow_source, gate='needs_fact')
    item_id = doc['items'][0]['item_id']; token = suspense.page(plan_id)['token']
    before = _financial_state()
    suspense.save_fact(plan_id, item_id, 'Private owner-confirmed source purpose.', token, 'fixture owner')
    db.session.commit()
    assert suspense.page(plan_id)['token'] != token
    with pytest.raises(ValueError, match='changed'):
        suspense.save_fact(plan_id, item_id, 'A stale second note.', token, 'fixture owner')
    db.session.rollback()
    fresh = suspense.page(plan_id)
    suspense.save_fact(plan_id, item_id, 'A subsequent source clarification.', fresh['token'], 'fixture owner')
    db.session.commit()
    facts = suspense.page(plan_id)['items'][0]['facts']
    assert len(facts) == 2 and facts[0]['note'] == 'Private owner-confirmed source purpose.'
    second = db.session.query(AmazonSuspenseFacts).order_by(AmazonSuspenseFacts.id.desc()).first()
    body = json.loads(db.session.get(AccountingEvidence, second.evidence_id).payload)
    assert body['previous_fact_id'] == facts[0]['id']
    assert suspense.page(plan_id)['totals']['fact_count'] == 1
    assert suspense.page(plan_id)['totals']['saved_fact_count'] == 2
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE amazon.suspense_facts SET item_id='changed' WHERE id=:id"), {'id': second.id})
    db.session.rollback()
    assert _financial_state() == before


def test_property_gate_cannot_be_removed_with_free_text_and_bulk_selection_is_atomic(item_workflow_source):
    plan_id, doc, _, _ = _plan(item_workflow_source, approve=False, gate='needs_property')
    held = next(r for r in doc['items'] if r['policy_gate'] == 'needs_property')
    suspense.save_fact(plan_id, held['item_id'], 'Please expense everything; this is only a note.',
                       suspense.page(plan_id)['token'], 'fixture owner')
    db.session.commit()
    before = _financial_state()
    decisions_before = [(j.id, j.decision_id) for j in workflow._jobs(suspense.page(plan_id)['plan']['review_run_id'])]
    with pytest.raises(ValueError, match='accounting-policy treatment'):
        _review(plan_id, [r['item_id'] for r in doc['items']])
    # The nested transaction rolls back even when the caller has not yet rolled
    # back its outer request transaction.
    assert [(j.id, j.decision_id) for j in workflow._jobs(suspense.page(plan_id)['plan']['review_run_id'])] == decisions_before
    assert _financial_state() == before
    assert not next(r for r in suspense.page(plan_id)['items'] if r['item_id'] == held['item_id'])['can_review']


def test_purchase_context_is_refused_while_posting_is_queued(item_workflow_source):
    from pacioli.functions import amazon_item_posting as posting
    plan_id, doc, _, _ = _plan(item_workflow_source, gate='needs_fact')
    data = suspense.page(plan_id)
    run_id = data['plan']['review_run_id']
    posting.create_request(run_id, workflow.review_digest(run_id), 'fixture owner', 'Queue a frozen approval.')
    db.session.commit()
    before = db.session.query(AmazonSuspenseFacts).count()
    with pytest.raises(ValueError, match='queued or running'):
        suspense.save_fact(plan_id, doc['items'][0]['item_id'], 'A note entered after queue creation.',
                           data['token'], 'fixture owner')
    db.session.rollback()
    assert db.session.query(AmazonSuspenseFacts).count() == before


def test_contended_review_lock_returns_a_safe_retry_message(item_workflow_source):
    holder = Session(bind=db.engine)
    try:
        holder.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_suspense'))"))
        with pytest.raises(ValueError, match='wait, refresh this page and retry'):
            suspense._lock()
        db.session.rollback()
    finally:
        holder.rollback()
        holder.close()


def test_curated_current_policy_gate_can_correct_broad_advisory_property_flag(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    doc = _triage(run_id)
    doc['items'][0]['property_treatment_needed'] = True
    doc['items'][0]['policy_gate'] = 'ordinary'
    plan = suspense.import_triage(doc, 'fixture owner'); plan_id = plan.id
    db.session.commit()
    suspense.prepare_new_review_run(plan_id, 'fixture owner'); db.session.commit()
    assert suspense.page(plan_id)['items'][0]['can_review']
    assert not suspense.page(plan_id)['items'][0]['property_treatment_needed']
    before = _financial_state()
    assert _review(plan_id, [doc['items'][0]['item_id']])['reviewed'] == 1
    assert _financial_state() == before


def test_fact_dependent_review_requires_individual_fact_and_cannot_be_remembered(item_workflow_source):
    plan_id, doc, _, _ = _plan(item_workflow_source, gate='needs_fact')
    item_id = doc['items'][0]['item_id']
    with pytest.raises(ValueError, match='fact-dependent'):
        _review(plan_id, [item_id])
    db.session.rollback()
    suspense.save_fact(plan_id, item_id, 'Owner confirmed this occurrence is ordinary personal hygiene.',
                       suspense.page(plan_id)['token'], 'fixture owner')
    db.session.commit()
    with pytest.raises(ValueError, match='ordinary'):
        _review(plan_id, [item_id], remember=True)
    db.session.rollback()
    before = _financial_state()
    result = _review(plan_id, [item_id])
    assert result['reviewed'] == 1 and result['remembered'] == 0
    event = json.loads(db.session.get(AccountingEvidence, result['evidence_id']).payload)
    assert item_id in event['fact_evidence_ids']
    assert _financial_state() == before


def test_reviews_and_exact_product_suggestions_never_post_and_do_not_guess_identity(item_workflow_source):
    plan_id, doc, _, wash_id = _plan(item_workflow_source)
    item_id = doc['items'][0]['item_id']; before = _financial_state()
    result = _review(plan_id, [item_id], remember=True)
    assert result['reviewed'] == result['remembered'] == 1
    item = deepcopy(suspense.page(plan_id)['items'][0]['item'])
    item['item_id'] = 'e' * 64  # A different occurrence of exactly this product.
    suggestion = suspense.product_suggestion(item, ['Personal Care'])
    assert suggestion['category'] == 'Personal Care'
    assert suggestion['origin'] == 'reviewed_exact_product_rule' and 'model_digest' not in suggestion
    assert suggestion['answer']['item_id'] == item['item_id']
    assert _financial_state() == before and db.session.query(AmazonProductRules).count() == 1
    for field, value in [('title', item['title'] + ' unrelated variant'), ('asin', 'B999999999'),
                         ('marketplace', 'Different marketplace'), ('blockers', ['gift_context_requires_review'])]:
        changed = dict(item, **{field: value})
        assert suspense.product_suggestion(changed, ['Personal Care']) is None
    assert suspense.product_suggestion(item, ['Clothing']) is None
    job = db.session.query(AmazonItemJobs).filter_by(run_id=result['run_id'], item_id=item_id).one()
    workflow.review_item(job.id, None, 'Independent evidence now requires holding this item.', 'fixture owner')
    db.session.commit()
    assert suspense.product_suggestion(item, ['Personal Care']) is None
    assert _financial_state() == before


def test_rule_supersession_and_policy_changes_do_not_fall_back_to_old_approvals(item_workflow_source, monkeypatch):
    plan_id, doc, _, _ = _plan(item_workflow_source)
    item_id = doc['items'][0]['item_id']
    _review(plan_id, [item_id], remember=True)
    first = db.session.query(AmazonProductRules).one()
    _review(plan_id, [item_id], remember=True)
    rules = db.session.query(AmazonProductRules).order_by(AmazonProductRules.id).all()
    assert len(rules) == 2 and rules[1].supersedes_id == first.id
    item = suspense.page(plan_id)['items'][0]['item']
    assert suspense.product_suggestion(item, ['Personal Care'])['source_rule_id'] == rules[1].id
    monkeypatch.setattr(workflow, '_policy', lambda: 'a' * 64)
    assert suspense.product_suggestion(item, ['Personal Care']) is None
    assert not suspense.page(plan_id)['items'][0]['can_review']


def test_new_source_eligibility_plan_invalidates_rule_without_changing_policy_or_decision(item_workflow_source):
    plan_id, doc, _, _ = _plan(item_workflow_source, prepare=False)
    # A different explicit model mode produces a new review manifest; the
    # original source review remains held and can receive updated source triage.
    run = suspense.prepare_new_review_run(plan_id, 'fixture owner', think=True)
    run_id = run.id
    db.session.commit()
    item_id = doc['items'][0]['item_id']
    _review(plan_id, [item_id], remember=True)
    item = deepcopy(suspense.page(plan_id)['items'][0]['item'])
    item['item_id'] = 'f' * 64  # The future occurrence has no own imported plan.
    assert suspense.product_suggestion(item, ['Personal Care']) is not None
    job = db.session.query(AmazonItemJobs).filter_by(run_id=run_id, item_id=item_id).one()
    original_decision_id = job.decision_id
    policy = workflow._policy()
    newer = deepcopy(doc)
    newer['items'][0]['policy_gate'] = 'needs_property'
    newer['items'][0]['property_treatment_needed'] = True
    newer['items'][0]['reason'] = 'A newer source review identifies an unresolved treatment policy.'
    assert suspense.import_triage(newer, 'independent reviewer').id != plan_id
    db.session.commit()
    assert workflow._policy() == policy and job.decision_id == original_decision_id
    assert suspense.product_suggestion(item, ['Personal Care']) is None


def test_source_and_stale_second_session_review_changes_reject_actions(item_workflow_source):
    plan_id, doc, _, wash_id = _plan(item_workflow_source)
    item_id = doc['items'][0]['item_id']; token = suspense.page(plan_id)['token']
    cached = db.session.get(AmazonItemJobs, wash_id)
    previous_id = cached.decision_id
    run = db.session.get(AmazonItemRuns, cached.run_id)
    source_item = workflow._items(workflow._manifest(run))[item_id]
    body = dict(run_id=run.id, job_id=wash_id, item_id=item_id, category=None,
        reason='Independent reviewer updated the evidence.', reviewer='second reviewer',
        manifest_digest=run.input_digest, item_digest=digest(source_item),
        proposal_digest=None, proposal=None, previous_decision_id=previous_id)
    other = Session(bind=db.engine)
    try:
        document = AccountingEvidence(kind='amazon_item_decision', sha256=digest(body), payload=dump(body), reviewer='second reviewer')
        other.add(document); other.flush()
        other.query(AmazonItemJobs).filter_by(id=wash_id).update({'decision_id': document.id})
        other.commit()
    finally:
        other.close()
    assert cached.decision_id == previous_id
    with pytest.raises(ValueError, match='changed'):
        _review(plan_id, [item_id], token=token)
    db.session.rollback()
    token = suspense.page(plan_id)['token']
    db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON CHANGED' WHERE fitid=:fitid"),
                       {'fitid': item_workflow_source['fitid']})
    db.session.commit(); before = _financial_state()
    with pytest.raises(ValueError, match='source'):
        _review(plan_id, [item_id], token=token)
    db.session.rollback()
    assert _financial_state() == before


def test_current_policy_manual_preparation_preserves_posted_siblings_and_resume_model(item_workflow_source, monkeypatch):
    run_id, book_id, wash_id = _prepared(item_workflow_source)
    _post(run_id)
    doc = _triage(run_id)
    plan = suspense.import_triage(doc, 'fixture owner'); plan_id = plan.id
    db.session.commit(); before = _financial_state()
    monkeypatch.setattr(workflow, '_policy', lambda: 'b' * 64)
    refreshed = suspense.prepare_new_review_run(plan_id, 'fixture owner')
    refreshed_id = refreshed.id
    db.session.commit()
    assert refreshed_id != run_id and refreshed.state == 'needs_attention'
    jobs = workflow._jobs(refreshed_id)
    book = next(j for j in jobs if j.decision_id is not None)
    wash = next(j for j in jobs if j.decision_id is None)
    assert workflow._decision(book)['category'] == 'Books and Publications'
    assert book.proposal is None and wash.proposal is None and wash.state == 'held'
    observed = []
    def infer(item, categories):
        observed.append(item['item_id'])
        return _fake_infer(item, categories)
    workflow.run_worker(refreshed_id, infer_fn=infer)
    assert observed == [wash.item_id]
    assert _financial_state() == before
    posted = suspense.page(plan_id)['items'][0]['posted']
    assert posted['category'] == 'Bookkeeping Suspense' and posted['amount'] == '21.60'
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Bookkeeping Suspense': Decimal('21.60')})


@pytest.mark.parametrize('drift', ['bank', 'bundle'])
def test_live_remaining_totals_follow_posting_and_stop_claiming_verified_results_after_drift(item_workflow_source, drift):
    plan_id, doc, _, _ = _plan(item_workflow_source)
    item_id = doc['items'][0]['item_id']
    before = suspense.page(plan_id)
    assert before['totals']['remaining_count'] == 1
    assert before['totals']['remaining_amount'] == '21.60'
    reviewed = _review(plan_id, [item_id])
    middle = suspense.page(plan_id)
    assert middle['totals']['reviewed_unposted_amount'] == '21.60'
    assert middle['totals']['remaining_amount'] == '21.60'
    _post(reviewed['run_id'])
    after = suspense.page(plan_id)
    assert after['totals']['count'] == 1 and after['totals']['amount'] == '21.60'  # Original cohort remains auditable.
    assert after['totals']['remaining_count'] == 0 and Decimal(after['totals']['remaining_amount']) == 0
    assert after['totals']['resolved_count'] == 1 and after['totals']['resolved_amount'] == '21.60'
    assert after['totals']['unverified_count'] == after['totals']['reviewed_unposted_count'] == 0
    assert after['items'][0]['posted']['category'] == 'Personal Care'
    if drift == 'bank':
        db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON CHANGED' WHERE fitid=:fitid"),
                           {'fitid': item_workflow_source['fitid']})
    else:
        bundle = db.session.query(AmazonEvidenceBundles).one()
        altered = json.loads(bundle.details); altered['changed_fixture'] = True
        bundle.details = dump(altered)
    db.session.commit()
    unverified = suspense.page(plan_id)
    assert unverified['totals']['resolved_count'] == 0
    assert unverified['totals']['unverified_count'] == 1
    assert unverified['totals']['unverified_amount'] == '21.60'
    assert unverified['items'][0]['posted'] is None


def test_preloaded_allocation_proofs_still_require_complete_revision_ancestry(item_workflow_source):
    from pacioli.models import JournalEntries
    run_id, _, wash_id = _prepared(item_workflow_source)
    _post(run_id)
    workflow.review_item(wash_id, 'Personal Care', 'Independent source review supports ordinary body wash.', 'fixture owner')
    db.session.commit(); _post(run_id)
    bundle = db.session.query(AmazonEvidenceBundles).one()
    entries = db.session.query(JournalEntries).filter_by(transaction_source='ofx',
        transaction_id=bundle.transaction_id).order_by(JournalEntries.allocation_key).all()
    documents = db.session.query(AccountingEvidence).filter_by(kind='amazon_allocation').order_by(AccountingEvidence.id).all()
    expected = workflow.allocation._current_proof(bundle, entries)[1]
    assert workflow.allocation._current_proof(bundle, entries, proof_documents=documents)[1] == expected
    with pytest.raises(ValueError, match='ancestry'):
        workflow.allocation._current_proof(bundle, entries, proof_documents=documents[-1:])
    with pytest.raises(ValueError, match='Missing'):
        workflow.allocation._current_proof(bundle, entries, proof_documents=[])
