"""On-demand local-agent jobs and encrypted, resumable collection checkpoints."""
from datetime import date, datetime, timedelta, timezone
import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pacioli.extensions import db
from pacioli.models import ChaseBrowserRuns, ChaseLocalPreferences, Connections, OFXFileImports, ChaseExportCoverage
from pacioli.functions.chase_browser import (
    ACTIVE_STATUSES, ChaseBrowserError, _heartbeat, _launch_systemd, _run, _set_run,
    expire_stale_runs)
from pacioli.functions.chase_local_bridge import FirefoxBridge, LocalChaseError
from pacioli.functions.chase_local_controller import Collector
from pacioli.functions.chase_local_model import LocalModel
from pacioli.functions.chase_profile import browser_lock
from pacioli.functions.chase_local_events import record
from pacioli.functions.chase_coverage import account_ranges, merge_ranges

MODES = ('local_ai', 'local_preview')
MESSAGES = {
    'queued': 'Waiting for the local worker.',
    'observing': 'Reading the selected Chase export page.',
    'thinking': 'Checking the next action with the local model.',
    'select_account': 'Selecting the approved account.',
    'select_qfx_format': 'Selecting Quicken QFX format.',
    'set_date_range': 'Setting the requested dates.',
    'download_qfx': 'Downloading QFX. Complete any Firefox Save As dialog.',
    'importing': 'Checking and importing the QFX file.',
    'wait': 'Waiting for Chase to finish preparing the report.',
    'bridge_unavailable': 'Connect the Firefox extension from the toolbar on your Chase tab, then resume.',
    'extension_update_required': 'Reload the Pacioli extension in Firefox, click its button on the Chase tab, then press Update Chase again.',
    'select_chase_tab': 'Click the Pacioli extension on your Chase tab, then resume.',
    'chase_tab_not_active': 'Keep the selected Chase tab visible in its window, then resume.',
    'capture_permission_required': 'Click the Pacioli extension button again on the signed-in Chase tab, then resume. Firefox renews capture permission after sign-in changes the site.',
    'authentication_required': 'Complete Chase verification directly in Firefox, then resume here.',
    'export_controls_unrecognized': 'Open the account activity download form in the selected Chase tab, then resume.',
    'open_export_for_preview': 'Open the account activity download form before checking the model.',
    'select_custom_dates': 'Choose a custom date range in Chase before checking the model.',
    'model_needs_review': 'The model and page checks disagreed. Check the export form, then resume.',
    'stale_observation': 'The page changed before the action. Check the selected tab, then resume.',
    'local_model_busy': 'Another local model is running. Resume after it finishes.',
    'local_model_memory_pressure': 'The workstation needs more available memory. Resume later.',
    'local_model_not_installed': 'The approved local Qwen model is unavailable.',
    'local_model_unavailable': 'Local inference did not respond. Resume after Ollama is available.',
    'local_model_invalid_response': 'The local model returned an incomplete or invalid response. No proposed action was executed.',
    'local_model_gpu_unavailable': 'The local GPU did not respond to the readiness check.',
    'desktop_launch_failed': 'The desktop worker could not start. Saved intervals are retained.',
    'qfx_invalid': 'The downloaded file is not a valid OFX statement. It was not imported.',
    'qfx_identity_mismatch': 'The downloaded QFX did not match the requested account or currency. It was not imported.',
    'qfx_coverage_unverified': 'The QFX did not confirm the full requested date interval. Review the download before retrying.',
    'minimum_interval_exceeded': 'Chase refused even a single-day export. This account needs manual review.',
    'download_provenance_unavailable': 'Firefox could not verify the download source. The file was not imported.',
    'ambiguous_download': 'More than one download started. Stop other downloads before resuming.',
    'download_timeout': 'The download did not finish. Check Firefox before resuming.',
    'bank_export_failed': 'Chase reported a problem preparing the export. Completed imports were retained. Open the account again in Firefox and retry when Chase is ready.',
    'worker_interrupted': 'The worker stopped. Saved intervals can be resumed.',
    'coverage_missing': 'Imported date coverage is missing or invalid. Review the account before updating.',
    'history_out_of_range': 'An older coverage gap needs a manual export. Review the saved account history.',
    'no_accounts': 'Import a first Chase QFX to register an account.',
    'ambiguous_account_selection': 'Saved accounts are missing or ambiguous. Review the account choices below.',
    'invalid_preferences': 'Choose at least one account and an overlap of 1 to 90 days.',
    'another_run_active': 'Another Chase job is already running. Its progress is shown below.',
    'browser_or_import_failed': 'The browser or importer stopped. Completed imports were retained; retry the update after reviewing the page.',
}


