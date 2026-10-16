"""Native-provider orchestration against PostgreSQL and an offline fake bank.

The browser tests exercise the production navigation/export/import path. Every
page request is intercepted; no Chase request or real passkey ceremony occurs.
Human approvals are simulated, so these tests cannot prove provider acceptance.
"""

from decimal import Decimal
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from pacioli.extensions import db
from pacioli.functions import chase_browser as connector
from pacioli.functions.chase_profile import (
    ProfileBusy, ProfileUnavailable, browser_lock, discard_history, profile_path)
from pacioli.functions.ofx_file_import import account_summary, import_chase_qfx
from pacioli.models import ChaseBrowserCredentials, ChaseBrowserRuns, OFXFileImports
from tests.test_chase_browser import PATH, _csrf, clean_chase_connector
from tests.test_chase_qfx import _qfx


@pytest.fixture
def clean_native_feed(app_context):
    existing = {row[0] for row in db.session.execute(text('SELECT id FROM ofx.acctfrom'))}
    yield
    db.session.remove()
    rows = db.session.execute(text('SELECT id, name FROM ofx.acctfrom')).all()
    connection_ids = set()
    for account_id, name in rows:
        if account_id in existing:
            continue
        connection_ids.update(row[0] for row in db.session.execute(text(
            'SELECT DISTINCT connection_id FROM admin.ofx_file_imports WHERE ofx_account_id=:id'),
            {'id': account_id}))
        for table, column in (('admin.ofx_file_imports', 'ofx_account_id'),
                              ('bookkeeping.bank_feed_accounts', 'ofx_account_id'),
                              ('ofx.stmttrn', 'acctfrom_id'), ('ofx.ledgerbal', 'acctfrom_id'),
                              ('ofx.bankacctfrom', 'id'), ('ofx.acctfrom', 'id')):
            db.session.execute(text('DELETE FROM {} WHERE {}=:id'.format(table, column)),
                               {'id': account_id})
        db.session.execute(text('DELETE FROM bookkeeping.subaccounts WHERE name=:name'),
                           {'name': name})
    for connection_id in connection_ids:
        db.session.execute(text('''DELETE FROM admin.connection_responses r WHERE connection_id=:id
            AND NOT EXISTS (SELECT 1 FROM admin.ofx_file_imports i WHERE i.response_id=r.id)'''),
                           {'id': connection_id})
        db.session.execute(text('''DELETE FROM admin.connections c WHERE id=:id
            AND NOT EXISTS (SELECT 1 FROM admin.connection_responses r WHERE r.connection_id=c.id)'''),
                           {'id': connection_id})
    db.session.commit()


@pytest.fixture
def native_run(clean_chase_connector, clean_native_feed, client, admin_user, monkeypatch):
    client.post('/login/', data=admin_user)
    client.post(PATH, data={'action': 'save', 'username': 'fixture-user',
                            'password': 'fixture-password'})
    monkeypatch.setattr(connector, '_launch_systemd', lambda run_id: None)
    client.post(PATH, data={'action': 'start', 'reviewed_usd': 'yes'})
    return (db.session.query(ChaseBrowserRuns).one(),
            db.session.query(ChaseBrowserCredentials).one())


def test_handoff_requires_active_request_and_csrf(native_run, client, app):
    run, credentials = native_run
    run.mode = 'automatic'
    run.status = 'waiting_for_mfa'
    run.mfa_prompt = 'code'
    db.session.commit()
    with pytest.raises(connector.ChaseBrowserError):
        connector.request_browser(run.id)
    run.mfa_prompt = 'open_browser'
    db.session.commit()
    prior = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post(PATH, data={'action': 'open_browser',
                                      'run_id': run.id}).status_code == 400
        page = client.get(PATH)
        assert b'Open verification window' in page.data
        client.post(PATH, data={'action': 'open_browser', 'run_id': run.id,
                                'csrf_token': _csrf(page.data)})
        db.session.refresh(run)
        assert run.browser_requested
        run.cancel_requested = True
        db.session.commit()
        with pytest.raises(connector.ChaseBrowserError):
            connector.request_browser(run.id)
        with pytest.raises(connector.ChaseBrowserCancelled):
            connector._wait_for_browser_request(run.id)
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior


def test_handoff_timeout_and_provider_confirmation_are_bounded(native_run, monkeypatch):
    run, credentials = native_run
    run.mode = 'automatic'
    db.session.commit()
    monkeypatch.setattr(connector, 'MFA_TIMEOUT_SECONDS', 0)
    with pytest.raises(connector.ChaseBrowserAttention) as error:
        connector._wait_for_browser_request(run.id)
    assert error.value.code == 'verification_window_timeout'
    with pytest.raises(connector.ChaseBrowserError):
        connector.confirm_provider_signin(run.id)
    run.mode = 'provider_test'
    run.mfa_prompt = 'provider_review'
    db.session.commit()
    connector.confirm_provider_signin(run.id)
    assert run.provider_confirmed
    run.cancel_requested = True
    db.session.commit()
    with pytest.raises(connector.ChaseBrowserError):
        connector.confirm_provider_signin(run.id)


def test_provider_confirmation_survives_accounts_page_rerender(native_run, monkeypatch):
    run, credentials = native_run
    run.mode = 'provider_test'
    db.session.commit()
    visible = [False, True, True]
    prompts = []
    monkeypatch.setattr(connector, '_signed_in_location', lambda page: visible.pop(0))
    class Page:
        url = 'https://secure.chase.com/'
        def wait_for_timeout(self, milliseconds):
            current = connector._run(run.id)
            prompts.append(current.mfa_prompt)
            if current.mfa_prompt == 'browser':
                with pytest.raises(connector.ChaseBrowserError):
                    connector.confirm_provider_signin(run.id)
            else:
                connector.confirm_provider_signin(run.id)
    connector._wait_for_provider_confirmation(Page(), run.id)
    assert prompts == ['browser', 'provider_review']
    assert run.provider_confirmed


@pytest.mark.parametrize('accounts_return', [True, False])
def test_accepted_provider_confirmation_waits_for_accounts(
        native_run, client, monkeypatch, accounts_return):
    run, credentials = native_run
    connector._set_run(run.id, 'waiting_for_mfa', mode='provider_test',
                       mfa_prompt='provider_review')
    response = client.post(PATH, data={'action': 'confirm_provider', 'run_id': run.id})
    assert response.status_code == 302
    db.session.refresh(run)
    assert run.provider_confirmed
    # The account controls disappear after the POST and before the next poll.
    visibility = iter([False, accounts_return])
    monkeypatch.setattr(connector, '_signed_in_location', lambda page: next(visibility))
    ticks = iter([0, 1, 2, connector.ACCOUNT_CHOICE_TIMEOUT_SECONDS + 1])
    monkeypatch.setattr(connector, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))

    class Page:
        url = 'https://secure.chase.com/'

        def wait_for_timeout(self, milliseconds):
            db.session.refresh(run)
            assert run.provider_confirmed
            assert run.mfa_prompt == 'browser'
            assert credentials.provider_verified_at is None
            from pacioli.views.admin_views import _chase_status_message
            assert 'confirmation was received' in _chase_status_message(run)

    if accounts_return:
        connector._wait_for_provider_confirmation(Page(), run.id)
    else:
        with pytest.raises(connector.ChaseBrowserAttention) as error:
            connector._wait_for_provider_confirmation(Page(), run.id)
        assert error.value.code == 'provider_confirmation_timeout'
    assert run.provider_confirmed
    assert run.status != 'completed'
    assert credentials.provider_verified_at is None


