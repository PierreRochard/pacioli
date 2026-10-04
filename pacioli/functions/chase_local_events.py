"""Safe, replayable local-sync events. Never accept page text or model output."""
from datetime import date, datetime, timezone
import json
import re
import time
from threading import BoundedSemaphore

from pacioli.extensions import db
from pacioli.models import ChaseBrowserRuns, ChaseLocalEvents

MESSAGES = {
    'queued': 'Update queued; waiting for the desktop worker.',
    'resumed': 'Resuming saved intervals; completed imports are retained.',
    'worker_started': 'Desktop worker started.',
    'browser_lock': 'Acquiring the Firefox connector lock.',
    'browser_connect': 'Connecting to the selected Firefox tab.',
    'browser_connected': 'Firefox extension connection verified.',
    'browser_prepare': 'Preparing the Chase window for background collection.',
    'browser_ready': 'Chase window ready.',
    'model_check': 'Checking available memory, GPU and the installed local model.',
    'model_ready': 'Local model checks passed.',
    'interval_started': 'Starting account interval.',
    'page_read': 'Reading Chase page state and export controls.',
    'page_ready': 'Chase page state received.',
    'page_retry': 'Chase is changing pages; waiting before reading again.',
    'action_started': 'Sending a verified browser action.',
    'action_finished': 'Firefox acknowledged the browser action.',
    'date_from': 'Setting the start date.',
    'date_to': 'Setting the end date.',
    'model_request': 'Asking the local model to check the next action; loading and processing may take time.',
    'model_output': 'Receiving the local model response.',
    'model_response': 'Local model response received and checked.',
    'model_agreed': 'Model and deterministic page checks agree.',
    'download_wait': 'Waiting for Chase to prepare the QFX and Firefox to finish downloading.',
    'download_received': 'Firefox verified the download source and reported a completed file.',
    'file_read': 'Checking and reading the downloaded QFX file.',
    'file_ready': 'Downloaded file is stable and readable.',
    'qfx_validate': 'Validating the QFX account, USD currency, transaction dates and export coverage.',
    'qfx_validated': 'QFX identity, currency and date checks passed.',
    'import_lock': 'Waiting for the accounting import lock.',
    'import_deduplicate': 'Checking whether this exact file was already imported.',
    'import_parse': 'Parsing QFX transactions and checking transaction identities.',
    'import_validate': 'Checking parsed transactions and the approved account.',
    'import_save': 'Saving transactions, encrypted source and verified coverage in one transaction.',
    'import_commit': 'Committing the import transaction.',
    'import_committed': 'Import committed successfully.',
    'balance_captured': 'Captured the QFX posted balance and bank as-of date; pending and available balances are excluded.',
    'opening_ready': 'Calculated an opening-balance candidate from connected coverage and signed transactions. Statement reconciliation is still required.',
    'opening_incomplete': 'Opening-balance calculation is blocked by incomplete coverage or missing balance data.',
    'checkpoint_saved': 'Saved interval checkpoint; this interval is complete.',
    'interval_split': 'Chase reported an export limit; splitting this interval into smaller date ranges.',
    'model_unload': 'Releasing local model GPU memory.',
    'model_unloaded': 'Local model unload request completed.',
    'browser_release': 'Releasing Firefox download tracking.',
    'browser_released': 'Firefox download tracking released.',
    'cleanup_warning': 'Firefox cleanup could not be confirmed; saved imports are retained.',
    'cancel_requested': 'Cancellation requested; waiting for the current operation to return.',
    'completed': 'Run completed.',
    'needs_attention': 'Run paused; your attention is needed.',
    'failed': 'Run stopped; saved intervals are retained.',
    'cancelled': 'Run cancelled; saved intervals are retained.',
}
ACTIONS = frozenset(('open_account', 'open_activity', 'open_export', 'reduce_date_range',
    'select_account', 'select_qfx_format', 'set_date_range', 'choose_option', 'download_qfx', 'wait'))
PAGE_KINDS = frozenset(('accounts', 'overview', 'form', 'download_complete', 'limit',
                        'unknown', 'authentication', 'error'))
NUMBERS = frozenset(('duration_ms', 'attempt', 'bytes', 'chunks', 'characters',
                     'transactions', 'new_transactions', 'existing_transactions',
                     'intervals', 'prompt_tokens', 'output_tokens', 'load_ms'))
PAGE_SIZE = 200
STREAM_SECONDS = 20
STREAM_INTERVAL = .25
# Leave half the eight web threads available for ordinary requests and polling.
STREAM_SLOTS = BoundedSemaphore(4)


