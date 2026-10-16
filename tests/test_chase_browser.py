"""Supervised Chase connector tests use only disposable local PostgreSQL."""

from datetime import datetime, timedelta, timezone
import re

import pytest
from sqlalchemy import inspect, text

from pacioli.extensions import db, run_sql
from pacioli.functions import chase_browser
from pacioli.functions.chase_profile import ProfileBusy, browser_lock
from pacioli.models import ChaseBrowserCredentials, ChaseBrowserRuns, Connections
from pacioli.secure_fields import PREFIX


PATH = '/connections/chase/'


@pytest.fixture
def clean_chase_connector(app_context, app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, 'CHASE_BROWSER_PROFILE_ROOT', tmp_path / 'profiles')
    db.session.remove()
    run_sql(text('DELETE FROM admin.chase_local_preferences'))
    run_sql(text('DELETE FROM admin.chase_browser_runs'))
    run_sql(text('DELETE FROM admin.chase_browser_credentials'))
    run_sql(text("DELETE FROM admin.connections WHERE source = 'chase_browser'"))
    yield
    db.session.remove()
    run_sql(text('DELETE FROM admin.chase_local_preferences'))
    run_sql(text('DELETE FROM admin.chase_browser_runs'))
    run_sql(text('DELETE FROM admin.chase_browser_credentials'))
    run_sql(text("DELETE FROM admin.connections WHERE source = 'chase_browser'"))


def _csrf(html):
    return re.search(rb'name="csrf_token" value="([^"]+)"', html).group(1).decode()


def test_login_is_encrypted_and_never_rendered(
        clean_chase_connector, app, client, admin_user):
    assert client.get(PATH).status_code == 302
    client.post('/login/', data=admin_user)
    prior = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        page = client.get(PATH)
        assert page.status_code == 200
        assert page.headers['Cache-Control'] == 'no-store'
        assert b'No Chase login is saved' in page.data
        assert client.post(PATH, data={
            'action': 'save', 'username': 'secret-user',
            'password': 'secret-password'}).status_code == 400
        saved = client.post(PATH, data={
            'csrf_token': _csrf(page.data), 'action': 'save',
            'username': 'secret-user', 'password': 'secret-password'})
        assert saved.status_code == 302
        connection = db.session.query(Connections).filter_by(source='chase_browser').one()
        credential = db.session.query(ChaseBrowserCredentials).one()
        assert credential.connection_id == connection.id
        assert credential.username == 'secret-user'
        assert credential.password == 'secret-password'
        raw = db.session.execute(text(
            'SELECT username, password FROM admin.chase_browser_credentials '
            'WHERE id = :id'), {'id': credential.id}).one()
        assert all(item.startswith(PREFIX) for item in raw)
        assert all('secret-' not in item for item in raw)
        shown = client.get(PATH)
        assert b'secret-user' not in shown.data
        assert b'secret-password' not in shown.data
        status = client.get('/connections/chase/status/')
        assert status.status_code == 200
        assert status.json == {'configured': True, 'saved_account_count': 0, 'run': None}
        assert status.headers['Cache-Control'] == 'no-store'
        # The removed Chromium engine's passkey and provider actions are gone.
        for action in ('enroll_passkey', 'test_passkey', 'test_provider', 'test_lastpass',
                       'browser_engine'):
            assert client.post(PATH, data={'csrf_token': _csrf(shown.data),
                                           'action': action}).status_code == 400
        assert db.session.query(ChaseBrowserRuns).count() == 0
        client.post(PATH, data={'csrf_token': _csrf(shown.data), 'action': 'forget'})
        assert db.session.query(ChaseBrowserCredentials).count() == 0
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior


def test_run_selection_cancel_and_singleton(
        clean_chase_connector, client, admin_user, monkeypatch):
    client.post('/login/', data=admin_user)
    page = client.get(PATH)
    client.post(PATH, data={
        'csrf_token': _csrf(page.data), 'action': 'save',
        'username': 'test-user', 'password': 'test-password'})
    launched = []
    monkeypatch.setattr(chase_browser, '_launch_systemd', launched.append)
    assert db.session.query(ChaseBrowserRuns).count() == 0
    client.post(PATH, data={'action': 'start'})
    assert db.session.query(ChaseBrowserRuns).count() == 0
    client.post(PATH, data={'action': 'start', 'reviewed_usd': 'yes'})
    run = db.session.query(ChaseBrowserRuns).one()
    assert launched == [run.id]
    assert run.reviewed_usd is True
    assert run.status == 'queued'
    client.post(PATH, data={'action': 'start', 'reviewed_usd': 'yes'})
    assert db.session.query(ChaseBrowserRuns).count() == 1
    run.status = 'choosing_accounts'
    run.account_choices = [{'index': 0, 'label': 'Chase account 1'},
                           {'index': 2, 'label': 'Chase account 2'}]
    db.session.commit()
    client.post(PATH, data={'action': 'select', 'run_id': str(run.id),
                            'account_index': ['1']})
    db.session.refresh(run)
    assert run.selected_accounts is None
    client.post(PATH, data={'action': 'select', 'run_id': str(run.id),
                            'account_index': ['2', '0', '2']})
    db.session.refresh(run)
    assert run.selected_accounts == [0, 2]
    status = client.get('/connections/chase/status/').json['run']
    assert status['status'] == 'choosing_accounts'
    assert status['account_choices'] == run.account_choices
    client.post(PATH, data={'action': 'cancel', 'run_id': str(run.id)})
    db.session.refresh(run)
    assert run.cancel_requested is True
    with pytest.raises(chase_browser.ChaseBrowserCancelled):
        chase_browser._heartbeat(run.id)
    run.updated_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    db.session.commit()
    chase_browser.expire_stale_runs()
    db.session.refresh(run)
    assert run.status == 'cancelled'


def test_unexpected_host_stops_and_account_labels_are_masked():
    class Page:
        url = 'https://attacker.example/signin'

    with pytest.raises(chase_browser.ChaseBrowserError) as error:
        chase_browser._check_chase_page(Page())
    assert error.value.code == 'unexpected_browser_host'
    Page.url = 'https://secure.chase.com/logout'
    with pytest.raises(chase_browser.ChaseBrowserError) as error:
        chase_browser._check_chase_page(Page())
    assert error.value.code == 'chase_signed_out'
    assert chase_browser.mask_account_label('Checking ending in 1234', 0) == (
        'Chase account 1 (•••• 1234)')
    assert chase_browser.mask_account_label('Checking balance 1234.56', 0) == (
        'Chase account 1')
    assert chase_browser.mask_account_label('Private account name', 1) == 'Chase account 2'


def test_sync_now_starts_only_on_request_for_approved_accounts(
        clean_chase_connector, app, client, admin_user, monkeypatch):
    client.post('/login/', data=admin_user)
    page = client.get(PATH)
    token = _csrf(page.data)
    client.post(PATH, data={'csrf_token': token, 'action': 'save',
                            'username': 'sync-user',
                            'password': 'sync-password'})
    credentials = db.session.query(ChaseBrowserCredentials).one()
    prior_csrf = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post(PATH, data={'action': 'sync_now'}).status_code == 400
        client.post(PATH, data={'csrf_token': token, 'action': 'sync_now'})
        assert db.session.query(ChaseBrowserRuns).count() == 0
        credentials.selected_account_labels = ['Chase account 1 (•••• 1234)']
        credentials.selected_ofx_account_ids = [42]
        db.session.commit()
        launched = []
        monkeypatch.setattr(chase_browser, '_launch_systemd', launched.append)
        assert db.session.query(ChaseBrowserRuns).count() == 0
        client.post(PATH, data={'csrf_token': token, 'action': 'sync_now'})
        automatic = db.session.query(ChaseBrowserRuns).one()
        assert automatic.mode == 'automatic'
        assert automatic.reviewed_usd is True
        assert launched == [automatic.id]
        client.post(PATH, data={'csrf_token': token, 'action': 'sync_now'})
        assert db.session.query(ChaseBrowserRuns).count() == 1
        automatic.status = 'needs_attention'
        automatic.error_code = 'interactive_verification_needed'
        db.session.commit()
        assert db.session.query(ChaseBrowserRuns).count() == 1
        client.post(PATH, data={'csrf_token': token, 'action': 'start',
                                'reviewed_usd': 'yes'})
        assert db.session.query(ChaseBrowserRuns).count() == 2
        interactive = db.session.query(ChaseBrowserRuns).order_by(
            ChaseBrowserRuns.id.desc()).first()
        assert interactive.mode == 'interactive'
        assert launched == [automatic.id, interactive.id]
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior_csrf