def test_profile_privacy_lock_and_forget(native_run, client, app):
    run, credentials = native_run
    key = uuid4().hex
    directory = profile_path(key)
    (directory / 'fixture-cookie').write_text('fixture secret')
    credentials.browser_profile_key = key
    db.session.commit()
    assert directory.stat().st_mode & 0o777 == 0o700
    with browser_lock():
        with pytest.raises(ProfileBusy):
            with browser_lock():
                pytest.fail('second worker acquired the lock')
        with pytest.raises(connector.ChaseBrowserError):
            connector.reset_browser_profile(credentials)
    with pytest.raises(ProfileUnavailable):
        profile_path('../outside')
    bad_key = uuid4().hex
    (directory.parent / bad_key).symlink_to(directory, target_is_directory=True)
    with pytest.raises(ProfileUnavailable):
        profile_path(bad_key)
    connector._set_run(run.id, 'cancelled')
    client.post(PATH, data={'action': 'forget'})
    assert not directory.exists()
    assert not db.session.query(ChaseBrowserCredentials).count()


def test_password_update_invalidates_profile_and_provider_confirmation(native_run, client):
    run, credentials = native_run
    key = uuid4().hex
    directory = profile_path(key)
    credentials.browser_profile_key = key
    credentials.provider_verified_at = connector._now()
    credentials.session_state = '{"cookies": []}'
    connector._set_run(run.id, 'cancelled')
    client.post(PATH, data={'action': 'save', 'password': 'replacement'})
    db.session.refresh(credentials)
    assert not directory.exists()
    assert credentials.browser_profile_key is None
    assert credentials.session_state is None
    assert credentials.provider_verified_at is None


def test_restore_strips_virtual_authenticator_material():
    state = connector._restorable_session(SimpleNamespace(session_state=json.dumps({
        'cookies': [{'domain': '.lastpass.com', 'value': 'never-copy'}],
        'origins': [{'origin': 'chrome-extension://provider', 'localStorage': []},
                    {'origin': 'https://secure.chase.com.attacker.test'}],
        'credentials': [{'privateKey': 'never-install'}]})))
    assert state == {'cookies': [], 'origins': []}


def test_native_snapshot_never_reads_provider_storage():
    class Frame:
        def __init__(self, url):
            self.url = url
        def evaluate(self, script):
            if self.url == 'https://www.chase.com/':
                raise connector.PlaywrightError('fixture frame navigated')
            assert self.url == 'https://secure.chase.com/accounts'
            return [{'name': 'bank-state', 'value': 'bank-fixture'}]
    class Context:
        pages = [SimpleNamespace(frames=[
            Frame('https://secure.chase.com/accounts'),
            Frame('https://www.chase.com/'),
            Frame('https://lastpass.com/vault'),
            Frame('chrome-extension://provider/vault'),
            Frame('https://secure.chase.com.attacker.test')])]
        def storage_state(self, **kwargs):
            pytest.fail('bulk storage snapshot would read provider data')
        def cookies(self, urls):
            assert all(url.startswith(('https://secure.chase.com/',
                                       'https://chase.com/',
                                       'https://www.chase.com/')) for url in urls)
            return [{'domain': '.chase.com', 'name': 'bank', 'value': 'fixture'},
                    {'domain': '.lastpass.com', 'name': 'vault', 'value': 'never-copy'}]
    state = connector._native_session_state(Context())
    assert state['cookies'] == [{'domain': '.chase.com', 'name': 'bank', 'value': 'fixture'}]
    assert state['origins'] == [{'origin': 'https://secure.chase.com',
                                'localStorage': [{'name': 'bank-state', 'value': 'bank-fixture'}]}]


def test_lastpass_action_is_signin_only_and_requires_csrf(native_run, client, app):
    run, credentials = native_run
    connector._set_run(run.id, 'cancelled')
    prior = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post(PATH, data={'action': 'test_lastpass'}).status_code == 400
        page = client.get(PATH)
        assert b'Test my LastPass passkey' in page.data
        client.post(PATH, data={'action': 'test_lastpass', 'csrf_token': _csrf(page.data)})
        latest = connector.latest_run(credentials.connection_id)
        assert latest.mode == 'lastpass_test' and latest.reviewed_usd is False
        connector._set_run(latest.id, 'waiting_for_mfa', mfa_prompt='provider_review')
        client.post(PATH, data={'action': 'confirm_provider', 'run_id': latest.id,
                                'csrf_token': _csrf(client.get(PATH).data)})
        db.session.refresh(latest)
        assert latest.provider_confirmed
        assert latest.imported_count == 0
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior


def test_history_workaround_keeps_authentication_state(native_run):
    profile = profile_path(uuid4().hex)
    default = profile / 'Default'
    default.mkdir()
    for name in ('History', 'History-journal', 'History-wal', 'History-shm', 'Cookies'):
        (default / name).write_text('fixture')
    discard_history(profile)
    assert sorted(p.name for p in default.iterdir()) == ['Cookies']


def test_restore_session_cookies_leaves_provider_and_persistent_state_alone():
    session = {'name': 'bank-session', 'value': 'fixture',
               'domain': '.chase.com', 'path': '/', 'expires': -1}
    persistent = dict(session, name='persistent', expires=9999999999)
    expired = dict(session, name='expired', expires=0)
    provider = dict(session, domain='provider.test')
    spoofed = dict(session, domain='chase.com.provider.test')
    restored = []
    # Only add_cookies is available; clearing storage or reading a vault fails.
    context = SimpleNamespace(add_cookies=lambda cookies: restored.extend(cookies))
    connector._restore_native_session_cookies(context, {
        'cookies': [session, persistent, expired, provider, spoofed],
        'origins': [{'origin': 'https://provider.test', 'localStorage': []}],
    })
    assert restored == [session]
    connector._restore_native_session_cookies(context, None)
    assert restored == [session]


def test_cancel_after_download_prevents_import(native_run, monkeypatch):
    run, credentials = native_run
    monkeypatch.setattr(connector, '_open_export', lambda page: None)
    monkeypatch.setattr(connector, '_check_chase_page', lambda page: None)
    monkeypatch.setattr(connector, '_account_choices',
                        lambda page: [{'index': 0, 'label': 'Current Chase account'}])
    def download(page, index):
        connector.cancel_run(run.id)
        return b'not imported'
    monkeypatch.setattr(connector, '_download_account', download)
    def unexpected(*args, **kwargs):
        pytest.fail('cancelled download was imported')
    monkeypatch.setattr(connector, 'import_chase_qfx', unexpected)
    with pytest.raises(connector.ChaseBrowserCancelled):
        connector._export_accounts(None, None, run.id, credentials)


class FakeBank:
    def __init__(self):
        self.content = _qfx(uuid4().hex[:12] + '4321')
        self.password_submissions = 0
        self.downloads = 0
        self.setup_pages = 0
        self.auth = 'password'
        self.expired = False
        self.headless = True

    def route(self, route):
        from urllib.parse import urlsplit
        url = urlsplit(route.request.url)
        if route.request.url == connector.LASTPASS_STORE_URL:
            self.setup_pages += 1
            route.fulfill(content_type='text/html', body='''<!doctype html>
                <title>Offline store fixture</title><script>
                localStorage.setItem('provider-fixture', 'never-copy');</script>''')
            return
        if url.scheme != 'https' or url.hostname != 'secure.chase.com':
            route.abort()
            return
        path = url.path
        if path == '/statement':
            self.downloads += 1
            route.fulfill(status=200, body=self.content, headers={
                'Content-Type': 'application/x-ofx',
                'Content-Disposition': 'attachment; filename="fixture.qfx"'})
            return
        authenticated = ('fixture-session=ok' in route.request.headers.get('cookie', '')
                         and not self.expired)
        if path == '/password':
            self.password_submissions += 1
        if authenticated or path == '/approved':
            self.expired = False
            body = '''<button aria-label="Download account activity">Download</button>
                <button id="select-account_options_id-selector-no-label">Account</button>
                <mds-select name="downloadActivityAccounts">
                  <mds-select-option>Checking ending in 4321</mds-select-option>
                </mds-select>
                <button id="select-showing-file-select-selector-no-label">Format</button>
                <mds-select-option id="showing-file-select-quicken web connect (qfx)">QFX</mds-select-option>
                <button id="select-showing-activity-select-selector-no-label">Dates</button>
                <mds-select-option id="showing-activity-select-last24monthsoption">24 months</mds-select-option>
                <button id="downloadButton" onclick="location='/statement'">Download QFX</button>
                <script>document.cookie='fixture-session=ok; Secure; SameSite=Lax; Path=/';</script>'''
        elif self.auth == 'provider' or (self.auth == 'challenge' and not self.headless):
            body = '''<input name="username"><input name="password" type="password">
                <button onclick="location='/approved'">Use existing passkey</button>'''
        elif path == '/password' and self.auth == 'challenge':
            body = '''<p>Approval requires a visible browser.</p><script>
                document.cookie='fixture-challenge=current; Secure; SameSite=Lax; Path=/';
                </script>'''
        elif path == '/password':
            body = '''<input autocomplete="one-time-code"
                onkeydown="if(event.key==='Enter' && this.value==='123456') location='/approved'">'''
        else:
            body = '''<input name="username"><input name="password" type="password">
                <button id="signin-button" onclick="location='/password'">Sign in</button>'''
        route.fulfill(status=200, content_type='text/html',
                      body='<!doctype html><title>Offline fixture</title>' + body)


@pytest.fixture
def fake_bank(native_run, monkeypatch):
    # Chromium treats the real Web Store origin specially and crashes when its
    # response is replaced with fixture HTML. The live public listing was
    # checked separately; use an ordinary intercepted origin for offline tests.
    monkeypatch.setattr(connector, 'LASTPASS_STORE_URL',
                        'https://provider-setup.test/lastpass')
    bank = FakeBank()
    real_context = connector._native_context
    launches = []
    def launch(playwright, profile, downloads, headless):
        # Simulate window approval while keeping all offline tests headless.
        # The production launch function still runs against real Chromium.
        bank.headless = headless
        launches.append((str(profile), headless))
        context = real_context(playwright, profile, downloads, headless=True)
        context.route('**/*', bank.route)
        return context
    def no_emulator(*args, **kwargs):
        pytest.fail('native flow attempted to create a virtual authenticator')
    monkeypatch.setattr(connector, '_native_context', launch)
    monkeypatch.setattr(connector, '_create_authenticator', no_emulator)
    return bank, launches


def _reload(run, credentials):
    return (db.session.get(ChaseBrowserRuns, inspect(run).identity[0]),
            db.session.get(ChaseBrowserCredentials, inspect(credentials).identity[0]))


def test_offline_mfa_export_balance_and_persistent_reuse(
        native_run, fake_bank, client, monkeypatch):
    run, credentials = native_run
    bank, launches = fake_bank
    real_field = connector._mfa_code_input
    sent = []
    def code_field(page):
        field = real_field(page)
        current = connector._run(run.id)
        if field is not None and current.mfa_prompt == 'code' and not sent:
            client.post(PATH, data={'action': 'mfa_code', 'run_id': run.id,
                                    'code': '123456'})
            sent.append(True)
        return field
    monkeypatch.setattr(connector, '_mfa_code_input', code_field)
    connector._run_browser(run.id, credentials)
    run, credentials = _reload(run, credentials)
    assert run.status == 'completed'
    assert run.imported_count == 1 and run.mfa_code is None
    assert sent == [True] and bank.password_submissions == 1
    assert len(credentials.selected_ofx_account_ids) == 1
    summary = account_summary(credentials.selected_ofx_account_ids[0])
    assert summary['balance'] == Decimal('575.00')
    assert summary['opening_candidate'] == Decimal('500.00')
    assert summary['transaction_count'] == 2
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries')).scalar() == 0
    assert b'fixture-session' not in client.get('/connections/chase/status/').data
    saved_cookies = json.loads(credentials.session_state)['cookies']
    assert next(cookie for cookie in saved_cookies
                if cookie['name'] == 'fixture-session')['expires'] == -1

    second = connector.start_run(credentials.connection_id, run.requested_by_id,
                                 reviewed_usd=True, mode='automatic')
    connector._run_browser(second.id, credentials)
    assert second.status == 'completed' and second.duplicate_count == 1
    assert second.imported_count == 0 and bank.password_submissions == 1
    assert launches[0][0] == launches[1][0]
    assert [entry[1] for entry in launches] == [False, True]
    assert account_summary(credentials.selected_ofx_account_ids[0])['transaction_count'] == 2