def accounts():
    rows = db.session.execute(text('''
        SELECT DISTINCT a.id, COALESCE(bank.acctid, card.acctid) AS acctid, a.name
        FROM ofx.acctfrom a
        LEFT JOIN ofx.bankacctfrom bank ON bank.id = a.id
        LEFT JOIN ofx.ccacctfrom card ON card.id = a.id
        JOIN admin.ofx_file_imports i ON i.ofx_account_id = a.id
        JOIN admin.connections c ON c.id = i.connection_id
        JOIN bookkeeping.bank_feed_accounts b ON b.ofx_account_id = a.id
        WHERE c.org = 'Chase' AND b.currency = 'USD'
          AND COALESCE(bank.acctid, card.acctid) IS NOT NULL
        ORDER BY a.id
    ''')).fetchall()
    return [{'id': row.id, 'account_number': row.acctid,
             'last4': row.acctid[-4:], 'label': row.name or 'Chase account ending ' + row.acctid[-4:]}
            for row in rows]


def checkpoint(run):
    return json.loads(run.diagnostics) if run.diagnostics else {}


def latest(user_id=None):
    expire_stale_runs()
    query = db.session.query(ChaseBrowserRuns).filter(ChaseBrowserRuns.mode.in_(MODES))
    if user_id is not None:
        query = query.filter_by(requested_by_id=user_id)
    return query.order_by(ChaseBrowserRuns.id.desc()).first()


def preferences(user_id):
    row = db.session.get(ChaseLocalPreferences, user_id)
    return {'account_ids': list(row.account_ids), 'overlap_days': row.overlap_days} if row else {
        'account_ids': [a['id'] for a in accounts()], 'overlap_days': 7}


def approved_accounts(account_ids):
    try:
        chosen = sorted(set(int(i) for i in account_ids))
    except (ValueError, TypeError):
        raise LocalChaseError('ambiguous_account_selection') from None
    available = accounts()
    approved = [a for a in available if a['id'] in chosen]
    if not chosen:
        raise LocalChaseError('no_accounts')
    if len(approved) != len(chosen) or any(sum(b['last4'] == a['last4'] for b in available) != 1 for a in approved):
        raise LocalChaseError('ambiguous_account_selection')
    return approved


def save_preferences(user_id, account_ids, overlap_days):
    try:
        overlap = int(overlap_days)
        if not 1 <= overlap <= 90:
            raise ValueError()
    except (ValueError, TypeError):
        raise LocalChaseError('invalid_preferences') from None
    approved = approved_accounts(account_ids)
    row = db.session.get(ChaseLocalPreferences, user_id)
    if row is None:
        row = ChaseLocalPreferences(user_id=user_id)
        db.session.add(row)
    row.account_ids = [a['id'] for a in approved]
    row.overlap_days = overlap
    row.updated_at = datetime.now(timezone.utc)
    db.session.commit()


def incremental_ranges(intervals, today, overlap_days):
    """Repair interior gaps and refresh recent coverage for late-posted activity."""
    if not intervals or any(a is None or b is None or a > b for a, b in intervals):
        raise LocalChaseError('coverage_missing')
    merged = []
    for begin, end in sorted(intervals):
        if merged and begin <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    if merged[0][0] > today:
        raise LocalChaseError('coverage_missing')
    wanted = [(left[1] + timedelta(days=1), right[0] - timedelta(days=1))
              for left, right in zip(merged, merged[1:])]
    wanted.append((max(merged[0][0], min(merged[-1][1], today) - timedelta(days=overlap_days-1)), today))
    result = []
    for begin, end in sorted(wanted):
        end = min(end, today)
        if begin > end:
            continue
        if (today - begin).days > 1096:
            raise LocalChaseError('history_out_of_range')
        if result and begin <= result[-1][1] + timedelta(days=1):
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((begin, end))
    return result


