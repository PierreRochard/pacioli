"""On-demand Chase QFX export through the owner's running Firefox."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy.exc import IntegrityError

from pacioli.extensions import db
from pacioli.functions.chase_profile import (ProfileBusy, ProfileUnavailable, _private_directory,
                                             browser_lock)
from pacioli.functions.ofx_file_import import (AccountNotApproved, MAX_OFX_BYTES,
                                                import_chase_qfx)
from pacioli.models import ChaseBrowserCredentials, ChaseBrowserRuns


ACTIVE_STATUSES = ('queued', 'signing_in', 'waiting_for_mfa',
                   'choosing_accounts', 'exporting', 'importing')
TERMINAL_STATUSES = ('completed', 'partial', 'failed', 'cancelled',
                     'needs_attention')
CHASE_HOSTS = frozenset(('chase.com', 'secure.chase.com', 'www.chase.com'))
STALE_AFTER = timedelta(minutes=5)
MFA_TIMEOUT_SECONDS = 10 * 60
ACCOUNT_CHOICE_TIMEOUT_SECONDS = 5 * 60
HEADLESS_CHALLENGE_SECONDS = 25
SIGNIN_ONLY_MODES = ('firefox_test',)


class ChaseBrowserError(Exception):
    def __init__(self, code):
        self.code = code
        super(ChaseBrowserError, self).__init__(code)


class ChaseBrowserCancelled(ChaseBrowserError):
    def __init__(self):
        super(ChaseBrowserCancelled, self).__init__('cancelled')


class ChaseBrowserAttention(ChaseBrowserError):
    """A bank challenge or account choice requires a supervised retry."""


def _now():
    return datetime.now(timezone.utc)


def _run(run_id):
    run = db.session.get(ChaseBrowserRuns, run_id)
    if run is None:
        raise ChaseBrowserError('run_missing')
    return run


def _set_run(run_id, status=None, **updates):
    run = _run(run_id)
    previous = run.status
    if run.status in TERMINAL_STATUSES and status in ACTIVE_STATUSES:
        raise ChaseBrowserCancelled()
    if status is not None:
        run.status = status
    for name, value in updates.items():
        setattr(run, name, value)
    run.updated_at = _now()
    if run.status in TERMINAL_STATUSES:
        run.finished_at = run.updated_at
        run.mfa_code = None
        run.mfa_prompt = None
        run.browser_requested = False
        run.browser_visible = False
    from pacioli.functions.chase_local_events import terminal_event
    terminal_event(run, previous)
    db.session.commit()
    return run


def expire_stale_runs():
    """Make jobs orphaned by a web or desktop restart restartable."""
    stale = (db.session.query(ChaseBrowserRuns)
             .filter(ChaseBrowserRuns.status.in_(ACTIVE_STATUSES))
             .filter(ChaseBrowserRuns.updated_at < _now() - STALE_AFTER)
             .all())
    for run in stale:
        previous = run.status
        run.status = 'cancelled' if run.cancel_requested else 'failed'
        run.error_code = 'worker_interrupted' if not run.cancel_requested else None
        run.mfa_code = None
        run.mfa_prompt = None
        run.browser_requested = False
        run.browser_visible = False
        run.updated_at = _now()
        run.finished_at = run.updated_at
        from pacioli.functions.chase_local_events import terminal_event
        terminal_event(run, previous)
    if stale:
        db.session.commit()


def latest_run(connection_id):
    expire_stale_runs()
    return (db.session.query(ChaseBrowserRuns)
            .filter_by(connection_id=connection_id)
            .order_by(ChaseBrowserRuns.id.desc()).first())


def _launch_systemd(run_id):
    """Start outside the web service's NoNewPrivileges sandbox.

    A containerized web service has no systemd user manager. It sets
    CHASE_LAUNCH_QUEUE instead and leaves an empty file named after the run;
    scripts/chase_launcher.py, run by a host path unit, starts the worker.
    """
    queue = current_app.config.get('CHASE_LAUNCH_QUEUE')
    if queue:
        descriptor = os.open(_private_directory(Path(queue)) / str(int(run_id)),
                             os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        return
    systemd_run(run_id)


def systemd_run(run_id):
    """Start the worker for one run as a transient systemd user service."""
    runtime = os.environ.get('XDG_RUNTIME_DIR') or '/run/user/{}'.format(os.getuid())
    environment = os.environ.copy()
    environment['XDG_RUNTIME_DIR'] = runtime
    environment.setdefault('DBUS_SESSION_BUS_ADDRESS',
                           'unix:path={}/bus'.format(runtime))
    project = Path(__file__).resolve().parents[2]
    command = [
        'systemd-run', '--user', '--collect',
        '--unit=pacioli-chase-{}'.format(run_id),
        '--property=WorkingDirectory={}'.format(project),
        '--property=UMask=0077',
        '--property=RuntimeMaxSec=40min',
        '--property=MemoryMax=3G',
        '--property=StandardOutput=null',
        '--property=StandardError=null',
        sys.executable, '-m', 'pacioli.chase_worker', str(run_id),
    ]
    subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=True, timeout=15)


def start_run(connection_id, requested_by_id, reviewed_usd, mode='interactive'):
    if mode not in ('interactive', 'automatic') + SIGNIN_ONLY_MODES:
        raise ChaseBrowserError('invalid_run_mode')
    if mode not in SIGNIN_ONLY_MODES and not reviewed_usd:
        raise ChaseBrowserError('review_usd_required')
    credentials = (db.session.query(ChaseBrowserCredentials)
                   .filter_by(connection_id=connection_id).first())
    if credentials is None:
        raise ChaseBrowserError('credentials_missing')
    if mode == 'automatic' and (
            not credentials.selected_account_labels or
            not credentials.selected_ofx_account_ids or
            len(credentials.selected_account_labels) !=
            len(credentials.selected_ofx_account_ids)):
        raise ChaseBrowserError('manual_run_required')
    expire_stale_runs()
    if db.session.query(ChaseBrowserRuns).filter(
            ChaseBrowserRuns.status.in_(ACTIVE_STATUSES)).first():
        raise ChaseBrowserError('another_run_active')
    run = ChaseBrowserRuns(connection_id=connection_id,
                           requested_by_id=requested_by_id,
                           reviewed_usd=bool(reviewed_usd), status='queued', mode=mode,
                           updated_at=_now())
    db.session.add(run)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ChaseBrowserError('another_run_active')
    try:
        _launch_systemd(run.id)
    except (OSError, subprocess.SubprocessError):
        _set_run(run.id, 'failed', error_code='desktop_launch_failed')
        raise ChaseBrowserError('desktop_launch_failed')
    return run


def cancel_run(run_id):
    run = _run(run_id)
    if run.status in ACTIVE_STATUSES:
        first_request = not run.cancel_requested
        run.cancel_requested = True
        run.updated_at = _now()
        if first_request and run.mode in ('local_ai', 'local_preview'):
            from pacioli.functions.chase_local_events import record
            record(run_id, 'cancel_requested', commit=False)
        db.session.commit()
    return run


def request_browser(run_id):
    run = _run(run_id)
    if (run.cancel_requested or run.mode != 'automatic' or run.browser_visible
            or run.status != 'waiting_for_mfa' or run.mfa_prompt != 'open_browser'):
        raise ChaseBrowserError('browser_not_requested')
    run.browser_requested = True
    run.updated_at = _now()
    db.session.commit()


def submit_mfa_code(run_id, code):
    run = _run(run_id)
    normalized = re.sub(r'[\s-]+', '', code or '')
    if run.cancel_requested or run.status != 'waiting_for_mfa' or run.mfa_prompt != 'code':
        raise ChaseBrowserError('mfa_code_not_requested')
    if not re.fullmatch(r'[0-9]{4,12}', normalized):
        raise ChaseBrowserError('invalid_mfa_code')
    run.mfa_code = normalized
    run.updated_at = _now()
    db.session.commit()


def select_accounts(run_id, indices):
    run = _run(run_id)
    if run.status != 'choosing_accounts':
        raise ChaseBrowserError('not_choosing_accounts')
    try:
        selected = sorted(set(int(value) for value in indices))
    except (TypeError, ValueError):
        raise ChaseBrowserError('invalid_account_selection')
    allowed = {choice['index'] for choice in (run.account_choices or [])}
    if not selected or not set(selected).issubset(allowed):
        raise ChaseBrowserError('invalid_account_selection')
    run.selected_accounts = selected
    run.updated_at = _now()
    db.session.commit()
    return run


def _heartbeat(run_id):
    db.session.expire_all()
    run = _run(run_id)
    if run.cancel_requested or run.status not in ACTIVE_STATUSES:
        raise ChaseBrowserCancelled()
    run.updated_at = _now()
    db.session.commit()
    return run


def _check_chase_page(page):
    parsed = urlsplit(page.url)
    if parsed.scheme != 'https':
        raise ChaseBrowserError('unexpected_browser_host')
    if parsed.path == '/logout' and parsed.hostname in CHASE_HOSTS:
        raise ChaseBrowserError('chase_signed_out')
    if parsed.hostname not in CHASE_HOSTS:
        raise ChaseBrowserError('unexpected_browser_host')


def _signed_in_location(page):
    for locator in (
            page.locator('a#heading').filter(has_text='See all transactions'),
            page.locator('button[aria-label="Download account activity"]')):
        if locator.count() and locator.first.is_visible():
            return True
    return False


def _mfa_code_input(page):
    """Find exactly one visible code field on a Chase-controlled page."""
    selector = ("input[autocomplete='one-time-code'], "
                "input[name*='verificationcode' i], input[id*='verificationcode' i], "
                "input[name*='onetimecode' i], input[id*='onetimecode' i], "
                "input[name*='otp' i], input[id*='otp' i]")
    candidates = []
    for frame in page.frames:
        if urlsplit(frame.url).hostname not in CHASE_HOSTS:
            continue
        fields = frame.locator(selector)
        for index in range(fields.count()):
            field = fields.nth(index)
            if field.is_visible() and field.get_attribute('type') != 'hidden':
                candidates.append(field)
    return candidates[0] if len(candidates) == 1 else None


def _wait_for_accounts(page, run_id):
    deadline = time.monotonic() + MFA_TIMEOUT_SECONDS
    run = _run(run_id)
    automatic = run.mode == 'automatic' and not run.browser_visible
    unknown_since = None
    while time.monotonic() < deadline:
        run = _heartbeat(run_id)
        _check_chase_page(page)
        if _signed_in_location(page):
            if run.mfa_prompt:
                _set_run(run_id, 'waiting_for_mfa', mfa_prompt=None)
            return
        if hasattr(page, 'capture_error_signals') and page.capture_error_signals():
            raise ChaseBrowserAttention('chase_signin_error')
        field = _mfa_code_input(page)
        if field is not None:
            unknown_since = None
            if run.mfa_prompt != 'code':
                _set_run(run_id, 'waiting_for_mfa', mfa_prompt='code')
            if run.mfa_code:
                code = run.mfa_code
                run.mfa_code = None
                db.session.commit()
                _check_chase_page(page)
                field.fill(code)
                field.press('Enter')
                code = None
        elif automatic:
            if unknown_since is None:
                unknown_since = time.monotonic()
            if time.monotonic() - unknown_since > HEADLESS_CHALLENGE_SECONDS:
                raise ChaseBrowserAttention('interactive_verification_needed')
        elif run.mfa_prompt != 'browser':
            _set_run(run_id, 'waiting_for_mfa', mfa_prompt='browser')
        page.wait_for_timeout(1000)
    if automatic:
        raise ChaseBrowserAttention('mfa_timeout')
    raise ChaseBrowserError('mfa_timeout')


def _wait_for_browser_request(run_id):
    _set_run(run_id, 'waiting_for_mfa', mfa_prompt='open_browser', mfa_code=None,
             browser_requested=False, browser_visible=False)
    deadline = time.monotonic() + MFA_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        run = _heartbeat(run_id)
        if run.browser_requested:
            _set_run(run_id, 'signing_in', mfa_prompt=None,
                     browser_requested=False, browser_visible=True)
            return
        time.sleep(1)
    raise ChaseBrowserAttention('verification_window_timeout')


def _open_export(page):
    _check_chase_page(page)
    download_action = page.locator('button[aria-label="Download account activity"]')
    if not download_action.count():
        page.locator('a#heading').filter(
            has_text='See all transactions').first.click(timeout=10000)
    download_action.click(timeout=15000)
    page.locator('#downloadButton').wait_for(state='visible', timeout=15000)
    _check_chase_page(page)


def mask_account_label(raw, index):
    """Show only an ordinal and optional last four, never the bank's label."""
    suffix = re.search(
        r'(?:ending\s+(?:in|with)|[•*xX]{2,}|\.{3,})\s*(\d{4})(?!\d)',
        raw or '', flags=re.IGNORECASE)
    label = 'Chase account {}'.format(index + 1)
    if suffix:
        label += ' (•••• {})'.format(suffix.group(1))
    return label


def _account_choices(page):
    select = page.locator('mds-select[name="downloadActivityAccounts"]')
    if not select.count():
        return [{'index': 0, 'label': 'Current Chase account'}]
    options = select.locator('mds-select-option')
    if not options.count():
        return [{'index': 0, 'label': 'Current Chase account'}]
    page.locator('#select-account_options_id-selector-no-label').click(timeout=7000)
    choices = []
    for index in range(options.count()):
        option = options.nth(index)
        if not option.is_visible():
            continue
        raw = option.inner_text(timeout=2000)
        choices.append({'index': index,
                        'label': mask_account_label(raw, len(choices))})
    page.keyboard.press('Escape')
    return choices or [{'index': 0, 'label': 'Current Chase account'}]


def _wait_for_account_selection(run_id, choices, credentials):
    if _run(run_id).mode == 'automatic':
        saved = credentials.selected_account_labels or []
        labels = [choice['label'] for choice in choices]
        if (not saved or any('••••' not in label for label in saved) or
                len(labels) != len(set(labels)) or
                not set(saved).issubset(set(labels))):
            raise ChaseBrowserAttention('account_selection_changed')
        return [choice['index'] for choice in choices if choice['label'] in saved]
    if len(choices) == 1:
        return [choices[0]['index']]
    _set_run(run_id, 'choosing_accounts', account_choices=choices)
    deadline = time.monotonic() + ACCOUNT_CHOICE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        run = _heartbeat(run_id)
        if run.selected_accounts:
            return run.selected_accounts
        time.sleep(1)
    raise ChaseBrowserError('account_choice_timeout')


def _select_option(page, button_selector, option_selector):
    page.locator(button_selector).click(timeout=7000)
    page.locator(option_selector).click(timeout=7000)


def _download_account(page, index):
    account_select = page.locator('mds-select[name="downloadActivityAccounts"]')
    if account_select.count() and account_select.locator('mds-select-option').count():
        page.locator('#select-account_options_id-selector-no-label').click(timeout=7000)
        account_select.locator('mds-select-option').nth(index).click(timeout=7000)
    _select_option(page, '#select-showing-file-select-selector-no-label',
                   'mds-select-option[id="showing-file-select-quicken web connect (qfx)"]')
    _select_option(page, '#select-showing-activity-select-selector-no-label',
                   'mds-select-option[id="showing-activity-select-last24monthsoption"]')
    _check_chase_page(page)
    with page.expect_download(timeout=45000) as event:
        page.locator('#downloadButton').click(timeout=10000)
    download = event.value
    if not download.suggested_filename.lower().endswith(('.qfx', '.ofx')):
        raise ChaseBrowserError('not_qfx_download')
    content = download.read_content(MAX_OFX_BYTES + 1)
    if not content or len(content) > MAX_OFX_BYTES:
        raise ChaseBrowserError('qfx_size_invalid')
    return content


def _export_accounts(page, run_id, credentials):
    run = _run(run_id)
    _open_export(page)
    choices = _account_choices(page)
    indices = _wait_for_account_selection(run_id, choices, credentials)
    _set_run(run_id, 'exporting', account_choices=choices)
    failures = 0
    successful_ids = []
    for index in indices:
        _heartbeat(run_id)
        _check_chase_page(page)
        content = _download_account(page, index)
        _heartbeat(run_id)
        _set_run(run_id, 'importing')
        try:
            result = import_chase_qfx(
                content, reviewed_usd=True,
                allowed_account_ids=(
                    credentials.selected_ofx_account_ids
                    if run.mode == 'automatic' else None))
        except AccountNotApproved:
            db.session.rollback()
            raise ChaseBrowserAttention('account_selection_changed')
        except ValueError:
            db.session.rollback()
            failures += 1
        else:
            successful_ids.append(result['account_id'])
            run = _run(run_id)
            if result['duplicate']:
                run.duplicate_count += 1
            else:
                run.imported_count += 1
            run.updated_at = _now()
            db.session.commit()
        finally:
            content = None
        _set_run(run_id, 'exporting')
    run = _run(run_id)
    if not failures and len(successful_ids) == len(indices):
        labels = [choice['label'] for choice in choices
                  if choice['index'] in indices]
        if (len(labels) == len(set(labels)) and
                all('••••' in label for label in labels) and
                len(set(successful_ids)) == len(successful_ids)):
            credentials.selected_account_labels = labels
            credentials.selected_ofx_account_ids = successful_ids
        else:
            credentials.selected_account_labels = None
            credentials.selected_ofx_account_ids = None
        # Firefox owns the Chase session and LastPass state; Pacioli keeps neither.
        credentials.updated_at = _now()
        db.session.commit()
    elif run.mode == 'interactive':
        credentials.selected_account_labels = None
        credentials.selected_ofx_account_ids = None
        db.session.commit()
    if failures:
        _set_run(run_id, 'partial', error_code='some_imports_failed')
    else:
        _set_run(run_id, 'completed')