def safe_details(details):
    result = {}
    for key, value in details.items():
        if key in NUMBERS and type(value) is int and 0 <= value <= 10**12:
            result[key] = value
        elif key == 'last4' and isinstance(value, str) and re.fullmatch(r'[0-9]{4}', value):
            result[key] = value
        elif key in ('start', 'end') and isinstance(value, str):
            try:
                if date.fromisoformat(value).isoformat() == value:
                    result[key] = value
            except ValueError:
                pass
        elif key == 'action' and isinstance(value, str) and value in ACTIONS:
            result[key] = value
        elif key == 'page' and isinstance(value, str) and value in PAGE_KINDS:
            result[key] = value
        elif key == 'duplicate' and type(value) is bool:
            result[key] = value
        elif key == 'reason' and isinstance(value, str):
            from pacioli.functions.chase_local import MESSAGES as REASONS
            if value in REASONS:
                result[key] = value
    return result


def record(run_id, kind, *, commit=True, **details):
    if kind not in MESSAGES:
        raise ValueError('Unknown local Chase event')
    # Every event writer holds this row lock until commit: cursor order is also
    # commit order, including simultaneous cancellation from the web process.
    run = db.session.query(ChaseBrowserRuns).filter_by(id=run_id).with_for_update().one()
    event = ChaseLocalEvents(run_id=run.id, kind=kind,
        occurred_at=datetime.now(timezone.utc), details=json.dumps(safe_details(details)))
    db.session.add(event)
    if commit:
        db.session.commit()
    return event


def terminal_event(run, previous):
    if run.mode in ('local_ai', 'local_preview') and run.status != previous and run.status in (
            'completed', 'needs_attention', 'failed', 'cancelled'):
        record(run.id, run.status, commit=False, reason=run.error_code)


def serialize(event):
    details = safe_details(json.loads(event.details))
    message = MESSAGES[event.kind]
    if details.get('reason'):
        from pacioli.functions.chase_local import MESSAGES as REASONS
        message += ' ' + REASONS[details['reason']]
    return {'id': event.id, 'at': event.occurred_at.isoformat(), 'kind': event.kind,
            'message': message, 'details': details}


def snapshot(user_id, run_id=0, after=0):
    from pacioli.functions.chase_local import latest, public_status
    run = latest(user_id)
    if run is None:
        return {'run': None, 'events': [], 'cursor': 0, 'has_more': False}
    after = after if run.id == run_id else 0
    # No account choices, checkpoint secrets, file paths, URLs or raw model text
    # cross this API. Both current state and history belong to the signed-in user.
    rows = (db.session.query(ChaseLocalEvents).filter_by(run_id=run.id)
            .filter(ChaseLocalEvents.id > after).order_by(ChaseLocalEvents.id)
            .limit(PAGE_SIZE + 1).all())
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    last = (db.session.query(ChaseLocalEvents).filter_by(run_id=run.id)
            .order_by(ChaseLocalEvents.id.desc()).first())
    status = public_status(run)
    status['last_event'] = serialize(last) if last else None
    if status['active'] and last:
        status['message'] = MESSAGES[last.kind]
    return {'run': status, 'events': [serialize(row) for row in rows],
            'cursor': rows[-1].id if rows else after, 'has_more': more}


def stream(user_id, run_id, after):
    """Bounded SSE connection. Release DB connections before yielding or waiting."""
    deadline = time.monotonic() + STREAM_SECONDS
    previous = None
    last_ping = time.monotonic()
    try:
        yield 'retry: 1000\n\n'
        while time.monotonic() < deadline:
            packet = snapshot(user_id, run_id, after)
            db.session.remove()
            encoded = json.dumps(packet, separators=(',', ':'))
            if encoded != previous:
                current = packet['run']
                run_id = current['id'] if current else 0
                after = packet['cursor']
                yield 'id: {}:{}\nevent: progress\ndata: {}\n\n'.format(run_id, after, encoded)
                previous = encoded
            if not packet['has_more'] and (not packet['run'] or not packet['run']['active']):
                yield 'event: idle\ndata: {}\n\n'
                return
            if time.monotonic() - last_ping >= 5:
                yield 'event: heartbeat\ndata: {}\n\n'
                last_ping = time.monotonic()
            if not packet['has_more']:
                time.sleep(STREAM_INTERVAL)
        yield 'event: reconnect\ndata: {}\n\n'
    finally:
        db.session.remove()
