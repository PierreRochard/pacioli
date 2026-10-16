"""On-demand Chase QFX export through a local, clearly labeled browser."""

import base64
import binascii
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import current_app
from playwright.sync_api import Error as PlaywrightError, sync_playwright
from sqlalchemy.exc import IntegrityError

from pacioli.extensions import db
from pacioli.functions.chase_diagnostics import (
    ChasePasskeyDiagnostics, safe_exception_details)
from pacioli.functions.chase_profile import (
    ProfileBusy, ProfileUnavailable, browser_lock, discard_history, profile_path,
    remove_profile, remove_unused_profiles)
from pacioli.functions.ofx_file_import import (AccountNotApproved, MAX_OFX_BYTES,
                                                import_chase_qfx)
from pacioli.models import ChaseBrowserCredentials, ChaseBrowserRuns


ACTIVE_STATUSES = ('queued', 'signing_in', 'waiting_for_mfa',
                   'choosing_accounts', 'exporting', 'importing')
TERMINAL_STATUSES = ('completed', 'partial', 'failed', 'cancelled',
                     'needs_attention')
CHASE_HOSTS = frozenset(('chase.com', 'secure.chase.com', 'www.chase.com'))
CHROMIUM_EXECUTABLE = '/snap/bin/chromium'
STALE_AFTER = timedelta(minutes=5)
MFA_TIMEOUT_SECONDS = 10 * 60
ACCOUNT_CHOICE_TIMEOUT_SECONDS = 5 * 60
PASSKEY_ENROLL_TIMEOUT_SECONDS = 10 * 60
HEADLESS_CHALLENGE_SECONDS = 25
PROVIDER_TEST_MODES = ('provider_test', 'lastpass_test')
SIGNIN_ONLY_MODES = ('passkey_enroll', 'passkey_test', 'firefox_test') + PROVIDER_TEST_MODES
# Linked by LastPass's official downloads page; never supplied by a request.
LASTPASS_STORE_URL = ('https://chromewebstore.google.com/detail/'
                     'lastpass-free-password-ma/hdokiejnpimakedhajhdlcegeplioahd')
PASSKEY_AUTHENTICATOR_OPTIONS = {
    'protocol': 'ctap2', 'transport': 'internal',
    'hasResidentKey': True, 'hasUserVerification': True,
    'isUserVerified': True, 'automaticPresenceSimulation': True,
}


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
    """Start outside the web service's NoNewPrivileges sandbox."""
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
    if mode == 'passkey_enroll' and not current_app.config.get(
            'CHASE_PASSKEY_ENROLLMENT_ENABLED', False):
        raise ChaseBrowserError('passkey_enrollment_unavailable')
    if mode not in SIGNIN_ONLY_MODES and not reviewed_usd:
        raise ChaseBrowserError('review_usd_required')
    credentials = (db.session.query(ChaseBrowserCredentials)
                   .filter_by(connection_id=connection_id).first())
    if credentials is None:
        raise ChaseBrowserError('credentials_missing')
    if credentials.browser_engine not in ('firefox', 'chromium'):
        raise ChaseBrowserError('browser_mode_mismatch')
    if ((credentials.browser_engine == 'firefox' and mode in
            ('passkey_enroll', 'passkey_test') + PROVIDER_TEST_MODES) or
            (mode == 'firefox_test' and credentials.browser_engine != 'firefox')):
        raise ChaseBrowserError('browser_mode_mismatch')
    if mode == 'passkey_test' and not credentials.passkey_credential:
        raise ChaseBrowserError('passkey_missing')
    if mode == 'passkey_enroll' and credentials.passkey_credential:
        raise ChaseBrowserError('passkey_already_saved')
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


def confirm_provider_signin(run_id):
    run = _run(run_id)
    if (run.cancel_requested or run.mode not in PROVIDER_TEST_MODES
            or run.status != 'waiting_for_mfa' or run.mfa_prompt != 'provider_review'):
        raise ChaseBrowserError('provider_not_ready')
    run.provider_confirmed = True
    run.updated_at = _now()
    db.session.commit()