def _run_browser(run_id, credentials):
    run = _run(run_id)
    if run.mode not in SIGNIN_ONLY_MODES and not run.reviewed_usd:
        raise ChaseBrowserError('review_usd_required')
    try:
        with browser_lock():
            _heartbeat(run_id)
            _run_firefox_browser(run_id, credentials)
    except ProfileBusy:
        raise ChaseBrowserAttention('browser_profile_busy') from None
    except ProfileUnavailable:
        raise ChaseBrowserError('browser_profile_unavailable') from None


def _run_firefox_browser(run_id, credentials):
    from pacioli.functions.firefox_bidi import FirefoxConnection, FirefoxError
    from pacioli.functions.firefox_diagnostics import FirefoxDiagnostics
    run = _run(run_id)
    port = current_app.config.get('CHASE_FIREFOX_PORT', 9222)
    diagnostics = FirefoxDiagnostics(run_id)

    def downloaded():
        _set_run(run_id, 'exporting', mfa_prompt='download')

    try:
        diagnostics.record('stage', name='connecting')
        with FirefoxConnection(port, heartbeat=lambda: _heartbeat(run_id),
                download_started=downloaded, diagnostic=diagnostics.record) as browser:
            page = browser.page
            visible = run.mode != 'automatic'
            _set_run(run_id, 'signing_in', browser_visible=visible)
            diagnostics.record('stage', name='opening_chase')
            page.goto('https://secure.chase.com/')
            _check_chase_page(page)
            if visible:
                page.bring_to_front()
            _set_run(run_id, 'waiting_for_mfa', mfa_prompt='browser' if visible else None)
            diagnostics.record('stage', name='waiting_for_accounts')
            try:
                _wait_for_accounts(page, run_id)
            except ChaseBrowserError as error:
                if run.mode != 'automatic' or error.code not in (
                        'interactive_verification_needed', 'mfa_timeout', 'chase_signed_out'):
                    raise
                _wait_for_browser_request(run_id)
                if error.code == 'chase_signed_out':
                    page.goto('https://secure.chase.com/')
                page.bring_to_front()
                _wait_for_accounts(page, run_id)
            if run.mode == 'firefox_test':
                diagnostics.record('stage', name='accounts_reached')
                _set_run(run_id, 'completed')
            else:
                diagnostics.record('stage', name='exporting')
                _export_accounts(page, run_id, credentials)
    except FirefoxError as error:
        diagnostics.record('failure', code=error.code)
        raise ChaseBrowserAttention(error.code) from None
    except ChaseBrowserError as error:
        diagnostics.record('failure', code=error.code)
        raise


def run_worker(run_id):
    """Entry point for a short-lived user service. Never log secret values."""
    try:
        run = _run(run_id)
        if run.status not in ACTIVE_STATUSES:
            return
        if run.mode in ('local_ai', 'local_preview'):
            from pacioli.functions.chase_local import run_local_worker
            run_local_worker(run_id)
            return
        credentials = (db.session.query(ChaseBrowserCredentials)
                       .filter_by(connection_id=run.connection_id).one_or_none())
        if credentials is None:
            raise ChaseBrowserError('credentials_missing')
        _run_browser(run_id, credentials)
    except ChaseBrowserCancelled:
        db.session.rollback()
        _set_run(run_id, 'cancelled')
    except ChaseBrowserAttention as error:
        db.session.rollback()
        _set_run(run_id, 'needs_attention', error_code=error.code)
    except ChaseBrowserError as error:
        db.session.rollback()
        status = ('needs_attention' if error.code == 'chase_signed_out' and
                  _run(run_id).mode == 'automatic' else 'failed')
        _set_run(run_id, status, error_code=error.code)
    except Exception:
        db.session.rollback()
        _set_run(run_id, 'failed', error_code='browser_or_import_failed')
    finally:
        db.session.remove()
