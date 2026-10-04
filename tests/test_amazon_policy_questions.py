"""Owner answers are immutable collection evidence, never financial authority."""
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pacioli.extensions import db
from pacioli.models import (AccountingEvidence, AmazonItemJobs, AmazonPolicyAnswers,
    AmazonProductRules, AmazonSuspenseFacts)
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions import amazon_policy_questions as questions
from pacioli.functions import amazon_suspense as suspense
from tests.test_amazon_item_workflow import item_workflow_source, _financial_state, _prepared, _post
from tests.test_amazon_suspense import _triage


ACTOR = 'fixture policy owner'


def _setup(source, *, both=False, gate='needs_property'):
    run_id, book_id, wash_id = _prepared(source, approve=not both)
    doc = _triage(run_id)
    for row in doc['items']:
        row.update(policy_gate=gate, property_treatment_needed=gate == 'needs_property',
            reason='Complete powered equipment requires an owner policy.' if gate == 'needs_property'
                   else 'The source needs an actual use fact.')
    plan = suspense.import_triage(doc, ACTOR)
    plan_id = plan.id
    db.session.commit()
    suspense.prepare_new_review_run(plan_id, ACTOR)
    db.session.commit()
    return plan_id, [r['item_id'] for r in doc['items']], wash_id


def _preview(plan_id, *, choice='expense', scope='selected', ids=None, custom='', params=None, token=None):
    page = questions.page(plan_id)
    selected = page['question']
    identifier = questions.prepare_preview(plan_id, selected['key'], choice, scope,
        ids if ids is not None else [i['item_id'] for i in selected['items']], custom, params or {},
        token or page['token'], ACTOR)
    db.session.commit()
    return identifier


def _confirm(preview_id):
    preview = questions.preview_page(preview_id, ACTOR)
    result = questions.confirm(preview_id, preview['token'], ACTOR)
    db.session.commit()
    return result


def _nonanswer_state():
    return dict(financial=_financial_state(),
        decisions=[(j.id, j.decision_id, j.proposal, j.state) for j in db.session.query(AmazonItemJobs).order_by(AmazonItemJobs.id)],
        facts=db.session.query(AmazonSuspenseFacts).count(), rules=db.session.query(AmazonProductRules).count())


def test_preview_and_answer_are_encrypted_immutable_and_never_release_the_accounting_gate(item_workflow_source):
    plan_id, ids, job_id = _setup(item_workflow_source)
    before = _nonanswer_state()
    preview_id = _preview(plan_id, custom='Private owner preference; inspect the exact purchase.')
    preview = questions.preview_page(preview_id, ACTOR)
    assert preview['count'] == 1 and preview['amount'] == '21.60'
    assert preview['items'][0]['source_refs'] and preview['items'][0]['components']
    assert preview['status'] == 'saved_policy_pending_implementation'
    assert not preview['already_confirmed'] and not preview['stale']
    assert sum(Decimal(c['amount']) for c in preview['items'][0]['components']) == Decimal('21.60')
    result = _confirm(preview_id)
    assert result['plan_id'] == plan_id and result['count'] == 1 and not result['replayed']
    page = questions.page(plan_id)
    assert page['totals']['answered_count'] == 1 and page['totals']['remaining_amount'] == '21.60'
    assert page['question']['items'][0]['answer']['choice'] == 'expense'
    assert _nonanswer_state() == before
    for identifier in (preview_id, db.session.query(AmazonPolicyAnswers).one().evidence_id):
        payload = db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence WHERE id=:id'),
                                     {'id': identifier}).scalar()
        assert payload.startswith('fernet:') and 'Private owner preference' not in payload
    with pytest.raises(ValueError, match='accounting-policy treatment'):
        workflow.review_item(job_id, 'Personal Care', 'A saved preference is not an implemented policy.', ACTOR)
    db.session.rollback()
    assert _nonanswer_state() == before
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE amazon.policy_answers SET question_key='changed'"))
    db.session.rollback()
    with pytest.raises(DBAPIError):
        db.session.execute(text('DELETE FROM amazon.policy_answers'))
    db.session.rollback()


def test_exact_confirmation_replay_never_adds_evidence_or_overwrites_a_newer_answer(item_workflow_source):
    plan_id, _, _ = _setup(item_workflow_source)
    first = _preview(plan_id)
    original = _confirm(first)
    second = _preview(plan_id, choice='assets')
    newer = _confirm(second)
    before = (_nonanswer_state(), db.session.query(AccountingEvidence).count(), db.session.query(AmazonPolicyAnswers).count())
    replay = _confirm(first)
    assert replay['id'] == original['id'] and replay['replayed']
    assert questions.page(plan_id)['question']['items'][0]['answer']['id'] == newer['id']
    assert (_nonanswer_state(), db.session.query(AccountingEvidence).count(), db.session.query(AmazonPolicyAnswers).count()) == before