def reset_browser_profile(credentials):
    try:
        with browser_lock():
            remove_profile(credentials.browser_profile_key)
            keep = {row[0] for row in db.session.query(
                ChaseBrowserCredentials.browser_profile_key).all()
                    if row[0] != credentials.browser_profile_key}
            remove_unused_profiles(keep)
            credentials.browser_profile_key = None
            credentials.provider_verified_at = None
            credentials.session_state = None
    except ProfileBusy:
        raise ChaseBrowserError('another_run_active') from None
    except (OSError, ProfileUnavailable):
        raise ChaseBrowserError('browser_profile_unavailable') from None


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


def confirm_passkey_enrollment(run_id):
    run = _run(run_id)
    if run.cancel_requested or run.mode != 'passkey_enroll' or (
            run.status != 'waiting_for_mfa') or (
            run.mfa_prompt != 'passkey_review'):
        raise ChaseBrowserError('passkey_not_ready')
    run.passkey_confirmed = True
    run.updated_at = _now()
    db.session.commit()


def reject_passkey_enrollment(run_id):
    """Record Chase's visible rejection and stop before storing a candidate."""
    run = _run(run_id)
    if run.mode != 'passkey_enroll' or run.status != 'waiting_for_mfa':
        raise ChaseBrowserError('passkey_not_ready')
    run.error_code = 'chase_passkey_rejected'
    run.cancel_requested = True
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


def _agent_user_agent(browser):
    major = re.match(r'\d+', browser.version)
    if not major:
        raise ChaseBrowserError('browser_version_unknown')
    return ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/{}.0.0.0 Safari/537.36 '
            'Pacioli-AI-Agent/0.1').format(major.group())


def _find_login_frame(page):
    for frame in page.frames:
        if urlsplit(frame.url).hostname != 'secure.chase.com':
            continue
        if (frame.locator('input[name="username"]').count() and
                frame.locator('input[name="password"]').count()):
            return frame
    return None


def _signed_in_location(page):
    for locator in (
            page.locator('a#heading').filter(has_text='See all transactions'),
            page.locator('button[aria-label="Download account activity"]')):
        if locator.count() and locator.first.is_visible():
            return True
    return False


def _sign_in(page, run_id, username, password, manual=False):
    page.goto('https://secure.chase.com/', wait_until='domcontentloaded',
              timeout=30000)
    _check_chase_page(page)
    if _run(run_id).browser_visible or _run(run_id).mode not in ('automatic', 'passkey_test'):
        page.bring_to_front()
    if manual:
        # The genuine provider controls its own UI. Never alter WebAuthn APIs.
        _set_run(run_id, 'waiting_for_mfa', mfa_prompt='browser')
        return False
    deadline = time.monotonic() + 45
    frame = None
    run = _run(run_id)
    passkey_available = (run.mode == 'passkey_test' and
                         _has_saved_passkey(run.connection_id))
    passkey_button_deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        _heartbeat(run_id)
        _check_chase_page(page)
        if _signed_in_location(page):
            return False
        if passkey_available:
            button = _passkey_sign_in_button(page)
            if button is not None:
                button.click(timeout=10000)
                _set_run(run_id, 'waiting_for_mfa')
                return True
        frame = _find_login_frame(page)
        if frame is not None and (
                not passkey_available or time.monotonic() >= passkey_button_deadline):
            break
        page.wait_for_timeout(1000)
    if run.mode == 'passkey_test':
        raise ChaseBrowserAttention('passkey_signin_unavailable')
    if frame is not None:
        # Never fill a frame from another host or record field values.
        if urlsplit(frame.url).hostname != 'secure.chase.com':
            raise ChaseBrowserError('unexpected_login_host')
        frame.locator('input[name="username"]').fill(username)
        frame.locator('input[name="password"]').fill(password)
        frame.locator('button#signin-button').click(timeout=10000)
    _set_run(run_id, 'waiting_for_mfa')
    return False


