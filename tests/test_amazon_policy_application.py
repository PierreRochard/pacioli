"""Implemented owner decisions remain source-bound through review and posting."""
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonItemJobs, AmazonItemRuns
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions import amazon_policy_application as application
from pacioli.functions import amazon_policy_questions as questions
from pacioli.functions import amazon_suspense as suspense
from pacioli.functions.reconciliation import digest
from tests.test_amazon_item_workflow import item_workflow_source, _financial_state, _post, _assert_source, _prepared
from tests.test_amazon_policy_questions import _setup, _preview, _confirm, ACTOR
from tests.test_amazon_suspense import _triage


def _packet(plan_id, answer_id=None, *, treatment='ordinary_movable', category='Personal Care'):
    plan, triage, source = suspense._plan(plan_id)
    items = workflow._items(source)
    return dict(version=application.VERSION, source_manifest_digest=triage['source_manifest_digest'],
        policy_digest=workflow._policy(), chart_digest=suspense._chart_digest(), items=[dict(
            item_id=row['item_id'], item_digest=digest(items[row['item_id']]), treatment=treatment,
            category=category, allowed_categories=[category] if category else [],
            reason='Independent review of this exact source supports ordinary personal expense.',
            answer_ids=[answer_id] if answer_id else [],
            research=[dict(ref='source:product.title', summary='The exact imported source identifies the ordinary item.')],
            hold_reason='A real project remains unresolved.' if treatment == 'hold' else '') for row in triage['items']])


def _implemented(source):
    plan_id, ids, job_id = _setup(source)
    answer = _confirm(_preview(plan_id))
    packet = _packet(plan_id, answer['id'])
    result = application.import_implementation(plan_id, packet, ACTOR)
    db.session.commit()
    return plan_id, ids, job_id, packet, result


def _save(plan_id, category='Personal Care', note=''):
    page = application.page(plan_id)
    selected = [dict(item_id=row['item_id'], category=category, reason=note) for row in page['items']]
    preview_id = application.prepare_preview(plan_id, selected, page['token'], ACTOR)
    preview = application.preview_page(preview_id, ACTOR)
    result = application.apply_preview(preview_id, preview['token'], ACTOR)
    db.session.commit()
    return preview_id, result


def test_implementation_review_and_financial_approval_are_distinct_and_replay_safe(item_workflow_source):
    before = _financial_state()
    plan_id, ids, job_id, packet, imported = _implemented(item_workflow_source)
    assert _financial_state() == before
    assert application.import_implementation(plan_id, packet, ACTOR)['replayed']
    db.session.commit()
    page = application.page(plan_id)
    assert page['totals']['eligible_count'] == 1 and page['totals']['eligible_amount'] == '21.60'
    assert page['items'][0]['category'] == 'Personal Care'
    assert page['owner_decisions'][0]['count'] == 1
    raw = db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence WHERE id=:id'),
                             {'id': imported['evidence_id']}).scalar()
    assert raw.startswith('fernet:') and 'ordinary personal expense' not in raw
    preview_id, reviewed = _save(plan_id)
    assert _financial_state() == before and reviewed['reviewed'] == 1
    count = db.session.query(AccountingEvidence).count()
    replay = application.apply_preview(preview_id, application.preview_page(preview_id, ACTOR)['token'], ACTOR)
    db.session.commit()
    assert replay['replayed'] and db.session.query(AccountingEvidence).count() == count
    approved = workflow.review_digest(reviewed['run_id'])
    result = _post(reviewed['run_id'], approved)
    assert result['posted'] == 1
    _assert_source(item_workflow_source, {'Personal Care': Decimal('21.60'), 'Books and Publications': Decimal('10.80')})
    after = _financial_state(); count = db.session.query(AccountingEvidence).count()
    _post(reviewed['run_id'], approved)
    assert _financial_state() == after and db.session.query(AccountingEvidence).count() == count
    page = application.page(plan_id)
    assert page['totals']['count'] == 0 and page['totals']['resolved_amount'] == '21.60'
    assert page['owner_decisions'][0]['count'] == 1


@pytest.mark.parametrize('change', ['source', 'category', 'answer', 'policy', 'duplicate', 'research', 'bypass'])
def test_implementation_rejects_changed_source_scope_and_missing_authority(item_workflow_source, change):
    plan_id, _, _ = _setup(item_workflow_source)
    answer = _confirm(_preview(plan_id))
    packet = _packet(plan_id, answer['id'])
    if change == 'source': packet['items'][0]['item_digest'] = '0' * 64
    elif change == 'category': packet['items'][0]['allowed_categories'] = ['Credit Card']
    elif change == 'answer': packet['items'][0]['answer_ids'] = []
    elif change == 'policy': packet['policy_digest'] = '0' * 64
    elif change == 'duplicate': packet['items'] *= 2
    elif change == 'research': packet['items'][0]['research'] = []
    else: packet['items'][0]['treatment'] = 'product_evidence'
    before = _financial_state(); count = db.session.query(AccountingEvidence).count()
    with pytest.raises(ValueError): application.import_implementation(plan_id, packet, ACTOR)
    db.session.commit()
    assert _financial_state() == before and db.session.query(AccountingEvidence).count() == count


@pytest.mark.parametrize('change', ['answers', 'facts', 'policy', 'bank'])
def test_approved_classifications_fail_closed_on_authority_or_source_changes(item_workflow_source, monkeypatch, change):
    plan_id, ids, _, _, _ = _implemented(item_workflow_source)
    _, saved = _save(plan_id)
    approved = workflow.review_digest(saved['run_id'])
    if change == 'answers':
        _confirm(_preview(plan_id, choice='assets'))
    elif change == 'facts':
        suspense.save_fact(plan_id, ids[0], 'Corrected owner use requires further review.',
                           suspense.page(plan_id)['token'], ACTOR); db.session.commit()
    elif change == 'policy': monkeypatch.setattr(workflow, '_policy', lambda: 'f' * 64)
    else:
        db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON CHANGED' WHERE fitid=:fitid"),
                           {'fitid': item_workflow_source['fitid']}); db.session.commit()
    before = _financial_state()
    with pytest.raises(ValueError): workflow.post_reviewed(saved['run_id'], approved, ACTOR, 'Now stale approval.')
    db.session.commit()
    assert _financial_state() == before


def test_held_project_cannot_be_overridden_in_old_or_batch_review(item_workflow_source):
    plan_id, _, job_id = _setup(item_workflow_source)
    answer = _confirm(_preview(plan_id))
    application.import_implementation(plan_id, _packet(plan_id, answer['id'], treatment='hold', category=None), ACTOR)
    db.session.commit()
    before = _financial_state()
    assert application.page(plan_id)['totals']['held_count'] == 1
    with pytest.raises(ValueError, match='real project'):
        workflow.review_item(job_id, 'Personal Care', 'Attempted unsupported override.', ACTOR)
    db.session.rollback()
    with pytest.raises(ValueError, match='outside'):
        _save(plan_id)
    db.session.rollback()
    assert _financial_state() == before


def test_hold_choice_is_preserved_without_new_owner_fact_or_journal(item_workflow_source):
    plan_id, _, _, _, _ = _implemented(item_workflow_source)
    before = _financial_state()
    _, saved = _save(plan_id, category=None, note='Leave this source unresolved for now.')
    assert application.page(plan_id)['items'][0]['category'] is None
    assert _financial_state() == before
    assert workflow._decision(db.session.query(AmazonItemJobs).filter_by(run_id=saved['run_id'],
        item_id=application.page(plan_id)['items'][0]['item_id']).one())['category'] is None


def test_batch_preview_bound_to_reviewer_and_rejects_stale_edits(item_workflow_source):
    plan_id, ids, _, _, _ = _implemented(item_workflow_source)
    page = application.page(plan_id)
    preview_id = application.prepare_preview(plan_id, [{'item_id': ids[0], 'category': 'Personal Care'}], page['token'], ACTOR)
    db.session.commit()
    with pytest.raises(ValueError, match='reviewer'):
        application.preview_page(preview_id, 'different reviewer')
    _confirm(_preview(plan_id, choice='assets'))
    before = _financial_state()
    with pytest.raises(ValueError, match='changed'):
        application.apply_preview(preview_id, application.preview_page(preview_id, ACTOR)['token'], ACTOR)
    db.session.commit()
    assert _financial_state() == before


def test_batch_failure_rolls_back_preceding_item_reviews_even_when_outer_commits(item_workflow_source, monkeypatch):
    plan_id, _, _ = _setup(item_workflow_source, both=True)
    answer = _confirm(_preview(plan_id))
    packet = _packet(plan_id, answer['id'])
    application.import_implementation(plan_id, packet, ACTOR); db.session.commit()
    page = application.page(plan_id)
    preview_id = application.prepare_preview(plan_id,
        [{'item_id': i['item_id'], 'category': 'Personal Care'} for i in page['items']], page['token'], ACTOR)
    db.session.commit()
    original = workflow.review_item; calls = []
    def fail_second(*args):
        calls.append(args)
        if len(calls) == 2: raise ValueError('Injected second classification failure')
        return original(*args)
    monkeypatch.setattr(workflow, 'review_item', fail_second)
    before = [(j.id, j.decision_id) for j in db.session.query(AmazonItemJobs).order_by(AmazonItemJobs.id)]
    count = db.session.query(AccountingEvidence).count()
    with pytest.raises(ValueError, match='Injected'):
        application.apply_preview(preview_id, application.preview_page(preview_id, ACTOR)['token'], ACTOR)
    db.session.commit()
    assert [(j.id, j.decision_id) for j in db.session.query(AmazonItemJobs).order_by(AmazonItemJobs.id)] == before
    assert db.session.query(AccountingEvidence).count() == count


def test_posted_ordinary_sibling_survives_policy_migration_without_global_old_gate_bypass(item_workflow_source, monkeypatch):
    run_id, book_id, wash_id = _prepared(item_workflow_source, approve=False)
    doc = _triage(run_id)
    wash_item = db.session.get(AmazonItemJobs, wash_id).item_id
    for row in doc['items']:
        if row['item_id'] == wash_item:
            row.update(policy_gate='needs_property', property_treatment_needed=True,
                       reason='Complete powered equipment requires an owner policy.')
    plan = suspense.import_triage(doc, ACTOR); plan_id = plan.id; db.session.commit()
    suspense.prepare_new_review_run(plan_id, ACTOR); db.session.commit()
    workflow.review_item(book_id, 'Books and Publications', 'Supported source-backed ordinary book.', ACTOR)
    workflow.review_item(wash_id, None, 'Equipment treatment remains unresolved.', ACTOR)
    db.session.commit(); _post(run_id)
    # The owner answer predates the implementation policy revision.
    answer = _confirm(_preview(plan_id))
    monkeypatch.setattr(workflow, '_policy', lambda: 'b' * 64)
    assert questions.page(plan_id)['totals']['answered_count'] == 1
    packet = _packet(plan_id, answer['id'])
    packet['items'] = [r for r in packet['items'] if r['item_id'] == wash_item]
    application.import_implementation(plan_id, packet, ACTOR); db.session.commit()
    new = suspense.prepare_new_review_run(plan_id, ACTOR); new_id = new.id; db.session.commit()
    assert new_id != run_id
    carried = db.session.query(AmazonItemJobs).filter_by(run_id=new_id, item_id=doc['items'][0]['item_id']).all()
    _, reviewed = _save(plan_id)
    result = _post(reviewed['run_id'])
    assert result['revised'] == 1 and result['posted'] == 0
    _assert_source(item_workflow_source, {'Personal Care': Decimal('21.60'), 'Books and Publications': Decimal('10.80')})


def test_existing_item_page_explicit_hold_is_never_replaced_by_a_category_guess(item_workflow_source):
    plan_id, _, job_id, _, _ = _implemented(item_workflow_source)
    assert application.page(plan_id)['items'][0]['category'] == 'Personal Care'
    workflow.review_item(job_id, None, 'Owner explicitly kept this purchase in suspense from item review.', ACTOR)
    db.session.commit()
    row = application.page(plan_id)['items'][0]
    assert row['category'] is None
    assert row['recommended_category'] == 'Personal Care'


def test_source_lock_contention_returns_retry_and_rolls_back_preview_evidence(item_workflow_source):
    from sqlalchemy.orm import Session
    plan_id, ids, _, _, _ = _implemented(item_workflow_source)
    page = application.page(plan_id)
    count = db.session.query(AccountingEvidence).count()
    db.session.commit()
    holder = Session(bind=db.engine)
    try:
        holder.execute(text('LOCK TABLE ofx.stmttrn IN ROW EXCLUSIVE MODE'))
        with pytest.raises(ValueError, match='wait, refresh this page and retry'):
            application.prepare_preview(plan_id, [{'item_id': ids[0], 'category': 'Personal Care'}],
                                        page['token'], ACTOR)
        db.session.commit()
        assert db.session.query(AccountingEvidence).count() == count
    finally:
        holder.rollback(); holder.close()


def test_posted_implementation_carries_across_later_policy_revision_without_releasing_new_reviews(item_workflow_source, monkeypatch):
    plan_id, ids, _ = _setup(item_workflow_source, both=True)
    answer = _confirm(_preview(plan_id))
    packet = _packet(plan_id, answer['id'])
    application.import_implementation(plan_id, packet, ACTOR); db.session.commit()
    page = application.page(plan_id)
    first, remaining = [r['item_id'] for r in page['items']]
    selected = [dict(item_id=r['item_id'], category='Personal Care' if r['item_id'] == first else None) for r in page['items']]
    preview = application.prepare_preview(plan_id, selected, page['token'], ACTOR)
    result = application.apply_preview(preview, application.preview_page(preview, ACTOR)['token'], ACTOR)
    db.session.commit(); _post(result['run_id'])
    original_plan, _, source = suspense._plan(plan_id)
    first_amount = Decimal(workflow._items(source)[first]['amount'])
    _assert_source(item_workflow_source, {'Personal Care': first_amount, 'Bookkeeping Suspense': Decimal('32.40') - first_amount})
    monkeypatch.setattr(workflow, '_policy', lambda: 'c' * 64)
    packet = _packet(plan_id, answer['id'])
    packet['items'] = [row for row in packet['items'] if row['item_id'] == remaining]
    application.import_implementation(plan_id, packet, ACTOR); db.session.commit()
    current = suspense.prepare_new_review_run(plan_id, ACTOR); run_id = current.id; db.session.commit()
    first_job = db.session.query(AmazonItemJobs).filter_by(run_id=run_id, item_id=first).one()
    carried = workflow._decision(first_job)
    assert carried['carry_forward'] and carried['category'] == 'Personal Care'
    before = _financial_state()
    # Historical authority cannot be requested by the ordinary/manual path.
    with pytest.raises(ValueError, match='refresh the implementation'):
        workflow.review_item(first_job.id, 'Personal Care', 'A fresh unposted approval under the stale policy.', ACTOR)
    db.session.rollback()
    assert _financial_state() == before
    _, reviewed = _save(plan_id)
    result = _post(reviewed['run_id'])
    assert result['revised'] == 1
    _assert_source(item_workflow_source, {'Personal Care': Decimal('32.40')})


@pytest.mark.parametrize('category', ['Personal Care', None])
def test_saved_correction_survives_reload_and_resave_without_duplication(item_workflow_source, category):
    plan_id, _, _, _, _ = _implemented(item_workflow_source)
    before = _financial_state()
    policy, chart = workflow._policy(), suspense._chart_digest()
    note = 'Please retain this <private> purchase context on the next review.'
    _save(plan_id, category=category, note=note)
    row = application.page(plan_id)['items'][0]
    assert row['saved_correction'] == note
    assert row['saved_review_reason'].endswith(' Reviewer correction: ' + note)
    _save(plan_id, category=category, note=row['saved_correction'])
    reloaded = application.page(plan_id)['items'][0]
    assert reloaded['saved_correction'] == note
    assert reloaded['saved_review_reason'] == row['saved_review_reason']
    assert reloaded['saved_review_reason'].count(note) == 1
    assert _financial_state() == before
    assert workflow._policy() == policy and suspense._chart_digest() == chart


def test_legacy_review_text_is_visible_without_inventing_a_structured_correction(item_workflow_source):
    plan_id, _, job_id, _, _ = _implemented(item_workflow_source)
    before = _financial_state()
    reason = 'Historical manual explanation. Reviewer correction: this is not a batch note.'
    workflow.review_item(job_id, 'Personal Care', reason, ACTOR)
    db.session.commit()
    row = application.page(plan_id)['items'][0]
    assert row['saved_review_reason'] == reason
    assert row['saved_correction'] == ''
    assert _financial_state() == before