def test_subset_answers_keep_independent_occurrences_and_explicit_future_intent(item_workflow_source):
    plan_id, ids, _ = _setup(item_workflow_source, both=True)
    data = questions.page(plan_id)
    assert data['question']['count'] == 2 and data['question']['amount'] == '32.40'
    first = _confirm(_preview(plan_id, ids=[ids[0]], scope='this_item'))
    second = _confirm(_preview(plan_id, ids=[ids[1]], scope='future', choice='assets'))
    by_id = {i['item_id']: i['answer'] for i in questions.page(plan_id)['question']['items']}
    assert by_id[ids[0]]['id'] == first['id'] and by_id[ids[1]]['id'] == second['id']
    assert by_id[ids[1]]['scope'] == 'future'
    assert db.session.query(AmazonProductRules).count() == 0
    latest = _confirm(_preview(plan_id, ids=[ids[0]], scope='this_item', choice='defer'))
    data = questions.page(plan_id)
    by_id = {i['item_id']: i['answer'] for i in data['question']['items']}
    assert by_id[ids[0]]['id'] == latest['id'] and by_id[ids[1]]['id'] == second['id']
    assert data['totals']['answered_count'] == data['totals']['deferred_count'] == 1
    assert data['totals']['remaining_count'] == 2


@pytest.mark.parametrize('change', ['facts', 'answers', 'policy', 'definitions', 'bank'])
def test_stale_preview_cannot_be_confirmed_after_context_or_source_changes(item_workflow_source, monkeypatch, change):
    plan_id, ids, _ = _setup(item_workflow_source)
    preview_id = _preview(plan_id)
    preview_token = questions.preview_page(preview_id, ACTOR)['token']
    if change == 'facts':
        suspense.save_fact(plan_id, ids[0], 'A newer actual purchase fact.', suspense.page(plan_id)['token'], ACTOR)
        db.session.commit()
    elif change == 'answers':
        _confirm(_preview(plan_id, choice='assets'))
    elif change == 'policy':
        monkeypatch.setattr(workflow, '_policy', lambda: 'f' * 64)
    elif change == 'definitions':
        definition = deepcopy(questions.QUESTIONS['durable_goods'])
        definition['prompt'] += ' A revised policy question.'
        monkeypatch.setitem(questions.QUESTIONS, 'durable_goods', definition)
    else:
        db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON CHANGED' WHERE fitid=:fitid"),
                           {'fitid': item_workflow_source['fitid']})
        db.session.commit()
    count = db.session.query(AmazonPolicyAnswers).count()
    before = _nonanswer_state()
    with pytest.raises(ValueError):
        questions.confirm(preview_id, preview_token, ACTOR)
    db.session.rollback()
    assert db.session.query(AmazonPolicyAnswers).count() == count
    assert _nonanswer_state() == before


def test_saved_answer_survives_question_copy_but_owner_fact_corrections_require_review(item_workflow_source, monkeypatch):
    plan_id, ids, _ = _setup(item_workflow_source)
    _confirm(_preview(plan_id))
    assert questions.page(plan_id)['totals']['answered_count'] == 1
    definition = deepcopy(questions.QUESTIONS['durable_goods'])
    definition['choices'][0]['explanation'] += ' Revised proposal.'
    monkeypatch.setitem(questions.QUESTIONS, 'durable_goods', definition)
    assert questions.page(plan_id)['totals']['answered_count'] == 1
    assert not questions.page(plan_id)['question']['items'][0]['answer']['stale']
    suspense.save_fact(plan_id, ids[0], 'New actual purpose evidence.', suspense.page(plan_id)['token'], ACTOR)
    db.session.commit()
    assert questions.page(plan_id)['totals']['answered_count'] == 0


def test_drifted_or_resolved_financial_items_are_not_claimed_as_questions(item_workflow_source):
    plan_id, ids, job_id = _setup(item_workflow_source, gate='ordinary')
    assert questions.page(plan_id)['totals']['remaining_count'] == 1
    workflow.review_item(job_id, 'Personal Care', 'Independent exact source review.', ACTOR)
    db.session.commit()
    _post(suspense.page(plan_id)['plan']['review_run_id'])
    assert questions.page(plan_id)['questions'] == []
    assert questions.page(plan_id)['totals']['remaining_count'] == 0
    db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON CHANGED' WHERE fitid=:fitid"),
                       {'fitid': item_workflow_source['fitid']})
    db.session.commit()
    data = questions.page(plan_id)
    assert data['questions'] == [] and data['totals']['unverified_count'] == 1