def _has_saved_passkey(connection_id):
    credentials = (db.session.query(ChaseBrowserCredentials)
                   .filter_by(connection_id=connection_id).one_or_none())
    return bool(credentials and credentials.passkey_credential)


def _passkey_sign_in_button(page):
    """Use only one visible passkey action hosted by Chase; otherwise fall back."""
    candidates = []
    sign_in_label = re.compile(
        r'^(?:sign in|log in|use|continue)(?: with| using)? '
        r'(?:a |your )?passkey(?: instead)?$', re.I)
    for frame in page.frames:
        if urlsplit(frame.url).hostname not in CHASE_HOSTS:
            continue
        for role in ('button', 'link'):
            controls = frame.get_by_role(role, name=sign_in_label)
            for index in range(controls.count()):
                control = controls.nth(index)
                if control.is_visible():
                    candidates.append(control)
    return candidates[0] if len(candidates) == 1 else None


def _validate_passkey_credential(value, allowed_rp='chase.com'):
    """Validate a CDP credential; production accepts Chase scopes only."""
    if not isinstance(value, dict):
        raise ChaseBrowserError('invalid_passkey')
    fields = ('credentialId', 'isResidentCredential', 'rpId', 'privateKey',
              'userHandle', 'signCount')
    if not set(fields).issubset(value):
        raise ChaseBrowserError('invalid_passkey')
    rp_id = value['rpId']
    if not isinstance(rp_id, str) or not (
            rp_id == allowed_rp or rp_id.endswith('.' + allowed_rp)):
        raise ChaseBrowserError('invalid_passkey')
    if value['isResidentCredential'] is not True or not (
            isinstance(value['signCount'], int) and
            not isinstance(value['signCount'], bool) and
            value['signCount'] >= -1):
        raise ChaseBrowserError('invalid_passkey')
    limits = {'credentialId': 1024, 'userHandle': 64, 'privateKey': 4096}
    decoded = {}
    for field, max_bytes in limits.items():
        item = value[field]
        if not isinstance(item, str) or len(item) > 8192:
            raise ChaseBrowserError('invalid_passkey')
        try:
            decoded[field] = base64.b64decode(item, validate=True)
        except (ValueError, binascii.Error):
            raise ChaseBrowserError('invalid_passkey')
        if not 1 <= len(decoded[field]) <= max_bytes:
            raise ChaseBrowserError('invalid_passkey')
    try:
        private_key = serialization.load_der_private_key(
            decoded['privateKey'], password=None)
    except (TypeError, ValueError):
        raise ChaseBrowserError('invalid_passkey')
    if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not (
            isinstance(private_key.curve, ec.SECP256R1)):
        raise ChaseBrowserError('invalid_passkey')
    validated = {field: value[field] for field in fields}
    backup_fields = ('backupEligibility', 'backupState')
    if any(field in value for field in backup_fields):
        if not all(type(value.get(field)) is bool for field in backup_fields):
            raise ChaseBrowserError('invalid_passkey')
        if value['backupState'] and not value['backupEligibility']:
            raise ChaseBrowserError('invalid_passkey')
        validated.update({field: value[field] for field in backup_fields})
    return validated


def _create_authenticator(context, page):
    """Use Chromium's native WebAuthn backend instead of a JavaScript shim."""
    session = context.new_cdp_session(page)
    session.send('WebAuthn.enable', {'enableUI': False})
    authenticator_id = session.send('WebAuthn.addVirtualAuthenticator', {
        'options': dict(PASSKEY_AUTHENTICATOR_OPTIONS)
    })['authenticatorId']
    return session, authenticator_id


def _install_saved_passkey(authenticator, credentials):
    if not credentials.passkey_credential:
        return False
    try:
        credential = _validate_passkey_credential(
            json.loads(credentials.passkey_credential))
    except (TypeError, ValueError):
        raise ChaseBrowserError('invalid_passkey')
    session, authenticator_id = authenticator
    session.send('WebAuthn.addCredential', {
        'authenticatorId': authenticator_id,
        'credential': credential})
    return True


def _refresh_saved_passkey(authenticator, credentials):
    """Update stored metadata after a successful assertion."""
    try:
        previous = _validate_passkey_credential(
            json.loads(credentials.passkey_credential))
    except (TypeError, ValueError):
        raise ChaseBrowserError('invalid_passkey')
    session, authenticator_id = authenticator
    candidates = session.send('WebAuthn.getCredentials', {
        'authenticatorId': authenticator_id})['credentials']
    if len(candidates) != 1:
        raise ChaseBrowserError('invalid_passkey')
    current = _validate_passkey_credential(candidates[0])
    identity_fields = ('credentialId', 'rpId', 'privateKey', 'userHandle',
                       'isResidentCredential')
    if any(current[field] != previous[field] for field in identity_fields):
        raise ChaseBrowserError('invalid_passkey')
    for field in ('backupEligibility', 'backupState'):
        if field in previous and current.get(field) != previous[field]:
            raise ChaseBrowserError('invalid_passkey')
    if current['signCount'] < previous['signCount']:
        raise ChaseBrowserError('invalid_passkey')
    credentials.passkey_credential = json.dumps(current, separators=(',', ':'))
    credentials.updated_at = _now()


def _wait_for_passkey_enrollment(authenticator, page, run_id, credentials,
                                 diagnostics=None):
    """Save a key only after Chase visibly confirms enrollment to the user."""
    _set_run(run_id, 'waiting_for_mfa', mfa_prompt='passkey_setup')
    if diagnostics:
        diagnostics.record('enrollment_wait_started')
    deadline = time.monotonic() + PASSKEY_ENROLL_TIMEOUT_SECONDS
    prior_count = None
    while time.monotonic() < deadline:
        if diagnostics:
            diagnostics.drain()
            try:
                diagnostics.persist()
            except Exception:
                db.session.rollback()
                diagnostics.record('collector_error', source='persist')
        run = _heartbeat(run_id)
        parsed = urlsplit(page.url)
        if parsed.scheme != 'https' or not parsed.hostname or not (
                parsed.hostname == 'chase.com' or
                parsed.hostname.endswith('.chase.com')):
            raise ChaseBrowserError('unexpected_browser_host')
        session, authenticator_id = authenticator
        candidates = session.send('WebAuthn.getCredentials', {
            'authenticatorId': authenticator_id})['credentials']
        if diagnostics and len(candidates) != prior_count:
            diagnostics.last_candidate_count = len(candidates)
            diagnostics.record('credential_candidate_count', count=len(candidates))
            prior_count = len(candidates)
        if len(candidates) > 1:
            raise ChaseBrowserError('multiple_passkeys_created')
        if candidates:
            credential = _validate_passkey_credential(candidates[0])
            if run.mfa_prompt != 'passkey_review':
                run = _set_run(run_id, 'waiting_for_mfa',
                               mfa_prompt='passkey_review')
            if run.passkey_confirmed:
                if diagnostics:
                    diagnostics.record('chase_success_confirmed_by_user')
                credentials.passkey_credential = json.dumps(
                    credential, separators=(',', ':'))
                credentials.passkey_verified_at = None
                credentials.updated_at = _now()
                db.session.commit()
                _set_run(run_id, 'completed', mfa_prompt=None)
                return
        page.wait_for_timeout(1000)
    raise ChaseBrowserError('passkey_enrollment_timeout')


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
    automatic = run.mode in ('automatic', 'passkey_test') and not run.browser_visible
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


def _wait_for_provider_confirmation(page, run_id):
    _set_run(run_id, 'waiting_for_mfa', mfa_prompt='provider_review')
    deadline = time.monotonic() + ACCOUNT_CHOICE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        run = _heartbeat(run_id)
        _check_chase_page(page)
        if not _signed_in_location(page):
            # Chase's SPA can temporarily remove account controls while it
            # rerenders. Keep an accepted user confirmation, but do not save
            # the session until the authenticated account controls return.
            if run.mfa_prompt != 'browser':
                _set_run(run_id, 'waiting_for_mfa', mfa_prompt='browser')
        elif run.provider_confirmed:
            return
        elif run.mfa_prompt != 'provider_review':
            _set_run(run_id, 'waiting_for_mfa', mfa_prompt='provider_review')
        page.wait_for_timeout(1000)
    raise ChaseBrowserAttention('provider_confirmation_timeout')


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


def _save_session_state(context, credentials):
    """Keep an encrypted auth snapshot in addition to the private local profile."""
    credentials.session_state = json.dumps(
        context.storage_state(indexed_db=True), separators=(',', ':'))
    credentials.updated_at = _now()
    db.session.commit()


def _native_session_state(context):
    """Snapshot only Chase cookies/localStorage, never provider vault storage.

    IndexedDB and extension state stay in Chromium's private persistent profile.
    Do not call storage_state() here: it enumerates unrelated origins too.
    """
    urls = {'https://{}/'.format(host) for host in CHASE_HOSTS}
    origins = {}
    for page in context.pages:
        for frame in page.frames:
            parsed = urlsplit(frame.url)
            if parsed.scheme != 'https' or parsed.hostname not in CHASE_HOSTS:
                continue
            if parsed.port not in (None, 443):
                continue
            urls.add(frame.url)
            origin = 'https://{}'.format(parsed.hostname)
            # Read only an allowlisted bank frame. No extension/provider frames.
            try:
                local_storage = frame.evaluate('''() => Object.keys(localStorage).map(
                    name => ({name, value: localStorage.getItem(name)}))''')
            except PlaywrightError:
                # A bank iframe may be sandboxed or navigate during the snapshot.
                # Cookies and the persistent profile still retain the session.
                continue
            origins[origin] = {
                'origin': origin,
                'localStorage': local_storage,
            }
    cookies = [cookie for cookie in context.cookies(urls=list(urls))
               if cookie.get('domain', '').lstrip('.') in CHASE_HOSTS]
    return {'cookies': cookies, 'origins': list(origins.values())}


def _save_native_session_state(context, credentials):
    credentials.session_state = json.dumps(
        _native_session_state(context), separators=(',', ':'))
    credentials.updated_at = _now()
    db.session.commit()


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
    if hasattr(download, 'read_content'):
        content = download.read_content(MAX_OFX_BYTES + 1)
    else:
        with Path(download.path()).open('rb') as source:
            content = source.read(MAX_OFX_BYTES + 1)
    if not content or len(content) > MAX_OFX_BYTES:
        raise ChaseBrowserError('qfx_size_invalid')
    return content


def _export_accounts(page, context, run_id, credentials, save_session=None):
    save_session = save_session or _save_native_session_state
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
        save_session(context, credentials)
    elif run.mode == 'interactive':
        credentials.selected_account_labels = None
        credentials.selected_ofx_account_ids = None
        db.session.commit()
    if failures:
        _set_run(run_id, 'partial', error_code='some_imports_failed')
    else:
        _set_run(run_id, 'completed')


def _native_context(playwright, profile, downloads, headless):
    discard_history(profile)
    # Read the installed version without opening a second browser. Preserve the
    # truthful Linux UA and agent label for every page, including provider popups.
    result = subprocess.run([CHROMIUM_EXECUTABLE, '--version'], capture_output=True,
                            text=True, check=True, timeout=15)
    version = re.search(r'(?:Chromium|Chrome)\s+(\d+)\.', result.stdout)
    if not version:
        raise ChaseBrowserError('browser_version_unknown')
    from types import SimpleNamespace
    context = playwright.chromium.launch_persistent_context(
        str(profile), executable_path=CHROMIUM_EXECUTABLE,
        headless=headless, chromium_sandbox=True, downloads_path=downloads,
        accept_downloads=True,
        user_agent=_agent_user_agent(SimpleNamespace(version=version.group(1))),
        ignore_default_args=['--disable-extensions', '--password-store=basic',
                             '--use-mock-keychain'],
        args=['--no-first-run', '--no-default-browser-check'])
    # No WebAuthn.enable, virtual authenticators, or credential API replacement.
    return context