@pytest.mark.parametrize('changed_account', [False, True])
def test_offline_expiry_handoff_preserves_run_and_account_allowlist(
        native_run, fake_bank, client, monkeypatch, changed_account):
    run, credentials = native_run
    bank, launches = fake_bank
    approved = import_chase_qfx(bank.content, reviewed_usd=True)
    credentials.selected_account_labels = ['Chase account 1 (•••• 4321)']
    credentials.selected_ofx_account_ids = [approved['account_id']]
    credentials.session_state = json.dumps({'cookies': [{
        'name': 'fixture-session', 'value': 'expired', 'domain': 'secure.chase.com',
        'path': '/', 'secure': True}], 'origins': []})
    run.mode = 'automatic'
    db.session.commit()
    bank.auth = 'challenge'
    bank.expired = True
    # Same displayed suffix but a different OFX account must still be rejected.
    if changed_account:
        bank.content = _qfx(uuid4().hex[:12] + '4321')
    bank_hash = sha256(bank.content).hexdigest()
    monkeypatch.setattr(connector, 'HEADLESS_CHALLENGE_SECONDS', -1)
    def approve_window(seconds):
        assert connector._run(run.id).mfa_prompt == 'open_browser'
        client.post(PATH, data={'action': 'open_browser', 'run_id': run.id})
    monkeypatch.setattr(connector, 'time', SimpleNamespace(
        monotonic=connector.time.monotonic, sleep=approve_window))
    real_wait = connector._wait_for_accounts
    def human_approval(page, run_id):
        if connector._run(run_id).browser_visible:
            assert 'fixture-challenge=current' in page.evaluate('document.cookie')
            page.get_by_role('button', name='Use existing passkey').click()
        return real_wait(page, run_id)
    monkeypatch.setattr(connector, '_wait_for_accounts', human_approval)
    if changed_account:
        with pytest.raises(connector.ChaseBrowserAttention) as error:
            connector._run_browser(run.id, credentials)
        assert error.value.code == 'account_selection_changed'
        assert db.session.query(OFXFileImports).filter_by(sha256=bank_hash).count() == 0
    else:
        connector._run_browser(run.id, credentials)
        assert run.status == 'completed' and run.duplicate_count == 1
    assert len(launches) == 2 and launches[0][0] == launches[1][0]
    assert [entry[1] for entry in launches] == [True, False]
    assert db.session.query(ChaseBrowserRuns).count() == 1
    assert credentials.selected_ofx_account_ids == [approved['account_id']]