@pytest.mark.parametrize('params', [
    {}, {'threshold_amount': '0', 'threshold_basis': 'separate_item'},
    {'threshold_amount': '-1', 'threshold_basis': 'separate_item'},
    {'threshold_amount': 'NaN', 'threshold_basis': 'separate_item'},
    {'threshold_amount': '1e3', 'threshold_basis': 'separate_item'},
    {'threshold_amount': '1.001', 'threshold_basis': 'separate_item'},
    {'threshold_amount': '10000000.01', 'threshold_basis': 'separate_item'},
    {'threshold_amount': '1000', 'threshold_basis': 'per_source_quantity'},
    {'threshold_amount': 1000.0, 'threshold_basis': 'separate_item'},
])
def test_threshold_rejects_guessed_imprecise_or_unbounded_parameters(item_workflow_source, params):
    plan_id, _, _ = _setup(item_workflow_source)
    before = db.session.query(AccountingEvidence).count()
    with pytest.raises(ValueError, match='threshold'):
        _preview(plan_id, choice='threshold', params=params)
    db.session.rollback()
    assert db.session.query(AccountingEvidence).count() == before


def test_threshold_preview_keeps_exact_money_and_does_not_compute_per_unit_assets(item_workflow_source):
    plan_id, _, _ = _setup(item_workflow_source)
    before = _nonanswer_state()
    preview_id = _preview(plan_id, choice='threshold',
                          params={'threshold_amount': '1000', 'threshold_basis': 'complete_assembly'})
    preview = questions.preview_page(preview_id, ACTOR)
    assert preview['parameters'] == {'threshold_amount': '1000.00', 'threshold_basis': 'complete_assembly'}
    assert 'at or above' in preview['choice']['explanation']
    assert any('do not divide' in s for s in preview['next_steps'])
    _confirm(preview_id)
    assert _nonanswer_state() == before


@pytest.mark.parametrize('changes', [
    {'choice': 'invented'}, {'scope': 'all'}, {'ids': []}, {'ids': ['missing']},
    {'choice': 'custom', 'custom': ''}, {'custom': 'x' * 2001},
    {'choice': 'defer', 'scope': 'future'}, {'params': {'unknown': 'value'}},
    {'params': {'threshold_amount': '1000', 'threshold_basis': 'separate_item'}},
])
def test_invalid_selections_cannot_create_preview_evidence(item_workflow_source, changes):
    plan_id, _, _ = _setup(item_workflow_source)
    before = db.session.query(AccountingEvidence).count()
    with pytest.raises(ValueError):
        _preview(plan_id, **changes)
    db.session.rollback()
    assert db.session.query(AccountingEvidence).count() == before


def test_duplicates_and_multi_item_single_scope_are_rejected(item_workflow_source):
    plan_id, ids, _ = _setup(item_workflow_source, both=True)
    for selected, scope in ((ids + [ids[0]], 'selected'), (ids, 'this_item')):
        with pytest.raises(ValueError):
            _preview(plan_id, ids=selected, scope=scope)
        db.session.rollback()
    assert db.session.query(AmazonPolicyAnswers).count() == 0


def test_context_cannot_be_reused_as_future_policy_or_silently_become_owner_fact(item_workflow_source):
    plan_id, ids, _ = _setup(item_workflow_source, gate='needs_fact')
    page = questions.page(plan_id)
    assert page['question']['key'].startswith('item_') and page['question']['kind'] == 'context'
    with pytest.raises(ValueError):
        _preview(plan_id, choice='custom', custom='For my household.', scope='future')
    db.session.rollback()
    before = _nonanswer_state()
    result = _confirm(_preview(plan_id, choice='custom', custom='For my household.', scope='this_item'))
    assert result['status'] == 'saved_context_pending_review'
    assert _nonanswer_state() == before
    assert db.session.query(AmazonSuspenseFacts).count() == 0


def test_previews_are_reviewer_bound_and_confirmation_requires_exact_preview_digest(item_workflow_source):
    plan_id, _, _ = _setup(item_workflow_source)
    identifier = _preview(plan_id)
    with pytest.raises(ValueError, match='reviewer'):
        questions.preview_page(identifier, 'someone else')
    with pytest.raises(ValueError, match='preview changed'):
        questions.confirm(identifier, '0' * 64, ACTOR)
    assert db.session.query(AmazonPolicyAnswers).count() == 0


def test_active_posting_blocks_new_previews_and_answers(item_workflow_source):
    from pacioli.functions import amazon_item_posting as posting
    plan_id, _, _ = _setup(item_workflow_source)
    identifier = _preview(plan_id)
    preview = questions.preview_page(identifier, ACTOR)
    run_id = suspense.page(plan_id)['plan']['review_run_id']
    posting.create_request(run_id, workflow.review_digest(run_id), ACTOR, 'Frozen separate posting request.')
    db.session.commit()
    with pytest.raises(ValueError, match='queued or running'):
        questions.confirm(identifier, preview['token'], ACTOR)
    db.session.rollback()
    with pytest.raises(ValueError, match='queued or running'):
        _preview(plan_id)
    db.session.rollback()
    assert db.session.query(AmazonPolicyAnswers).count() == 0