def _restorable_session(credentials):
    try:
        state = json.loads(credentials.session_state or 'null')
    except (TypeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    # Playwright's optional `credentials` field would install a virtual
    # authenticator and disconnect native providers. Never restore that field.
    return {
        'cookies': [cookie for cookie in state.get('cookies', [])
                    if cookie.get('domain', '').lstrip('.') in CHASE_HOSTS],
        'origins': [origin for origin in state.get('origins', [])
                    if origin.get('origin') in {
                        'https://{}'.format(host) for host in CHASE_HOSTS}],
    }


def _restore_native_session_cookies(context, state):
    """Restore bank session cookies that Chromium drops on a clean shutdown.

    Persistent cookies and origin storage already live in the retained profile.
    Do not use set_storage_state there: it clears unrelated provider storage.
    """
    cookies = [cookie for cookie in (state or {}).get('cookies', [])
               if cookie.get('expires', -1) == -1
               and cookie.get('domain', '').lstrip('.') in CHASE_HOSTS]
    if cookies:
        context.add_cookies(cookies)


def _open_lastpass_setup(context):
    # Installation and vault unlock are performed by the user in LastPass's UI.
    # Keep Chase in its own tab; the worker only observes that Chase tab.
    page = context.new_page()
    page.goto(LASTPASS_STORE_URL, wait_until='domcontentloaded', timeout=30000)
    page.bring_to_front()


def _run_native_browser(run_id, credentials):
    run = _run(run_id)
    provider_test = run.mode in PROVIDER_TEST_MODES
    previous_key = credentials.browser_profile_key
    key = uuid.uuid4().hex if provider_test or not previous_key else previous_key
    profile = profile_path(key)
    adopted = not provider_test
    state = _restorable_session(credentials) if not provider_test else None
    restore_full_state = not previous_key and not provider_test
    if not provider_test and not previous_key:
        credentials.browser_profile_key = key
        db.session.commit()
    visible = run.mode != 'automatic'
    try:
        with tempfile.TemporaryDirectory(
                prefix='pacioli-chase-', dir=os.environ['TMPDIR']) as downloads:
            with sync_playwright() as playwright:
                # One headless attempt, then at most one user-requested window.
                for attempt in range(2):
                    _heartbeat(run_id)
                    context = _native_context(playwright, profile, downloads,
                                              headless=not visible)
                    handoff = False
                    confirmed_state = None
                    page = None
                    try:
                        if state is not None:
                            if restore_full_state:
                                context.set_storage_state(state)
                            else:
                                _restore_native_session_cookies(context, state)
                            state = None
                        restore_full_state = False
                        page = context.new_page()
                        for other in context.pages:
                            if other != page:
                                other.close()
                        _set_run(run_id, 'signing_in', browser_visible=visible,
                                 mfa_prompt=None)
                        _sign_in(page, run_id, credentials.username,
                                 credentials.password,
                                 manual=(provider_test or
                                         (run.mode == 'automatic' and visible) or
                                         bool(credentials.provider_verified_at)))
                        if run.mode == 'lastpass_test':
                            _open_lastpass_setup(context)
                        _wait_for_accounts(page, run_id)
                        if provider_test:
                            _wait_for_provider_confirmation(page, run_id)
                            confirmed_state = json.dumps(
                                _native_session_state(context),
                                separators=(',', ':'))
                        else:
                            _save_native_session_state(context, credentials)
                            _export_accounts(page, context, run_id, credentials)
                            return
                    except ChaseBrowserError as error:
                        if (run.mode != 'automatic' or visible or error.code not in (
                                'interactive_verification_needed', 'mfa_timeout',
                                'chase_signed_out')):
                            raise
                        # Carry the current challenge's session cookies into
                        # the visible browser, rather than an older snapshot.
                        state = _native_session_state(context)
                        handoff = True
                    except PlaywrightError:
                        if page is not None and page.is_closed():
                            raise ChaseBrowserAttention('browser_window_closed') from None
                        raise
                    finally:
                        context.close()
                    if handoff:
                        _wait_for_browser_request(run_id)
                        visible = True
                        continue
                    if provider_test and confirmed_state is not None:
                        _heartbeat(run_id)
                        credentials.browser_profile_key = key
                        credentials.provider_verified_at = _now()
                        credentials.session_state = confirmed_state
                        credentials.updated_at = _now()
                        db.session.commit()
                        adopted = True
                        remove_profile(previous_key)
                        _set_run(run_id, 'completed')
                        return
    finally:
        if not adopted:
            remove_profile(key)


def _run_browser(run_id, credentials, diagnostics=None):
    run = _run(run_id)
    if run.mode not in SIGNIN_ONLY_MODES and not run.reviewed_usd:
        raise ChaseBrowserError('review_usd_required')
    scratch = os.environ.get('TMPDIR')
    if not scratch or not Path(scratch).is_dir():
        raise ChaseBrowserError('scratch_unavailable')
    if credentials.browser_engine != 'firefox' and not Path(CHROMIUM_EXECUTABLE).exists():
        raise ChaseBrowserError('browser_unavailable')
    try:
        with browser_lock():
            _heartbeat(run_id)
            if credentials.browser_engine == 'firefox':
                _run_firefox_browser(run_id, credentials)
                return
            remove_unused_profiles({row[0] for row in db.session.query(
                ChaseBrowserCredentials.browser_profile_key).all()})
            if run.mode in ('passkey_enroll', 'passkey_test'):
                _run_legacy_browser(run_id, credentials, diagnostics)
            else:
                _run_native_browser(run_id, credentials)
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

    def save_metadata(context, stored):
        # The everyday browser owns its session and LastPass state. Do not
        # export either into the app's old Chromium authentication snapshot.
        stored.updated_at = _now()
        db.session.commit()

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
                _export_accounts(page, None, run_id, credentials, save_session=save_metadata)
    except FirefoxError as error:
        diagnostics.record('failure', code=error.code)
        raise ChaseBrowserAttention(error.code) from None
    except ChaseBrowserError as error:
        diagnostics.record('failure', code=error.code)
        raise


def _run_legacy_browser(run_id, credentials, diagnostics=None):
    run = _run(run_id)
    if run.mode not in ('passkey_enroll', 'passkey_test') and not run.reviewed_usd:
        raise ChaseBrowserError('review_usd_required')
    username, password = credentials.username, credentials.password
    try:
        state = json.loads(credentials.session_state) if credentials.session_state else None
        if state is not None and not isinstance(state, dict):
            state = None
    except (TypeError, ValueError):
        state = None
    if run.mode == 'passkey_test':
        state = None  # Prove the passkey works without a cached Chase session.
    scratch = os.environ.get('TMPDIR')
    if not scratch or not Path(scratch).is_dir():
        raise ChaseBrowserError('scratch_unavailable')
    if not Path(CHROMIUM_EXECUTABLE).exists():
        raise ChaseBrowserError('browser_unavailable')
    with tempfile.TemporaryDirectory(prefix='pacioli-chase-', dir=scratch) as downloads:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=CHROMIUM_EXECUTABLE,
                headless=run.mode in ('automatic', 'passkey_test'),
                chromium_sandbox=True, downloads_path=downloads,
                args=['--no-first-run', '--no-default-browser-check'])
            try:
                if diagnostics:
                    major = re.match(r'\d+', browser.version)
                    diagnostics.browser_major = int(major.group()) if major else None
                    diagnostics.record('browser_started', headless=False)
                context = browser.new_context(
                    user_agent=_agent_user_agent(browser), accept_downloads=True,
                    storage_state=state)
                try:
                    page = context.new_page()
                    authenticator = None
                    passkey_asserted = []
                    if run.mode == 'passkey_enroll':
                        authenticator = _create_authenticator(context, page)
                        if diagnostics:
                            diagnostics.attach(context, page, authenticator)
                    elif run.mode in ('automatic', 'passkey_test'):
                        if credentials.passkey_credential:
                            authenticator = _create_authenticator(context, page)
                            _install_saved_passkey(authenticator, credentials)
                            session, authenticator_id = authenticator
                            session.on('WebAuthn.credentialAsserted',
                                       lambda event: passkey_asserted.append(
                                           event.get('authenticatorId') ==
                                           authenticator_id))
                    _set_run(run_id, 'signing_in')
                    passkey_action_clicked = _sign_in(
                        page, run_id, username, password)
                    username = password = None
                    _wait_for_accounts(page, run_id)
                    if diagnostics:
                        diagnostics.record('accounts_page_reached')
                    if authenticator and any(passkey_asserted):
                        _refresh_saved_passkey(authenticator, credentials)
                        db.session.commit()
                    if run.mode == 'passkey_test':
                        if not passkey_action_clicked or not any(passkey_asserted):
                            raise ChaseBrowserAttention('passkey_signin_unavailable')
                        credentials.passkey_verified_at = _now()
                        _save_session_state(context, credentials)
                        _set_run(run_id, 'completed')
                        return
                    _save_session_state(context, credentials)
                    if run.mode == 'passkey_enroll':
                        _wait_for_passkey_enrollment(
                            authenticator, page, run_id, credentials,
                            diagnostics=diagnostics)
                        return
                finally:
                    if diagnostics:
                        diagnostics.drain()
                    context.close()
            finally:
                browser.close()


