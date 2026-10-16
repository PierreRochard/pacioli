"""Durable explicit financial approvals, executed outside HTTP request timeouts.

The request evidence is immutable. Progress uses a separate short transaction;
all journal changes and the completed result share one atomic commit. A crashed
worker can retry only its original frozen approval, which the accounting workflow
revalidates or recognizes as an exact replay under the ledger lock.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonItemPostRuns, AmazonItemRuns
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions.reconciliation import digest, dump, evidence


VERSION = 'amazon-item-post-request-v1'
ACTIVE = ('queued', 'running')
COUNT_FIELDS = ('posted', 'revised', 'unchanged', 'held_settlements')
AMOUNT_FIELDS = ('classified_amount', 'suspense_amount')
ERROR_CODES = ('review_changed', 'period_closed', 'validation_failed', 'posting_failed')


def _now():
    return datetime.now(timezone.utc)


def _identifier(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise ValueError('Choose a valid posting request or review run')
    return value


def _request_key(run_id, expected_digest):
    return digest(dict(run_id=run_id, expected_digest=expected_digest))


def _request(job):
    document = db.session.get(AccountingEvidence, job.evidence_id)
    if not document or document.kind != 'amazon_item_post_request':
        raise ValueError('Missing immutable posting authorization')
    body = json.loads(document.payload)
    required = {'version', 'run_id', 'expected_digest', 'reviewer', 'reason', 'manifest_digest'}
    if (not isinstance(body, dict) or set(body) != required or digest(body) != document.sha256
            or body['version'] != VERSION or body['run_id'] != job.run_id
            or _request_key(body['run_id'], body['expected_digest']) != job.request_key):
        raise ValueError('Posting authorization changed')
    _authorization(body['expected_digest'], body['reviewer'], body['reason'])
    return body


def _authorization(expected_digest, reviewer, reason):
    if not isinstance(expected_digest, str) or not re.fullmatch('[0-9a-f]{64}', expected_digest):
        raise ValueError('An exact review approval digest is required')
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 240:
        raise ValueError('A named reviewer is required for financial posting')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('A bounded posting authorization reason is required')


def create_request(run_id, expected_digest, reviewer, reason):
    """Save or return the original explicit approval; caller commits and launches.

    A retry with the same run/token retains the original actor and reason. A
    failed request may be explicitly requeued, but never given a replacement
    token. Exact source/version validation remains inside the financial lock.
    """
    run_id = _identifier(run_id)
    _authorization(expected_digest, reviewer, reason)
    db.session.execute(text("SET LOCAL lock_timeout='3s'"))
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_item_post_requests'))"))
    key = _request_key(run_id, expected_digest)
    old = db.session.query(AmazonItemPostRuns).populate_existing().filter_by(request_key=key).one_or_none()
    if old:
        _request(old)
        if old.state != 'failed':
            return old
    active = db.session.query(AmazonItemPostRuns).filter(
        AmazonItemPostRuns.run_id == run_id, AmazonItemPostRuns.state.in_(ACTIVE)).first()
    if active:
        raise ValueError('A posting request is already queued or running for this review')
    if old:
        old.state = 'queued'
        old.progress = 'Retry queued using the original approved request'
        old.result = None
        old.updated_at = _now()
        db.session.flush()
        return old
    run = db.session.get(AmazonItemRuns, run_id, populate_existing=True)
    if not run or run.state not in ('completed', 'needs_attention'):
        raise ValueError('Complete item classification before approving financial posting')
    workflow._manifest(run)
    body = dict(version=VERSION, run_id=run_id, expected_digest=expected_digest,
                reviewer=reviewer.strip(), reason=reason.strip(), manifest_digest=run.input_digest)
    document = evidence('amazon_item_post_request', body, reviewer.strip())
    job = AmazonItemPostRuns(run_id=run_id, evidence_id=document.id, request_key=key,
        state='queued', progress='Approved posting is queued', updated_at=_now())
    db.session.add(job)
    db.session.flush()
    return job


def _safe_result(raw):
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in COUNT_FIELDS:
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 1000000:
            result[key] = value
    for key in AMOUNT_FIELDS:
        value = raw.get(key)
        if isinstance(value, str) and len(value) <= 40:
            try:
                amount = Decimal(value)
                if amount.is_finite() and amount >= 0 and amount == amount.quantize(Decimal('.01')):
                    result[key] = format(amount, '.2f')
            except InvalidOperation:
                pass
    return result or None


def _status(job):
    result = json.loads(job.result) if job.result else None
    error = result.get('error_code') if isinstance(result, dict) else None
    return dict(id=job.id, run_id=job.run_id, state=job.state, progress=job.progress[:500],
        result=_safe_result(result), revision=job.updated_at.isoformat(),
        error=error if error in ERROR_CODES else None)


def status(job_id):
    job = db.session.get(AmazonItemPostRuns, _identifier(job_id), populate_existing=True)
    if not job:
        raise ValueError('Missing posting request')
    return _status(job)


def latest_status(run_id):
    run_id = _identifier(run_id)
    job = db.session.query(AmazonItemPostRuns).populate_existing().filter_by(run_id=run_id).order_by(
        AmazonItemPostRuns.state.in_(ACTIVE).desc(), AmazonItemPostRuns.id.desc()).first()
    return _status(job) if job else None


def _lease_path(job_id):
    return Path('/run/user') / str(os.getuid()) / ('pacioli-amazon-post-{}.lock'.format(_identifier(job_id)))


def _progress(job_id, stage, **counts):
    """Do not hold the financial transaction open on a progress-row conflict."""
    labels = {
        'waiting_for_ledger': 'Waiting for the accounting ledger',
        'checking_sources': 'Rechecking source evidence and current approvals',
        'preparing_settlement': 'Preparing reviewed allocations',
        'settlement_ready': 'Prepared allocations; financial changes are not committed yet',
        'rebuilding_balances': 'Verifying account balances before commit',
        'ready_to_commit': 'Committing the complete reviewed batch',
    }
    message = labels.get(stage, 'Processing the approved financial batch')
    processed, total = counts.get('processed'), counts.get('total')
    if (type(processed) is int and type(total) is int and 0 <= processed <= total <= 1000000):
        message += ' ({} of {} settlements)'.format(processed, total)
    session = Session(bind=db.engine)
    try:
        session.execute(text("SET LOCAL lock_timeout='1s'"))
        session.execute(text("SET LOCAL statement_timeout='3s'"))
        session.query(AmazonItemPostRuns).filter_by(id=job_id, state='running').update(
            dict(progress=message, updated_at=_now()), synchronize_session=False)
        session.commit()
    except SQLAlchemyError:
        # Progress is advisory. An unavailable progress connection must not
        # alter the source-preserving accounting transaction's success/failure.
        session.rollback()
    finally:
        session.close()


def _failure(exc):
    if isinstance(exc, ValueError):
        message = str(exc).casefold()
        if 'closed' in message:
            return 'period_closed', 'Posting stopped because the affected accounting period is closed'
        if any(word in message for word in ('changed', 'stale', 'version')):
            return 'review_changed', 'Posting stopped because approvals or evidence changed; refresh the review'
        return 'validation_failed', 'Posting stopped because an accounting validation failed; review the evidence'
    return 'posting_failed', 'Posting stopped before completion; the original approved request can be retried'


def run_worker(job_id):
    job_id = _identifier(job_id)
    descriptor = os.open(_lease_path(job_id), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        job = db.session.get(AmazonItemPostRuns, job_id, populate_existing=True)
        if not job:
            raise ValueError('Missing posting request')
        if job.state == 'completed':
            return _status(job)
        request = _request(job)
        run = db.session.get(AmazonItemRuns, job.run_id, populate_existing=True)
        if not run or run.input_digest != request['manifest_digest']:
            raise ValueError('The approved review manifest changed')
        job.state = 'running'; job.progress = 'Starting the original approved posting request'
        job.result = None; job.updated_at = _now()
        db.session.commit()
        result = workflow.post_reviewed(request['run_id'], request['expected_digest'],
            request['reviewer'], request['reason'],
            report=lambda stage, **counts: _progress(job_id, stage, **counts))
        job = db.session.get(AmazonItemPostRuns, job_id, populate_existing=True)
        job.state = 'completed'; job.progress = 'The complete reviewed batch has been committed'
        job.result = dump(result); job.updated_at = _now()
        # The success record and every financial write commit together. If the
        # process dies earlier, replay starts from the original frozen token.
        db.session.commit()
        return _status(job)
    except Exception as exc:
        db.session.rollback()
        job = db.session.get(AmazonItemPostRuns, job_id, populate_existing=True)
        if job and job.state == 'completed':
            return _status(job)  # A commit succeeded despite a lost acknowledgement.
        if job:
            code, message = _failure(exc)
            job.state = 'failed'; job.progress = message
            job.result = dump(dict(error_code=code, error_type=type(exc).__name__))
            job.updated_at = _now(); db.session.commit()
        raise
    finally:
        os.close(descriptor)