def _balance_refresh_ranges(account_id, ranges, today):
    """Include known activity so quiet accounts still yield a balance-bearing QFX."""
    if not any(end == today for _, end in ranges):
        return ranges
    latest = db.session.execute(text('''
        SELECT MAX(dtposted) FROM ofx.stmttrn
        WHERE acctfrom_id=:account_id AND dtposted < :end
    '''), {'account_id': account_id,
           'end': datetime.combine(today + timedelta(days=1), datetime.min.time())}).scalar()
    if latest is None:
        return ranges
    latest = latest.date()
    # Chase returns no file at all for a quiet date range. Widen only the
    # balance refresh, retaining every requested day and every interior gap.
    # Previously imported activity is safe to fetch again through FITID checks.
    widened = [(min(begin, latest) if end == today else begin, end)
               for begin, end in ranges]
    if any((today - begin).days > 1096 for begin, _ in widened):
        raise LocalChaseError('history_out_of_range')
    return merge_ranges(widened)


def _update_jobs(approved, today, overlap_days):
    jobs = []
    for account in approved:
        intervals, invalid = account_ranges(account['id'])
        if invalid:
            raise LocalChaseError('coverage_missing')
        ranges = _balance_refresh_ranges(account['id'],
            incremental_ranges(intervals, today, overlap_days), today)
        for begin, end in ranges:
            jobs.append(dict(account_id=account['id'], account_number=account['account_number'],
                             last4=account['last4'], start=begin.isoformat(), end=end.isoformat()))
    return jobs


def update(user_id):
    """One click either reuses a running job, resumes it, or plans a fresh update."""
    expire_stale_runs()
    # Serialize button requests across web workers; the existing unique active
    # slot also excludes legacy browser jobs. Commit publishes the job first.
    db.session.execute(text('SELECT pg_advisory_xact_lock(724109, 1)'))
    active = db.session.query(ChaseBrowserRuns).filter(ChaseBrowserRuns.status.in_(ACTIVE_STATUSES)).first()
    if active:
        if active.requested_by_id == user_id and active.mode == 'local_ai':
            db.session.commit()
            return active
        db.session.rollback()
        raise LocalChaseError('another_run_active')
    prior = (db.session.query(ChaseBrowserRuns).filter_by(requested_by_id=user_id, mode='local_ai')
             .order_by(ChaseBrowserRuns.id.desc()).first())
    if prior and prior.status in ('needs_attention', 'failed') and checkpoint(prior).get('incremental') and checkpoint(prior).get('jobs'):
        resume(prior.id, user_id)
        return prior
    settings = preferences(user_id)
    approved = approved_accounts(settings['account_ids'])
    jobs = _update_jobs(approved, date.today(), settings['overlap_days'])
    if len(jobs) > 512:
        raise LocalChaseError('too_many_intervals')
    if db.session.get(ChaseLocalPreferences, user_id) is None:
        db.session.add(ChaseLocalPreferences(user_id=user_id, **settings))
    return _enqueue(user_id, jobs, True, incremental=True, overlap_days=settings['overlap_days'])


def public_status(run):
    if run is None:
        return None
    state = checkpoint(run)
    phase = state.get('phase', 'queued')
    code = run.error_code or phase
    if run.status == 'completed':
        message = ('Preview finished. Proposed action: ' + state.get('proposal', 'none') + '. No controls were activated.'
                   if run.mode == 'local_preview' else 'All selected intervals were imported. Posted balances and their bank as-of dates are shown below.')
    elif run.status == 'cancelled':
        message = 'Cancelled. Completed intervals were retained.'
    else:
        message = MESSAGES.get(code, 'The connector stopped for review. Error: ' + code)
    current = state.get('jobs', [None])[0] if state.get('jobs') else None
    return {'id': run.id, 'status': run.status, 'mode': run.mode, 'message': message,
            'error_code': run.error_code,
            'phase': phase, 'remaining': len(state.get('jobs', [])), 'completed': len(state.get('done', [])),
            'imported': run.imported_count, 'duplicates': run.duplicate_count,
            'new_transactions': sum(job.get('new_transactions', 0) for job in state.get('done', [])),
            'existing_transactions': sum(job.get('existing_transactions', 0) for job in state.get('done', [])),
            'balances': list(state.get('balances', {}).values()),
            'incremental': bool(state.get('incremental')),
            'created_at': run.created_at.isoformat(),
            'updated_at': run.updated_at.isoformat(),
            'finished_at': run.finished_at.isoformat() if run.finished_at else None,
            'cancel_requested': run.cancel_requested,
            'intervals': [dict({k: job[k] for k in ('last4', 'start', 'end')},
                               status=status, duplicate=job.get('duplicate', False))
                          for status, jobs in [('completed', state.get('done', [])),
                                               ('pending', state.get('jobs', []))]
                          for job in jobs],
            'current': {k: current[k] for k in ('last4','start','end')} if current else None,
            'active': run.status in ACTIVE_STATUSES,
            'resumable': run.status in ('needs_attention','failed','cancelled') and bool(state.get('jobs'))}


def start(user_id, account_ids, start, end, reviewed, preview=False):
    if not reviewed and not preview:
        raise LocalChaseError('review_usd_required')
    try:
        begin, finish = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise LocalChaseError('invalid_export_request') from None
    if begin > finish or finish > date.today() or (finish-begin).days > 1096:
        raise LocalChaseError('invalid_export_request')
    approved = approved_accounts(account_ids)
    expire_stale_runs()
    jobs = [dict(account_id=a['id'], account_number=a['account_number'], last4=a['last4'], start=start, end=end) for a in approved]
    return _enqueue(user_id, jobs, reviewed, preview=preview)


def _enqueue(user_id, jobs, reviewed, preview=False, incremental=False, overlap_days=7):
    if db.session.query(ChaseBrowserRuns).filter(ChaseBrowserRuns.status.in_(ACTIVE_STATUSES)).first():
        raise LocalChaseError('another_run_active')
    connection = db.session.query(Connections).filter_by(source='chase_browser', org='Chase').first()
    if connection is None:
        connection = Connections(source='chase_browser', org='Chase', type='Browser QFX', currency='USD', url='https://secure.chase.com/')
        db.session.add(connection); db.session.flush()
    run = ChaseBrowserRuns(connection_id=connection.id, requested_by_id=user_id, reviewed_usd=reviewed,
        status='queued', mode='local_preview' if preview else 'local_ai',
        diagnostics=json.dumps({'version': 2, 'incremental': incremental, 'overlap_days': overlap_days,
                                'target_date': date.today().isoformat(), 'phase': 'queued', 'jobs': jobs, 'done': []}),
        updated_at=datetime.now(timezone.utc))
    db.session.add(run)
    try:
        db.session.flush()
        record(run.id, 'queued', commit=False, intervals=len(jobs))
        db.session.commit()
    except IntegrityError:
        db.session.rollback(); raise LocalChaseError('another_run_active') from None
    launch(run)
    return run


def launch(run):
    try:
        _launch_systemd(run.id)
    except Exception:
        _set_run(run.id, 'failed', error_code='desktop_launch_failed')
        raise LocalChaseError('desktop_launch_failed') from None


def resume(run_id, user_id):
    run = _run(run_id)
    if run.mode not in MODES or run.requested_by_id != user_id or not public_status(run)['resumable']:
        raise LocalChaseError('run_not_resumable')
    expire_stale_runs()
    if db.session.query(ChaseBrowserRuns).filter(ChaseBrowserRuns.status.in_(ACTIVE_STATUSES)).first():
        raise LocalChaseError('another_run_active')
    run.status = 'queued'; run.cancel_requested = False; run.error_code = None
    run.finished_at = None; run.updated_at = datetime.now(timezone.utc)
    state = checkpoint(run)
    # A run interrupted yesterday must refresh even its completed accounts
    # through today. Preserve its original account selection and pending gaps.
    target = state.get('target_date') or max(job['end'] for job in state['jobs'] + state.get('done', []))
    if state.get('incremental') and target < date.today().isoformat():
        old_jobs = state['jobs'] + state.get('done', [])
        approved = approved_accounts({job['account_id'] for job in old_jobs})
        additions = _update_jobs(approved, date.today(), state.get('overlap_days', 7))
        jobs = []
        for account in approved:
            ranges = [(date.fromisoformat(job['start']), date.fromisoformat(job['end']))
                      for job in state['jobs'] + additions if job['account_id'] == account['id']]
            for begin, end in merge_ranges(ranges):
                jobs.append(dict(account_id=account['id'], account_number=account['account_number'],
                                 last4=account['last4'], start=begin.isoformat(), end=end.isoformat()))
        if len(jobs) > 512:
            raise LocalChaseError('too_many_intervals')
        state['jobs'] = jobs
        state['target_date'] = date.today().isoformat()
    if state.get('incremental'):
        # Also repair a saved quiet-account interval created by older workers.
        # Completed work and the originally approved account choices stay put.
        jobs = []
        for account_id in dict.fromkeys(job['account_id'] for job in state['jobs']):
            pending = [job for job in state['jobs'] if job['account_id'] == account_id]
            ranges = [(date.fromisoformat(job['start']), date.fromisoformat(job['end'])) for job in pending]
            for begin, end in _balance_refresh_ranges(account_id, ranges, date.today()):
                jobs.append(dict(pending[0], start=begin.isoformat(), end=end.isoformat()))
        state['jobs'] = jobs
    state['phase'] = 'queued'; run.diagnostics = json.dumps(state)
    try:
        record(run.id, 'resumed', commit=False, intervals=len(state['jobs']))
        db.session.commit()
    except IntegrityError:
        db.session.rollback(); raise LocalChaseError('another_run_active') from None
    launch(run)


def run_local_worker(run_id):
    run = _run(run_id)
    state = checkpoint(run)
    bridge = FirefoxBridge(heartbeat=lambda: _heartbeat(run_id))

    def report(kind, **details):
        job = state['jobs'][0] if state.get('jobs') else {}
        safe_job = {k: job[k] for k in ('last4', 'start', 'end') if k in job}
        record(run_id, kind, **dict(safe_job, **details))

    def save(value):
        current = _run(run_id)
        current.diagnostics = json.dumps(value)
        current.imported_count = sum(not d['duplicate'] for d in value['done'])
        current.duplicate_count = sum(d['duplicate'] for d in value['done'])
        _set_run(run_id, updated_at=datetime.now(timezone.utc))

    def progress(phase, job):
        state['phase'] = phase
        save(state)
        _set_run(run_id, 'importing' if phase == 'importing' else 'exporting')

    failure = None
    try:
        report('worker_started')
        report('browser_lock')
        with browser_lock():
            report('browser_connect')
            connected = bridge.command('ping')
            if not connected.get('ready'):
                raise LocalChaseError('select_chase_tab')
            report('browser_connected')
            if run.mode != 'local_preview':
                if connected.get('version', 1) < 2:
                    raise LocalChaseError('extension_update_required')
                report('browser_prepare')
                bridge.command('prepare')
                report('browser_ready')
            with LocalModel(report=report, heartbeat=lambda: _heartbeat(run_id)) as model:
                result = Collector(bridge, model, state, save, lambda: _heartbeat(run_id), progress,
                                   preview=run.mode == 'local_preview', report=report).collect()
                if run.mode == 'local_preview':
                    state['proposal'] = result['action']; save(state)
    except LocalChaseError as error:
        db.session.rollback()
        failure = error.code[:48]
    except BaseException:
        db.session.rollback()
        raise
    finally:
        # Cancellation must not prevent cleaning up the download correlation state.
        bridge.heartbeat = lambda: None
        try:
            try:
                report('browser_release')
            finally:
                bridge.command('release')
            report('browser_released')
        except LocalChaseError:
            report('cleanup_warning')
    # Terminal events follow cleanup, so a reconnect sees the entire trace.
    _set_run(run_id, 'needs_attention' if failure else 'completed', error_code=failure)
