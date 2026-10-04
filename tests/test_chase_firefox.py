"""Real stock Firefox and local fixture bank; never the user's browser/Chase."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from threading import Thread
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from websockets.sync.client import connect

from pacioli.extensions import db
from pacioli.functions import chase_browser as connector
from pacioli.functions import firefox_bidi as bidi
from pacioli.models import ChaseBrowserRuns
from pacioli.functions.ofx_file_import import import_chase_qfx
from tests.test_chase_browser import PATH, _csrf, clean_chase_connector
from tests.test_chase_native import native_run, clean_native_feed, _reload
from tests.test_chase_qfx import _qfx


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def firefox(tmp_path):
    subprocess.run(['free', '-h'], check=True)
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    assert available >= 10 * 1024 * 1024, 'Not enough memory for a browser test'
    bank = SimpleNamespace(downloads=0, auth='passkey', content=_qfx(uuid4().hex[:12] + '4321'))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            if self.path == '/statement':
                bank.downloads += 1
                self.send_header('Content-Type', 'application/x-ofx')
                self.send_header('Content-Disposition', 'attachment; filename="fixture.qfx"')
                self.end_headers()
                self.wfile.write(bank.content)
                return
            self.send_header('Content-Type', 'text/html')
            if self.path == '/approved':
                self.send_header('Set-Cookie', 'fixture-session=ok; Path=/; SameSite=Lax')
            self.end_headers()
            if self.path == '/personal':
                body = '<title>Personal tab fixture</title><input value="leave this alone">'
            elif self.path == '/mfa':
                body = '''<input autocomplete="one-time-code"
                  onkeydown="if(event.key==='Enter' && this.value==='123456') parent.location='/approved'">'''
            elif self.path == '/error':
                body = '<div role="alert">Something went wrong. Please try again later.</div>'
            elif self.path == '/approved' or 'fixture-session=ok' in self.headers.get('Cookie', ''):
                body = '''<button aria-label="Download account activity">Download</button>
                  <button id="select-account_options_id-selector-no-label">Account</button>
                  <mds-select name="downloadActivityAccounts">
                    <mds-select-option>Checking ending in 4321</mds-select-option>
                  </mds-select>
                  <button id="select-showing-file-select-selector-no-label">Format</button>
                  <mds-select-option id="showing-file-select-quicken web connect (qfx)">QFX</mds-select-option>
                  <button id="select-showing-activity-select-selector-no-label">Dates</button>
                  <mds-select-option id="showing-activity-select-last24monthsoption">24 months</mds-select-option>
                  <button id="downloadButton" onclick="location='/statement'">Download QFX</button>'''
            elif bank.auth == 'otp':
                body = '<iframe src="/mfa"></iframe>'
            elif bank.auth == 'error':
                body = '<div role="alert">Something went wrong. Please try again later.</div>'
            elif bank.auth == 'iframe_error':
                body = '<iframe src="/error"></iframe>'
            else:
                body = '''<input name="username"><input name="password" type="password">
                  <button id="passkey" onclick="location='/approved'">Use existing passkey</button>'''
            self.wfile.write(('<!doctype html>' + body).encode())

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = 'http://127.0.0.1:{}'.format(server.server_port)
    profile = tmp_path / 'profile'
    profile.mkdir(mode=0o700)
    downloads = tmp_path / 'downloads'
    downloads.mkdir(mode=0o700)
    preferences = {
        'browser.shell.checkDefaultBrowser': False,
        'browser.startup.homepage_override.mstone': 'ignore',
        'browser.download.folderList': 2, 'browser.download.dir': str(downloads),
        'browser.download.useDownloadDir': True,
        'browser.helperApps.neverAsk.saveToDisk': 'application/x-ofx',
        'network.captive-portal-service.enabled': False,
        'network.connectivity-service.enabled': False,
        'datareporting.healthreport.uploadEnabled': False,
        'toolkit.telemetry.enabled': False,
    }
    (profile / 'user.js').write_text('\n'.join('user_pref({}, {});'.format(json.dumps(k), json.dumps(v))
                                             for k, v in preferences.items()))
    log_path = tmp_path / 'firefox.log'
    log = log_path.open('wb')
    # Port 0 lets Firefox bind its own free port. A port chosen here can be taken
    # before Firefox binds it; BiDi then never starts while the browser stays up.
    process = subprocess.Popen(['/snap/bin/firefox', '--headless', '--no-remote',
                                '--profile', str(profile), '--remote-debugging-port', '0',
                                origin + '/personal'], stdout=log, stderr=subprocess.STDOUT)
    try:
        # Startup takes about five seconds; profiles on a busy HDD can stall it for minutes.
        started = time.monotonic()
        port = None
        while port is None:
            text = log_path.read_text(errors='replace')
            match = re.search(r'WebDriver BiDi listening on ws://127\.0\.0\.1:(\d+)', text)
            if match:
                port = int(match.group(1))
            elif process.poll() is not None or time.monotonic() - started > 90:
                pytest.fail('Fixture Firefox did not start (exit {}, {:.0f}s):\n{}'.format(
                    process.poll(), time.monotonic() - started, text[-2000:]))
            else:
                time.sleep(.2)
        yield SimpleNamespace(port=port, origin=origin, bank=bank, process=process, downloads=downloads)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def attach(firefox, **kwargs):
    return bidi.FirefoxConnection(firefox.port, allowed_origins={firefox.origin}, **kwargs)


def test_firefox_keeps_personal_tab_and_browser_open(firefox):
    for attempt in range(2):
        with attach(firefox) as connection:
            page = connection.page
            page.goto(firefox.origin + '/')
            assert page.locator('#passkey').is_visible()
            # Test-only inspection of the fixture's whole tree; production
            # always requests the root of its own tab.
            contexts = connection.command('browsingContext.getTree', {})['contexts']
            personal = [c for c in contexts if c['url'].endswith('/personal')]
            assert len(personal) == 1
            with pytest.raises(bidi.FirefoxError):
                page.goto('https://unrelated.example/')
            connection.command('browsingContext.navigate', {
                'context': connection.tab, 'url': 'data:text/html,unrelated', 'wait': 'complete'})
            with pytest.raises(bidi.FirefoxError) as error:
                page.locator('input').count()
            assert error.value.code == 'unexpected_browser_host'
        assert firefox.process.poll() is None


def test_firefox_download_uses_browser_path_without_global_override(firefox):
    with attach(firefox) as connection:
        page = connection.page
        page.goto(firefox.origin + '/')
        page.locator('#passkey').click()
        page.locator('#downloadButton').wait_for()
        with page.expect_download(timeout=5000) as event:
            page.locator('#downloadButton').click()
        assert event.value.suggested_filename == 'fixture.qfx'
        assert event.value.read_content(100000) == firefox.bank.content
    assert (firefox.downloads / 'fixture.qfx').read_bytes() == firefox.bank.content
    assert firefox.process.poll() is None


def test_firefox_connection_refusal_and_invalid_ports():
    with pytest.raises(bidi.FirefoxError) as error:
        with bidi.FirefoxConnection(free_port()):
            pytest.fail('Unexpected connection')
    assert error.value.code == 'firefox_not_connected'
    for port in (True, 80, 99999, '9222'):
        with pytest.raises(bidi.FirefoxError):
            bidi.FirefoxConnection(port)


def test_firefox_download_refuses_symlink(tmp_path):
    source = tmp_path / 'fixture.qfx'
    source.write_bytes(b'fixture')
    link = tmp_path / 'link.qfx'
    link.symlink_to(source)
    with pytest.raises(OSError):
        bidi.FirefoxDownload('fixture.qfx', str(link)).read_content(100)


def test_firefox_error_capture_preserves_failed_tab(firefox):
    records = []
    def record(event, **details):
        records.append((event, details))
    with pytest.raises(bidi.FirefoxError) as error:
        with attach(firefox, diagnostic=record) as connection:
            connection.page.goto(firefox.origin + '/')
            connection.script(connection.tab, '''
                document.body.innerHTML += '<p>Something went wrong. Account 987654321, secret@example.com</p>';
                return null;
            ''')
            connection.page.capture_error_signals()
            assert connection.page.locator('#passkey').count() == 1
            assert ('visible_error', {'phrases': ['something went wrong']}) in records
            assert '987654321' not in json.dumps(records)
            assert 'secret@example.com' not in json.dumps(records)
            raise bidi.FirefoxError('fixture_error')
    assert error.value.code == 'fixture_error'
    assert records[-1][1]['attached'] is False
    with attach(firefox) as connection:
        contexts = connection.command('browsingContext.getTree', {'maxDepth': 0})['contexts']
        assert any(c['url'] == firefox.origin + '/' for c in contexts)
        assert any(c['url'] == firefox.origin + '/personal' for c in contexts)
    assert firefox.process.poll() is None


@pytest.mark.parametrize('url,expected', [
    ('https://secure.chase.com/accounts/1234?token=secret#9876',
     {'kind': 'allowed', 'origin': 'https://secure.chase.com'}),
    ('about:neterror?u=https://secure.chase.com/?secret=1234',
     {'kind': 'browser_page', 'page': 'neterror'}),
    ('about:blank', {'kind': 'browser_page', 'page': 'blank'}),
    ('https://other.chase.com/private?token=secret',
     {'kind': 'unapproved_chase_host', 'host': 'other.chase.com'}),
    ('https://secret.example.com/?password=secret',
     {'kind': 'outside_allowlist', 'scheme': 'https'}),
    ('moz-extension://secret-vault-id/private',
     {'kind': 'outside_allowlist', 'scheme': 'moz-extension'}),
])
def test_firefox_navigation_diagnostics_omit_private_addresses(url, expected):
    assert bidi.navigation_summary(url, {'https://secure.chase.com'}) == expected


def test_firefox_sigterm_releases_session(firefox):
    code = '''
import signal, sys, time
from pacioli.chase_worker import _cancel_worker
from pacioli.functions.chase_browser import ChaseBrowserCancelled
from pacioli.functions.firefox_bidi import FirefoxConnection
signal.signal(signal.SIGTERM, _cancel_worker)
try:
    with FirefoxConnection(int(sys.argv[1]), allowed_origins={sys.argv[2]}) as connection:
        connection.page.goto(sys.argv[2] + '/')
        print('ready', flush=True)
        time.sleep(60)
except ChaseBrowserCancelled:
    pass
'''
    child = subprocess.Popen([sys.executable, '-W', 'ignore', '-c', code,
                              str(firefox.port), firefox.origin], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
    try:
        import select
        assert select.select([child.stdout], [], [], 10)[0], 'Worker did not start'
        assert child.stdout.readline().strip() == 'ready'
        child.terminate()
        assert child.wait(timeout=10) == 0
        with attach(firefox) as connection:
            connection.page.goto(firefox.origin + '/')
            assert connection.page.locator('#passkey').count() == 1
        assert firefox.process.poll() is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()


@pytest.fixture
def firefox_run(native_run, firefox, app, monkeypatch):
    run, credentials = native_run
    credentials.browser_engine = 'firefox'
    db.session.commit()
    original_connection = bidi.FirefoxConnection
    def connection(port, **kwargs):
        kwargs.setdefault('allowed_origins', {firefox.origin})
        return original_connection(firefox.port, **kwargs)
    monkeypatch.setattr(bidi, 'FirefoxConnection', connection)
    original_goto = bidi.FirefoxPage.goto
    def goto(page, url, **kwargs):
        assert url == 'https://secure.chase.com/'
        return original_goto(page, firefox.origin + '/', **kwargs)
    monkeypatch.setattr(bidi.FirefoxPage, 'goto', goto)
    def check(page):
        assert page.url.startswith(firefox.origin + '/')
    monkeypatch.setattr(connector, '_check_chase_page', check)
    monkeypatch.setattr(connector, 'CHASE_HOSTS', frozenset({'127.0.0.1'}))
    real_wait = connector._wait_for_accounts
    def human_login(page, run_id):
        if connector._run(run_id).browser_visible and page.locator('#passkey').count():
            page.locator('#passkey').click()
        return real_wait(page, run_id)
    monkeypatch.setattr(connector, '_wait_for_accounts', human_login)
    return run, credentials, firefox


def test_firefox_session_check_and_export_reuse(firefox_run, client):
    run, credentials, firefox = firefox_run
    run.mode = 'firefox_test'
    db.session.commit()
    connector.run_worker(run.id)
    run, credentials = _reload(run, credentials)
    assert run.status == 'completed', run.error_code
    assert firefox.bank.downloads == 0
    assert credentials.session_state is None
    assert credentials.provider_verified_at is None
    report = json.loads(run.diagnostics)
    assert report['kind'] == 'chase_firefox'
    assert any(event.get('name') == 'accounts_reached' for event in report['events'])
    assert report['events'][-1]['attached'] is False
    from pacioli.views.admin_views import _chase_status_message
    assert 'not which sign-in method' in _chase_status_message(run, 'firefox')

    export = connector.start_run(credentials.connection_id, run.requested_by_id, True)
    connector.run_worker(export.id)
    export, credentials = _reload(export, credentials)
    assert export.status == 'completed', export.error_code
    assert export.imported_count == 1
    assert len(credentials.selected_ofx_account_ids) == 1
    repeat = connector.start_run(credentials.connection_id, export.requested_by_id, True, mode='automatic')
    connector.run_worker(repeat.id)
    repeat, credentials = _reload(repeat, credentials)
    assert repeat.status == 'completed', repeat.error_code
    assert repeat.duplicate_count == 1 and repeat.imported_count == 0
    assert credentials.session_state is None
    assert credentials.browser_profile_key is None
    assert firefox.process.poll() is None


def test_firefox_lost_session_requires_restart_without_closing_browser(firefox):
    connection = attach(firefox).__enter__()
    try:
        connection.page.goto(firefox.origin + '/')
        # Simulate an abrupt worker loss, skipping its normal cleanup.
        connection._socket_manager.__exit__(None, None, None)
        connection.socket = None
        with pytest.raises(bidi.FirefoxError) as error:
            with attach(firefox):
                pytest.fail('Should not take over the existing session')
        assert error.value.code == 'firefox_session_busy'
        assert firefox.process.poll() is None
    finally:
        connection.close()


def test_firefox_cancel_before_import_preserves_browser(firefox_run, monkeypatch):
    run, credentials, firefox = firefox_run
    download = connector._download_account
    def cancelled(page, index):
        content = download(page, index)
        connector.cancel_run(run.id)
        return content
    monkeypatch.setattr(connector, '_download_account', cancelled)
    connector.run_worker(run.id)
    run, credentials = _reload(run, credentials)
    assert run.status == 'cancelled'
    assert run.imported_count == 0
    assert firefox.process.poll() is None


@pytest.mark.parametrize('auth', ['error', 'iframe_error'])
def test_firefox_bank_error_is_reported_without_waiting_for_mfa_timeout(firefox_run, auth):
    run, credentials, firefox = firefox_run
    firefox.bank.auth = auth
    run.mode = 'firefox_test'
    db.session.commit()
    connector.run_worker(run.id)
    run, credentials = _reload(run, credentials)
    assert run.status == 'needs_attention'
    assert run.error_code == 'chase_signin_error'
    assert run.imported_count == firefox.bank.downloads == 0
    report = json.loads(run.diagnostics)
    assert any(e.get('event') == 'visible_error' and
               e.get('phrases') == ['something went wrong', 'try again later']
               for e in report['events'])
    assert firefox.process.poll() is None


def test_firefox_mfa_relay_inside_bank_iframe(firefox_run, client, monkeypatch):
    run, credentials, firefox = firefox_run
    firefox.bank.auth = 'otp'
    run.mode = 'firefox_test'
    db.session.commit()
    original_field = connector._mfa_code_input
    sent = []
    def send_code(page):
        field = original_field(page)
        current = connector._run(run.id)
        if field is not None and current.mfa_prompt == 'code' and not sent:
            client.post(PATH, data={'action': 'mfa_code', 'run_id': run.id, 'code': '123456'})
            sent.append(True)
        return field
    monkeypatch.setattr(connector, '_mfa_code_input', send_code)
    connector.run_worker(run.id)
    run, credentials = _reload(run, credentials)
    assert run.status == 'completed', run.error_code
    assert sent == [True] and run.mfa_code is None
    assert run.imported_count == firefox.bank.downloads == 0
    assert firefox.process.poll() is None


@pytest.mark.parametrize('changed_account', [False, True])
def test_firefox_expired_session_handoff_checks_approved_account(
        firefox_run, client, monkeypatch, changed_account):
    run, credentials, firefox = firefox_run
    approved = import_chase_qfx(firefox.bank.content, reviewed_usd=True)
    credentials.selected_account_labels = ['Chase account 1 (•••• 4321)']
    credentials.selected_ofx_account_ids = [approved['account_id']]
    run.mode = 'automatic'
    db.session.commit()
    if changed_account:
        firefox.bank.content = _qfx(uuid4().hex[:12] + '4321')
    monkeypatch.setattr(connector, 'HEADLESS_CHALLENGE_SECONDS', -1)
    prompts = []
    def approve_window(seconds):
        prompts.append(connector._run(run.id).mfa_prompt)
        client.post(PATH, data={'action': 'open_browser', 'run_id': run.id})
    monkeypatch.setattr(connector, 'time', SimpleNamespace(
        monotonic=time.monotonic, sleep=approve_window))
    connector.run_worker(run.id)
    run, credentials = _reload(run, credentials)
    assert prompts == ['open_browser'], (run.status, run.error_code)
    if changed_account:
        assert run.status == 'needs_attention'
        assert run.error_code == 'account_selection_changed'
        assert run.imported_count == run.duplicate_count == 0
    else:
        assert run.status == 'completed', run.error_code
        assert run.duplicate_count == 1
    assert credentials.selected_ofx_account_ids == [approved['account_id']]
    assert firefox.process.poll() is None


def test_firefox_download_event_matches_tab_and_download_id():
    origin = 'https://secure.chase.com'
    records = [
        {'method': 'browsingContext.downloadWillBegin', 'params': {
            'context': 'personal', 'download': 'other', 'url': origin + '/other',
            'suggestedFilename': 'other.qfx'}},
        {'method': 'browsingContext.downloadWillBegin', 'params': {
            'context': 'owned', 'download': 'ours', 'url': origin + '/statement',
            'suggestedFilename': 'ours.qfx'}},
        {'method': 'browsingContext.downloadEnd', 'params': {
            'context': 'owned', 'download': 'wrong-id', 'url': origin + '/statement',
            'status': 'complete', 'filepath': '/never-read-other-file'}},
        {'method': 'browsingContext.downloadEnd', 'params': {
            'context': 'owned', 'download': 'ours', 'url': origin + '/statement',
            'status': 'complete', 'filepath': '/fixture.qfx'}},
    ]
    connection = SimpleNamespace(events=records, heartbeat=lambda: None,
                                 check=lambda context: None,
                                 command=lambda *args: None, allowed_origins={origin})
    event = bidi.FirefoxDownloadEvent(SimpleNamespace(context='owned', connection=connection))
    event.wait(.1)
    assert event.value.suggested_filename == 'ours.qfx'
    assert event.value._path == '/fixture.qfx'


def test_firefox_selection_csrf_and_legacy_test_rejection(native_run, client, app):
    run, credentials = native_run
    connector._set_run(run.id, 'completed')
    prior = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post(PATH, data={'action': 'browser_engine', 'browser_engine': 'firefox'}).status_code == 400
        response = client.get(PATH)
        client.post(PATH, data={'action': 'browser_engine', 'browser_engine': 'firefox',
                                'csrf_token': _csrf(response.data)})
        db.session.refresh(credentials)
        assert credentials.browser_engine == 'firefox'
        with pytest.raises(connector.ChaseBrowserError) as error:
            connector.start_run(credentials.connection_id, run.requested_by_id, False, 'lastpass_test')
        assert error.value.code == 'browser_mode_mismatch'
        assert client.post(PATH, data={'action': 'test_firefox'}).status_code == 400
        page = client.get(PATH)
        assert b'My existing Firefox' in page.data
        client.post(PATH, data={'action': 'test_firefox', 'csrf_token': _csrf(page.data)})
        latest = connector.latest_run(credentials.connection_id)
        assert latest.mode == 'firefox_test' and not latest.reviewed_usd
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior
