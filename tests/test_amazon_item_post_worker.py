"""Durable item-post requests preserve approval and atomic ledger semantics."""
from decimal import Decimal
import fcntl
import json
import os

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonItemPostRuns
from pacioli.functions import amazon_item_posting as posting
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions.reconciliation import digest
from tests.test_amazon_item_workflow import (
    item_workflow_source, _assert_source, _financial_state, _prepared,
)


PRIVATE_REASON = 'PRIVATE approval: independently reviewed the paperback source item.'
PRIVATE_REVIEWER = 'private-fixture-owner@example.invalid'
PRIVATE_EXCEPTION = 'PRIVATE source title, bank account, and approval must never reach progress'
SUMMARY_KEYS = {'posted', 'revised', 'unchanged', 'held_settlements',
                'classified_amount', 'suspense_amount'}


def _request(run_id, expected=None, reviewer=PRIVATE_REVIEWER, reason=PRIVATE_REASON):
    job = posting.create_request(run_id, expected or workflow.review_digest(run_id),
                                 reviewer, reason)
    identifier = job.id
    db.session.commit()
    return identifier


def _proof(job_id):
    job = db.session.get(AmazonItemPostRuns, job_id, populate_existing=True)
    evidence = db.session.get(AccountingEvidence, job.evidence_id)
    body = json.loads(evidence.payload)
    assert evidence.kind == 'amazon_item_post_request'
    assert digest(body) == evidence.sha256
    return job, evidence, body


def _safe_status(job_id):
    result = posting.status(job_id)
    encoded = json.dumps(result)
    for private in (PRIVATE_REASON, PRIVATE_REVIEWER, PRIVATE_EXCEPTION,
                    'Fixture paperback novel', 'Fixture ordinary body wash',
                    'PRIVATE BILLING', 'PRIVATE SHIPPING', 'fixture-private-tracking'):
        assert private not in encoded
    if result.get('result'):
        assert set(result['result']) <= SUMMARY_KEYS
    return result


def test_request_dedupes_without_posting_and_binds_original_immutable_approval(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    expected = workflow.review_digest(run_id)
    before = _financial_state()
    job_id = _request(run_id, expected)
    job, evidence, body = _proof(job_id)
    evidence_id = evidence.id
    request_body = dict(body)
    assert body['run_id'] == run_id and body['expected_digest'] == expected
    assert body['reviewer'] == PRIVATE_REVIEWER and body['reason'] == PRIVATE_REASON
    assert body['manifest_digest']
    assert job.request_key == digest(dict(run_id=run_id, expected_digest=expected))
    assert job.state == 'queued'
    assert _financial_state() == before

    # A duplicate network submission cannot replace the author or rationale of
    # the durable approval, even if the retried request supplies different text.
    repeated = _request(run_id, expected, 'another reviewer', 'A different request reason.')
    assert repeated == job_id
    job, evidence, body = _proof(job_id)
    assert evidence.id == evidence_id and body == request_body
    assert db.session.query(AmazonItemPostRuns).count() == 1
    assert db.session.query(AccountingEvidence).filter_by(kind='amazon_item_post_request').count() == 1
    raw = db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence '
                                  'WHERE id=:id'), dict(id=evidence_id)).scalar()
    assert raw.startswith('fernet:') and PRIVATE_REASON not in raw
    assert _financial_state() == before
    status = _safe_status(job_id)
    assert status['state'] == 'queued' and status['run_id'] == run_id
    assert posting.latest_status(run_id)['id'] == job_id


def test_worker_posts_partial_source_once_and_completed_retry_is_noop(item_workflow_source, monkeypatch):
    run_id, _, _ = _prepared(item_workflow_source)
    job_id = _request(run_id)
    posting.run_worker(job_id)
    status = _safe_status(job_id)
    assert status['state'] == 'completed'
    assert status['result']['posted'] == 1 and status['result']['revised'] == 0
    assert Decimal(status['result']['classified_amount']) == Decimal('10.80')
    assert Decimal(status['result']['suspense_amount']) == Decimal('21.60')
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Bookkeeping Suspense': Decimal('21.60')})
    before = _financial_state()
    evidence_count = db.session.query(AccountingEvidence).count()
    _, _, proof = _proof(job_id)
    monkeypatch.setattr(workflow, 'post_reviewed',
                        lambda *args, **kwargs: pytest.fail('Completed requests must not post again'))
    posting.run_worker(job_id)
    assert _request(run_id, proof['expected_digest'], 'retry actor', 'Retry after completed response.') == job_id
    assert _safe_status(job_id)['state'] == 'completed'
    assert _financial_state() == before
    assert db.session.query(AccountingEvidence).count() == evidence_count


@pytest.mark.parametrize('drift', ['source', 'decision'])
def test_queued_request_rejects_changed_evidence_with_durable_safe_failure(item_workflow_source, drift):
    run_id, book_id, _ = _prepared(item_workflow_source)
    expected = workflow.review_digest(run_id)
    job_id = _request(run_id, expected)
    _, proof, body = _proof(job_id)
    original_evidence_id, original_body = proof.id, dict(body)
    if drift == 'source':
        db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON CHANGED' WHERE fitid=:fitid"),
                           dict(fitid=item_workflow_source['fitid']))
    else:
        workflow.review_item(book_id, None, 'Independent reviewer requires further evidence.', 'second reviewer')
    db.session.commit()
    before = _financial_state()

    with pytest.raises(ValueError):
        posting.run_worker(job_id)
    assert _safe_status(job_id)['state'] == 'failed'
    assert _financial_state() == before
    job, proof, body = _proof(job_id)
    assert proof.id == original_evidence_id and body == original_body

    # Retrying must keep the frozen approval token. It must not silently obtain
    # the new review digest and thereby authorize later changes.
    assert _request(run_id, expected, 'retry reviewer', 'Retry the old frozen approval.') == job_id
    assert _safe_status(job_id)['state'] == 'queued'
    with pytest.raises(ValueError):
        posting.run_worker(job_id)
    assert _safe_status(job_id)['state'] == 'failed'
    assert _proof(job_id)[2] == original_body
    assert _financial_state() == before


def test_failure_after_real_post_rolls_back_entire_batch_and_same_request_can_retry(item_workflow_source, monkeypatch):
    run_id, _, _ = _prepared(item_workflow_source)
    job_id = _request(run_id)
    before = _financial_state()
    evidence_count = db.session.query(AccountingEvidence).count()
    real_post = workflow.post_reviewed
    progress_snapshots = []

    def fail_after_financial_work(*args, **kwargs):
        report = kwargs['report']
        report('preparing_settlement', processed=1, total=2, item_title=PRIVATE_EXCEPTION)
        progress_snapshots.append(_safe_status(job_id))
        report(PRIVATE_EXCEPTION, processed=True, total=float('inf'), reason=PRIVATE_REASON)
        progress_snapshots.append(_safe_status(job_id))
        result = real_post(*args, **kwargs)
        assert result['posted'] == 1
        raise RuntimeError(PRIVATE_EXCEPTION)

    monkeypatch.setattr(workflow, 'post_reviewed', fail_after_financial_work)
    with pytest.raises(RuntimeError, match='PRIVATE source title'):
        posting.run_worker(job_id)
    assert '1 of 2 settlements' in progress_snapshots[0]['progress']
    assert progress_snapshots[1]['progress'] == 'Processing the approved financial batch'
    assert _safe_status(job_id)['state'] == 'failed'
    assert _financial_state() == before
    assert db.session.query(AccountingEvidence).count() == evidence_count
    _assert_source(item_workflow_source, {'Bookkeeping Suspense': Decimal('32.40')})

    monkeypatch.setattr(workflow, 'post_reviewed', real_post)
    original = _proof(job_id)[2]
    assert _request(run_id, original['expected_digest']) == job_id
    posting.run_worker(job_id)
    assert _safe_status(job_id)['state'] == 'completed'
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Bookkeeping Suspense': Decimal('21.60')})


def test_worker_lease_excludes_overlapping_attempt_then_allows_original_request(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    job_id = _request(run_id)
    before = _financial_state()
    descriptor = os.open(posting._lease_path(job_id), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        posting.run_worker(job_id)
        assert _safe_status(job_id)['state'] == 'queued'
        assert _financial_state() == before
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    posting.run_worker(job_id)
    assert _safe_status(job_id)['state'] == 'completed'
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Bookkeeping Suspense': Decimal('21.60')})


def test_one_active_request_per_run_and_new_explicit_token_can_post_new_review(item_workflow_source):
    run_id, _, bodywash = _prepared(item_workflow_source)
    first_id = _request(run_id)
    workflow.review_item(bodywash, 'Personal Care',
                         'Independently confirmed ordinary personal body wash.', 'fixture owner')
    db.session.commit()
    fresh_digest = workflow.review_digest(run_id)
    with pytest.raises(ValueError):
        posting.create_request(run_id, fresh_digest, PRIVATE_REVIEWER, PRIVATE_REASON)
    db.session.rollback()
    assert posting.latest_status(run_id)['id'] == first_id
    with pytest.raises(ValueError):
        posting.run_worker(first_id)
    assert _safe_status(first_id)['state'] == 'failed'
    second_id = _request(run_id, fresh_digest)
    assert second_id != first_id and posting.latest_status(run_id)['id'] == second_id
    posting.run_worker(second_id)
    assert _safe_status(second_id)['state'] == 'completed'
    assert _safe_status(first_id)['state'] == 'failed'
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Personal Care': Decimal('21.60')})


def test_later_request_revises_partial_post_without_changing_completed_request(item_workflow_source):
    run_id, _, bodywash = _prepared(item_workflow_source)
    first_id = _request(run_id)
    posting.run_worker(first_id)
    first_proof = _proof(first_id)[2]
    first_result = _safe_status(first_id)['result']
    assert first_result['posted'] == 1

    workflow.review_item(bodywash, 'Personal Care',
                         'Independently confirmed ordinary personal body wash.', 'fixture owner')
    db.session.commit()
    second_id = _request(run_id)
    assert second_id != first_id
    posting.run_worker(second_id)
    assert _safe_status(second_id)['result']['revised'] == 1
    assert _safe_status(second_id)['result']['posted'] == 0
    assert _proof(first_id)[2] == first_proof
    assert _safe_status(first_id)['result'] == first_result
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Personal Care': Decimal('21.60')})
    before = _financial_state()
    posting.run_worker(first_id)
    posting.run_worker(second_id)
    assert _financial_state() == before