def test_code_relay_is_encrypted_one_time_and_only_on_chase_prompt(
        clean_chase_connector, client, admin_user, monkeypatch):
    client.post('/login/', data=admin_user)
    client.post(PATH, data={'action': 'save', 'username': 'test-user',
                            'password': 'test-password'})
    connection = db.session.query(Connections).filter_by(source='chase_browser').one()
    from pacioli.models import Users
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    run = ChaseBrowserRuns(connection_id=connection.id, requested_by_id=user.id,
                           status='waiting_for_mfa', mode='interactive',
                           mfa_prompt='code', reviewed_usd=True,
                           updated_at=datetime.now(timezone.utc))
    db.session.add(run)
    db.session.commit()
    client.post(PATH, data={'action': 'mfa_code', 'run_id': str(run.id),
                            'code': '12 34-56'})
    db.session.refresh(run)
    assert run.mfa_code == '123456'
    raw = db.session.execute(text(
        'SELECT mfa_code FROM admin.chase_browser_runs WHERE id = :id'),
        {'id': run.id}).scalar()
    assert raw.startswith(PREFIX)
    assert '123456' not in raw
    assert b'123456' not in client.get(PATH).data

    class FakePage:
        signed_in = False

        def wait_for_timeout(self, milliseconds):
            pass

    class FakeField:
        def fill(self, value):
            assert value == '123456'

        def press(self, key):
            assert key == 'Enter'
            page.signed_in = True

    page = FakePage()
    monkeypatch.setattr(chase_browser, '_check_chase_page', lambda page: None)
    monkeypatch.setattr(chase_browser, '_signed_in_location',
                        lambda page: page.signed_in)
    monkeypatch.setattr(chase_browser, '_mfa_code_input', lambda page: FakeField())
    chase_browser._wait_for_accounts(page, run.id)
    db.session.refresh(run)
    assert run.mfa_code is None
    assert run.mfa_prompt is None
    assert client.post(PATH, data={'action': 'mfa_code', 'run_id': str(run.id),
                                   'code': '999999'}).status_code == 302
    db.session.refresh(run)
    assert run.mfa_code is None


def test_automatic_selection_stops_when_accounts_change(clean_chase_connector, admin_user):
    from pacioli.models import Users
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    connection = Connections(source='chase_browser', org='Chase')
    db.session.add(connection)
    db.session.flush()
    credentials = ChaseBrowserCredentials(connection_id=connection.id,
                                          username='test-user', password='test-password',
                                          selected_account_labels=['Chase account 1 (•••• 1234)'],
                                          selected_ofx_account_ids=[42])
    run = ChaseBrowserRuns(connection_id=connection.id, requested_by_id=user.id,
                           status='exporting', mode='automatic', reviewed_usd=True,
                           updated_at=datetime.now(timezone.utc))
    db.session.add_all([credentials, run])
    db.session.commit()
    choices = [{'index': 2, 'label': 'Chase account 1 (•••• 1234)'},
               {'index': 0, 'label': 'Chase account 2 (•••• 5678)'}]
    assert chase_browser._wait_for_account_selection(run.id, choices, credentials) == [2]
    with pytest.raises(chase_browser.ChaseBrowserAttention) as error:
        chase_browser._wait_for_account_selection(
            run.id, [{'index': 0, 'label': 'Chase account 1 (•••• 9999)'}], credentials)
    assert error.value.code == 'account_selection_changed'