def run_worker(run_id):
    """Entry point for a short-lived user service. Never log secret values."""
    diagnostics = None
    try:
        run = _run(run_id)
        if run.status not in ACTIVE_STATUSES:
            return
        if run.mode in ('local_ai', 'local_preview'):
            from pacioli.functions.chase_local import run_local_worker
            run_local_worker(run_id)
            return
        if run.mode == 'passkey_enroll':
            diagnostics = ChasePasskeyDiagnostics(run_id)
        credentials = (db.session.query(ChaseBrowserCredentials)
                       .filter_by(connection_id=run.connection_id).one_or_none())
        if credentials is None:
            raise ChaseBrowserError('credentials_missing')
        _run_browser(run_id, credentials, diagnostics=diagnostics)
        if diagnostics:
            diagnostics.record('worker_finished', outcome='completed')
    except ChaseBrowserCancelled:
        db.session.rollback()
        rejected = _run(run_id).error_code == 'chase_passkey_rejected'
        _set_run(run_id, 'failed' if rejected else 'cancelled')
        if diagnostics:
            diagnostics.record('worker_finished',
                               outcome='failed' if rejected else 'cancelled',
                               error_code='chase_passkey_rejected' if rejected
                               else None)
    except ChaseBrowserAttention as error:
        db.session.rollback()
        _set_run(run_id, 'needs_attention', error_code=error.code)
        if diagnostics:
            diagnostics.record('worker_finished', outcome='needs_attention',
                               error_code=error.code)
    except ChaseBrowserError as error:
        db.session.rollback()
        status = ('needs_attention' if error.code == 'chase_signed_out' and
                  _run(run_id).mode == 'automatic' else 'failed')
        if error.code == 'chase_signed_out':
            credentials = (db.session.query(ChaseBrowserCredentials)
                           .filter_by(connection_id=_run(run_id).connection_id).first())
            if credentials is not None:
                credentials.session_state = None
        _set_run(run_id, status, error_code=error.code)
        if diagnostics:
            diagnostics.record('worker_finished', outcome=status,
                               error_code=error.code)
    except Exception as error:
        db.session.rollback()
        _set_run(run_id, 'failed', error_code='browser_or_import_failed')
        if diagnostics:
            diagnostics.record('worker_finished', outcome='failed',
                               error_code='browser_or_import_failed',
                               **safe_exception_details(error))
    finally:
        if diagnostics:
            try:
                diagnostics.persist(force=True)
            except Exception:
                db.session.rollback()
        db.session.remove()