@pytest.mark.parametrize('confirm', [True, False])
@pytest.mark.parametrize('mode', ['provider_test', 'lastpass_test'])
def test_offline_existing_provider_is_fresh_and_never_imports(
        native_run, fake_bank, client, monkeypatch, confirm, mode):
    run, credentials = native_run
    bank, launches = fake_bank
    bank.auth = 'provider'
    old_key = uuid4().hex
    old_directory = profile_path(old_key)
    credentials.browser_profile_key = old_key
    credentials.session_state = '{"cookies":[],"origins":[]}'
    run.mode = mode
    run.reviewed_usd = False
    db.session.commit()
    real_wait = connector._wait_for_accounts
    def human_approval(page, run_id):
        assert page.evaluate("typeof PublicKeyCredential === 'function'")
        assert page.evaluate("navigator.credentials.get.toString().includes('[native code]')")
        assert page.locator('input[name="username"]').input_value() == ''
        assert page.locator('input[name="password"]').input_value() == ''
        page.get_by_role('button', name='Use existing passkey').click()
        return real_wait(page, run_id)
    monkeypatch.setattr(connector, '_wait_for_accounts', human_approval)
    real_heartbeat = connector._heartbeat
    def confirm_at_accounts(run_id):
        current = connector._run(run_id)
        if current.mfa_prompt == 'provider_review':
            client.post(PATH, data={'action': 'confirm_provider' if confirm else 'cancel',
                                    'run_id': run_id})
        return real_heartbeat(run_id)
    monkeypatch.setattr(connector, '_heartbeat', confirm_at_accounts)
    connector.run_worker(run.id)
    run, credentials = _reload(run, credentials)
    assert bank.downloads == bank.password_submissions == 0
    assert bank.setup_pages == (1 if mode == 'lastpass_test' else 0)
    assert Path(launches[0][0]) != old_directory
    assert run.imported_count == run.duplicate_count == 0
    if confirm:
        assert run.status == 'completed', run.error_code
        assert credentials.provider_verified_at is not None
        assert credentials.browser_profile_key != old_key
        assert 'never-copy' not in credentials.session_state
        assert not old_directory.exists()
        assert Path(launches[0][0]).exists()
    else:
        assert run.status == 'cancelled', run.error_code
        assert credentials.provider_verified_at is None
        assert credentials.browser_profile_key == old_key
        assert old_directory.exists()
        assert not Path(launches[0][0]).exists()


def test_offline_closed_window_stops_cleanly(native_run, fake_bank, monkeypatch):
    run, credentials = native_run
    real_wait = connector._wait_for_accounts
    def close_window(page, run_id):
        page.close()
        return real_wait(page, run_id)
    monkeypatch.setattr(connector, '_wait_for_accounts', close_window)
    connector.run_worker(run.id)
    run, credentials = _reload(run, credentials)
    assert run.status == 'needs_attention'
    assert run.error_code == 'browser_window_closed'
    assert run.imported_count == 0


@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
def test_offline_frontend_polling_shows_handoff_and_completion(
        native_run, client, tmp_path, engine):
    from playwright.sync_api import sync_playwright, expect
    run, credentials = native_run
    credentials.browser_engine = engine
    run.mode = 'automatic'
    connector._set_run(run.id, 'signing_in')
    html = client.get(PATH).data
    profile = profile_path(uuid4().hex)
    with sync_playwright() as playwright:
        context = connector._native_context(playwright, profile, str(tmp_path), True)
        try:
            def serve(route):
                if route.request.url == 'https://pacioli.test' + PATH:
                    route.fulfill(content_type='text/html', body=html)
                elif route.request.url == 'https://pacioli.test/connections/chase/status/':
                    route.fulfill(content_type='application/json',
                                  body=client.get('/connections/chase/status/').data)
                else:
                    route.abort()
            context.route('**/*', serve)
            page = context.new_page()
            page.goto('https://pacioli.test' + PATH)
            if engine == 'firefox':
                expect(page.locator('#chase-firefox-form')).to_be_visible()
                expect(page.locator('#chase-chromium-tools')).to_be_hidden()
                expect(page.locator('#chase-firefox-button')).to_be_disabled()
            expect(page.locator('#chase-browser-form')).to_be_hidden()
            connector._set_run(run.id, 'waiting_for_mfa', mfa_prompt='open_browser')
            expect(page.locator('#chase-browser-form')).to_be_visible(timeout=6000)
            expect(page.locator('#chase-browser-run-id')).to_have_value(str(run.id))
            expect(page.locator('#chase-start-button')).to_be_disabled()
            connector._set_run(run.id, 'completed', duplicate_count=1)
            expect(page.locator('#chase-browser-form')).to_be_hidden(timeout=6000)
            expect(page.locator('#chase-cancel-form')).to_be_hidden()
            expect(page.locator('#chase-run-counts')).to_contain_text('Already present: 1')
            expect(page.locator('#chase-provider-button')).to_be_enabled()
        finally:
            context.close()