def test_automatic_attention_is_visible_from_admin_home(
        clean_chase_connector, client, admin_user):
    client.post('/login/', data=admin_user)
    client.post(PATH, data={'action': 'save', 'username': 'test-user',
                            'password': 'test-password'})
    connection = db.session.query(Connections).filter_by(source='chase_browser').one()
    from pacioli.models import Users
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    db.session.add(ChaseBrowserRuns(
        connection_id=connection.id, requested_by_id=user.id,
        mode='automatic', status='needs_attention',
        error_code='interactive_verification_needed', reviewed_usd=True,
        updated_at=datetime.now(timezone.utc)))
    db.session.commit()
    home = client.get('/')
    assert home.status_code == 200
    assert b'id="chase-attention-banner"' in home.data
    status = client.get('/connections/chase/status/').json['run']
    assert status['status'] == 'needs_attention'
    assert 'interactive verification' in status['message']


@pytest.fixture
def clean_chase_feed(app_context):
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
def chase_run(clean_chase_connector, clean_chase_feed, client, admin_user, monkeypatch):
    client.post('/login/', data=admin_user)
    client.post(PATH, data={'action': 'save', 'username': 'fixture-user',
                            'password': 'fixture-password'})
    monkeypatch.setattr(chase_browser, '_launch_systemd', lambda run_id: None)
    client.post(PATH, data={'action': 'start', 'reviewed_usd': 'yes'})
    return (db.session.query(ChaseBrowserRuns).one(),
            db.session.query(ChaseBrowserCredentials).one())


def _reload(run, credentials):
    return (db.session.get(ChaseBrowserRuns, inspect(run).identity[0]),
            db.session.get(ChaseBrowserCredentials, inspect(credentials).identity[0]))


def test_handoff_requires_active_request_and_csrf(chase_run, client, app):
    run, credentials = chase_run
    run.mode = 'automatic'
    run.status = 'waiting_for_mfa'
    run.mfa_prompt = 'code'
    db.session.commit()
    with pytest.raises(chase_browser.ChaseBrowserError):
        chase_browser.request_browser(run.id)
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
        with pytest.raises(chase_browser.ChaseBrowserError):
            chase_browser.request_browser(run.id)
        with pytest.raises(chase_browser.ChaseBrowserCancelled):
            chase_browser._wait_for_browser_request(run.id)
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior


def test_handoff_timeout_is_bounded(chase_run, monkeypatch):
    run, credentials = chase_run
    run.mode = 'automatic'
    db.session.commit()
    monkeypatch.setattr(chase_browser, 'MFA_TIMEOUT_SECONDS', 0)
    with pytest.raises(chase_browser.ChaseBrowserAttention) as error:
        chase_browser._wait_for_browser_request(run.id)
    assert error.value.code == 'verification_window_timeout'


def test_worker_lock_refuses_a_second_worker(chase_run):
    with browser_lock():
        with pytest.raises(ProfileBusy):
            with browser_lock():
                pytest.fail('second worker acquired the lock')


def test_cancel_after_download_prevents_import(chase_run, monkeypatch):
    run, credentials = chase_run
    monkeypatch.setattr(chase_browser, '_open_export', lambda page: None)
    monkeypatch.setattr(chase_browser, '_check_chase_page', lambda page: None)
    monkeypatch.setattr(chase_browser, '_account_choices',
                        lambda page: [{'index': 0, 'label': 'Current Chase account'}])
    def download(page, index):
        chase_browser.cancel_run(run.id)
        return b'not imported'
    monkeypatch.setattr(chase_browser, '_download_account', download)
    def unexpected(*args, **kwargs):
        pytest.fail('cancelled download was imported')
    monkeypatch.setattr(chase_browser, 'import_chase_qfx', unexpected)
    with pytest.raises(chase_browser.ChaseBrowserCancelled):
        chase_browser._export_accounts(None, run.id, credentials)