def test_contended_source_table_lock_returns_safe_retry_without_partial_preview(item_workflow_source):
    plan_id, ids, _ = _setup(item_workflow_source)
    page = questions.page(plan_id)
    before = db.session.query(AccountingEvidence).count()
    db.session.commit()
    holder = Session(bind=db.engine)
    try:
        holder.execute(text('LOCK TABLE ofx.stmttrn IN ROW EXCLUSIVE MODE'))
        with pytest.raises(ValueError, match='wait, refresh this page and retry'):
            questions.prepare_preview(plan_id, page['question']['key'], 'expense', 'selected', ids,
                                      '', {}, page['token'], ACTOR)
        # The savepoint recovers even if the caller commits the outer request.
        db.session.commit()
        assert db.session.query(AccountingEvidence).count() == before
        assert db.session.query(AmazonPolicyAnswers).count() == 0
    finally:
        holder.rollback()
        holder.close()


def test_confirmation_freezes_legacy_review_and_worker_rows_until_answer_commit(item_workflow_source, monkeypatch):
    plan_id, _, job_id = _setup(item_workflow_source)
    identifier = _preview(plan_id)
    preview = questions.preview_page(identifier, ACTOR)
    run_id = questions.page(plan_id)['plan']['review_run_id']
    original_evidence = questions.evidence
    blocked_tables = []

    def evidence_with_competing_writers(kind, payload, reviewer):
        if kind == 'amazon_policy_answer':
            # These writers intentionally bypass the suspense advisory lock,
            # just as the legacy review path and item worker can do.
            for table, row_id in (('item_jobs', job_id), ('item_runs', run_id)):
                other = Session(bind=db.engine)
                try:
                    other.execute(text("SET LOCAL lock_timeout='50ms'"))
                    with pytest.raises(DBAPIError) as caught:
                        other.execute(text('UPDATE amazon.' + table +
                                           ' SET updated_at=updated_at WHERE id=:id'), {'id': row_id})
                    assert getattr(caught.value.orig, 'pgcode', None) == '55P03'
                    blocked_tables.append(table)
                finally:
                    other.rollback()
                    other.close()
        return original_evidence(kind, payload, reviewer)

    monkeypatch.setattr(questions, 'evidence', evidence_with_competing_writers)
    result = questions.confirm(identifier, preview['token'], ACTOR)
    db.session.commit()
    assert result['status'] == 'saved_policy_pending_implementation'
    assert blocked_tables == ['item_jobs', 'item_runs']


@pytest.mark.parametrize('gate,reason,title,key', [
    ('needs_property', 'Complete powered device.', 'Fixture device', 'durable_goods'),
    ('needs_property', 'Furniture-leg floor-protection caps need a boundary.', 'Fixture chair cap', 'protective_accessories'),
    ('needs_property', 'A reusable waste-container lid restraint needs a boundary.', 'Fixture lid restraint', 'protective_accessories'),
    ('needs_property', 'The workbook includes a sound button.', 'Fixture workbook', 'sound_workbook'),
    ('needs_property', 'Veterinary treatment has a policy gap.', 'Fixture veterinary product', 'veterinary_care'),
    ('needs_fact', 'Need purpose.', 'Fixture drain stopper', 'repair_context'),
    ('needs_fact', 'Need purpose.', 'Fixture plastic weld adhesive', 'craft_context'),
    ('needs_fact', 'Need product specification.', 'Fixture soil moisture sensor', 'sensor_specification'),
    ('needs_fact', 'Need material.', 'Fixture crucifix', 'jewelry_material'),
    ('needs_fact', 'Need purpose.', 'Fixture marine sealant', 'marine_context'),
    ('needs_fact', 'Need purpose.', 'Fixture tray', 'tray_context'),
    ('needs_property', 'A wholly new policy question.', 'New product', 'item_fixture'),
    ('needs_fact', 'A wholly new purpose question.', 'New product', 'item_fixture'),
])
def test_presentation_catalog_has_explicit_fallback_and_never_approves_source(gate, reason, title, key):
    row = dict(item_id='fixture', policy_gate=gate, reason=reason, source_title=title)
    before = deepcopy(row)
    question = questions._question(row)
    assert question['key'] == key and row == before
    assert {'custom', 'defer'}.issubset({c['key'] for c in question['choices']})
    assert 'category' not in question and 'approved' not in question
