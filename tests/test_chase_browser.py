"""Supervised Chase connector tests use only disposable local PostgreSQL."""

from datetime import datetime, timedelta, timezone
import base64
import json
import re
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import text

from pacioli.extensions import db, run_sql
from pacioli.functions import chase_browser
from pacioli.functions.chase_diagnostics import (
    MAX_EVENTS, ChasePasskeyDiagnostics, _chase_endpoint, _machine_codes)
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


def _test_passkey():
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.DER,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return {
        'credentialId': base64.b64encode(b'test-credential').decode(),
        'isResidentCredential': True,
        'rpId': 'chase.com',
        'privateKey': base64.b64encode(private).decode(),
        'userHandle': base64.b64encode(b'test-user').decode(),
        'signCount': 0,
    }


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
        assert status.json == {'configured': True, 'browser_engine': 'chromium', 'passkey_saved': False,
                               'passkey_enrollment_enabled': False,
                               'passkey_verified': False,
                               'provider_verified': False,
                               'saved_account_count': 0, 'run': None}
        assert status.headers['Cache-Control'] == 'no-store'
        assert b'Chase rejected both supervised software-passkey' in shown.data
        assert b'id="chase-enroll-button" type="submit" disabled' in shown.data
        client.post(PATH, data={'csrf_token': _csrf(shown.data),
                                'action': 'enroll_passkey'})
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


def test_headless_sync_starts_only_on_request_and_reuses_encrypted_state(
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

        class FakeContext:
            def storage_state(self, indexed_db=False):
                assert indexed_db is True
                return {'cookies': [{'name': 'session', 'value': 'private-token'}],
                        'origins': []}

        chase_browser._save_session_state(FakeContext(), credentials)
        raw = db.session.execute(text(
            'SELECT session_state FROM admin.chase_browser_credentials '
            'WHERE id = :id'), {'id': credentials.id}).scalar()
        assert raw.startswith(PREFIX)
        assert 'private-token' not in raw
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


def test_local_passkey_enrollment_requires_chase_confirmation_and_encrypts_key(
        clean_chase_connector, app, client, admin_user, monkeypatch):
    monkeypatch.setitem(app.config, 'CHASE_PASSKEY_ENROLLMENT_ENABLED', True)
    client.post('/login/', data=admin_user)
    token = _csrf(client.get(PATH).data)
    client.post(PATH, data={'csrf_token': token, 'action': 'save',
                            'username': 'test-user', 'password': 'test-password'})
    credentials = db.session.query(ChaseBrowserCredentials).one()
    launched = []
    monkeypatch.setattr(chase_browser, '_launch_systemd', launched.append)
    prior_csrf = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post(PATH, data={'action': 'enroll_passkey'}).status_code == 400
        client.post(PATH, data={'csrf_token': token, 'action': 'enroll_passkey'})
        run = db.session.query(ChaseBrowserRuns).one()
        assert run.mode == 'passkey_enroll'
        assert run.reviewed_usd is False
        assert launched == [run.id]
        client.post(PATH, data={'csrf_token': token, 'action': 'confirm_passkey',
                                'run_id': str(run.id)})
        db.session.refresh(run)
        assert run.passkey_confirmed is False
        assert credentials.passkey_credential is None

        credential = dict(_test_passkey(), backupEligibility=True,
                          backupState=False)

        class FakeSession:
            def send(self, method, params):
                assert method == 'WebAuthn.getCredentials'
                assert params == {'authenticatorId': 'virtual-1'}
                return {'credentials': [credential]}

        class FakePage:
            url = 'https://secure.chase.com/settings'

            def wait_for_timeout(self, milliseconds):
                chase_browser.cancel_run(run.id)

        with pytest.raises(chase_browser.ChaseBrowserCancelled):
            chase_browser._wait_for_passkey_enrollment(
                (FakeSession(), 'virtual-1'), FakePage(), run.id, credentials)
        db.session.refresh(credentials)
        assert credentials.passkey_credential is None
        run.cancel_requested = False
        run.mfa_prompt = 'passkey_review'
        db.session.commit()
        client.post(PATH, data={'csrf_token': token, 'action': 'confirm_passkey',
                                'run_id': str(run.id)})
        db.session.refresh(run)
        assert run.passkey_confirmed is True

        class MultipleSession(FakeSession):
            def send(self, method, params):
                return {'credentials': [credential, _test_passkey()]}

        with pytest.raises(chase_browser.ChaseBrowserError) as error:
            chase_browser._wait_for_passkey_enrollment(
                (MultipleSession(), 'virtual-1'), FakePage(), run.id,
                credentials)
        assert error.value.code == 'multiple_passkeys_created'
        db.session.refresh(credentials)
        assert credentials.passkey_credential is None

        chase_browser._wait_for_passkey_enrollment(
            (FakeSession(), 'virtual-1'), FakePage(), run.id, credentials)
        db.session.refresh(credentials)
        assert credentials.passkey_credential is not None
        assert json.loads(credentials.passkey_credential) == credential
        raw = db.session.execute(text(
            'SELECT passkey_credential FROM admin.chase_browser_credentials '
            'WHERE id = :id'), {'id': credentials.id}).scalar()
        assert raw.startswith(PREFIX)
        assert credential['privateKey'] not in raw
        status = client.get('/connections/chase/status/').json
        assert status['passkey_saved'] is True
        assert status['passkey_verified'] is False
        assert credential['privateKey'] not in str(status)
        client.post(PATH, data={'csrf_token': token, 'action': 'enroll_passkey'})
        assert db.session.query(ChaseBrowserRuns).count() == 1
        client.post(PATH, data={'csrf_token': token, 'action': 'test_passkey'})
        test_run = db.session.query(ChaseBrowserRuns).order_by(
            ChaseBrowserRuns.id.desc()).first()
        assert test_run.mode == 'passkey_test'
        assert test_run.reviewed_usd is False
        assert launched == [run.id, test_run.id]
        test_run.status = 'completed'
        credentials.passkey_verified_at = datetime.now(timezone.utc)
        db.session.commit()
        assert client.get('/connections/chase/status/').json['passkey_verified'] is True
        client.post(PATH, data={'csrf_token': token, 'action': 'forget_passkey'})
        db.session.refresh(credentials)
        assert credentials.passkey_credential is None
        assert credentials.passkey_verified_at is None
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior_csrf


def test_chase_rejection_stops_enrollment_and_preserves_diagnostics(
        clean_chase_connector, app, client, admin_user, monkeypatch):
    client.post('/login/', data=admin_user)
    client.post(PATH, data={'action': 'save', 'username': 'test-user',
                            'password': 'test-password'})
    connection = db.session.query(Connections).filter_by(source='chase_browser').one()
    from pacioli.models import Users
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    connection_id, user_id = connection.id, user.id
    run = ChaseBrowserRuns(connection_id=connection.id, requested_by_id=user.id,
                           status='waiting_for_mfa', mode='passkey_enroll',
                           mfa_prompt='passkey_review',
                           updated_at=datetime.now(timezone.utc))
    db.session.add(run)
    db.session.commit()

    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    token = _csrf(client.get(PATH).data)
    assert client.post(PATH, data={'action': 'reject_passkey',
                                   'run_id': str(run.id)}).status_code == 400
    client.post(PATH, data={'csrf_token': token, 'action': 'reject_passkey',
                            'run_id': str(run.id)})
    db.session.refresh(run)
    assert run.cancel_requested is True
    assert run.error_code == 'chase_passkey_rejected'
    client.post(PATH, data={'csrf_token': token, 'action': 'confirm_passkey',
                            'run_id': str(run.id)})
    db.session.refresh(run)
    assert run.passkey_confirmed is False

    def cancelled(*args, **kwargs):
        raise chase_browser.ChaseBrowserCancelled()

    monkeypatch.setattr(chase_browser, '_run_browser', cancelled)
    run_id = run.id
    chase_browser.run_worker(run_id)
    run = db.session.get(ChaseBrowserRuns, run_id)
    assert run.status == 'failed'
    assert run.error_code == 'chase_passkey_rejected'
    assert json.loads(run.diagnostics)['events'][-1]['error_code'] == (
        'chase_passkey_rejected')
    assert db.session.query(ChaseBrowserCredentials).one().passkey_credential is None

    failed = ChaseBrowserRuns(connection_id=connection_id,
                              requested_by_id=user_id, status='queued',
                              mode='passkey_enroll',
                              updated_at=datetime.now(timezone.utc))
    db.session.add(failed)
    db.session.commit()

    def unexpected(*args, **kwargs):
        raise RuntimeError('secret-password')

    monkeypatch.setattr(chase_browser, '_run_browser', unexpected)
    failed_id = failed.id
    chase_browser.run_worker(failed_id)
    failed = db.session.get(ChaseBrowserRuns, failed_id)
    assert failed.status == 'failed'
    report = json.loads(failed.diagnostics)
    assert report['events'][-1]['exception_type'] == 'RuntimeError'
    assert report['events'][-1]['frames']
    assert 'secret-password' not in json.dumps(report)


def test_local_passkey_must_have_chase_relying_party_and_valid_fields():
    value = _test_passkey()
    assert chase_browser._validate_passkey_credential(value) == value
    backed_up = dict(value, backupEligibility=True, backupState=True,
                     unrelatedField='discarded')
    assert chase_browser._validate_passkey_credential(backed_up) == dict(
        value, backupEligibility=True, backupState=True)
    for bad in (dict(value, rpId='example.com'),
                dict(value, privateKey='invalid value'),
                dict(value, credentialId=''),
                dict(value, isResidentCredential=False),
                dict(value, signCount=True),
                dict(value, backupEligibility=True),
                dict(value, backupEligibility=1, backupState=False),
                dict(value, backupEligibility=False, backupState=True)):
        with pytest.raises(chase_browser.ChaseBrowserError) as error:
            chase_browser._validate_passkey_credential(bad)
        assert error.value.code == 'invalid_passkey'


def test_saved_passkey_is_installed_only_in_requested_browser_context():
    credential = dict(_test_passkey(), backupEligibility=True,
                      backupState=False)

    class Stored:
        passkey_credential = json.dumps(credential)

    sent = []

    class FakeSession:
        def send(self, method, params):
            sent.append((method, params))

    assert chase_browser._install_saved_passkey(
        (FakeSession(), 'virtual-1'), Stored()) is True
    assert sent == [('WebAuthn.addCredential', {
        'authenticatorId': 'virtual-1', 'credential': credential})]


def test_saved_passkey_counter_advances_without_changing_credential():
    original = dict(_test_passkey(), backupEligibility=True,
                    backupState=False, signCount=3)

    class Stored:
        passkey_credential = json.dumps(original)
        updated_at = None

    class Session:
        candidate = dict(original, signCount=4)

        def send(self, method, params):
            assert method == 'WebAuthn.getCredentials'
            assert params == {'authenticatorId': 'virtual-1'}
            return {'credentials': [self.candidate]}

    stored, session = Stored(), Session()
    chase_browser._refresh_saved_passkey((session, 'virtual-1'), stored)
    assert json.loads(stored.passkey_credential) == session.candidate
    assert stored.updated_at is not None
    for bad in (dict(original, signCount=2),
                dict(original, credentialId=base64.b64encode(b'other').decode()),
                dict(original, backupEligibility=False)):
        session.candidate = bad
        with pytest.raises(chase_browser.ChaseBrowserError) as error:
            chase_browser._refresh_saved_passkey((session, 'virtual-1'), stored)
        assert error.value.code == 'invalid_passkey'


def test_native_authenticator_is_added_before_navigation():
    sent = []

    class FakeSession:
        def send(self, method, params):
            sent.append((method, params))
            if method == 'WebAuthn.addVirtualAuthenticator':
                return {'authenticatorId': 'virtual-1'}

    class FakeContext:
        def new_cdp_session(self, page):
            assert page == 'blank-page'
            return FakeSession()

    session, authenticator_id = chase_browser._create_authenticator(
        FakeContext(), 'blank-page')
    assert isinstance(session, FakeSession)
    assert authenticator_id == 'virtual-1'
    assert sent[0] == ('WebAuthn.enable', {'enableUI': False})
    assert sent[1][0] == 'WebAuthn.addVirtualAuthenticator'
    assert sent[1][1]['options'] == chase_browser.PASSKEY_AUTHENTICATOR_OPTIONS


def test_passkey_diagnostics_keep_chase_error_codes_without_secrets():
    context_events, page_events, cdp_events = {}, {}, {}

    class Emitter:
        def __init__(self, events):
            self.events = events

        def on(self, name, callback):
            self.events[name] = callback

    diagnostics = ChasePasskeyDiagnostics(7, browser_major=130)
    diagnostics.attach(Emitter(context_events), Emitter(page_events),
                       (Emitter(cdp_events), 'authenticator-1'))

    def response(status, path, payload, content_type='application/json'):
        body = (payload.encode() if isinstance(payload, str) else
                json.dumps(payload).encode())
        item = SimpleNamespace(
            url='https://secure.chase.com{}?token=secret-query'.format(path),
            status=status,
            request=SimpleNamespace(resource_type='fetch', method='POST'),
            headers={'content-type': content_type,
                     'content-length': str(len(body)),
                     'x-request-id': 'request-12345678'},
            body=lambda: body)
        item.request.response = lambda: item
        return item

    def emit(item):
        context_events['response'](item)
        context_events['requestfinished'](item.request)

    emit(response(200, '/api/preflight', {'accountNumber': '123456789'}))
    cdp_events['WebAuthn.credentialAdded']({
        'authenticatorId': 'authenticator-1',
        'credential': {'rpId': 'chase.com', 'isResidentCredential': True,
                       'credentialId': 'secret-credential-id',
                       'privateKey': 'secret-private-key'}})
    emit(response(200, '/api/register', {
        'errors': [{'errorCode': 'ATTESTATION_REJECTED',
                    'message': 'secret-user@example.com 123456789'}]}))
    emit(response(500, '/api/passkey', {
        'errorCode': 'WEBAUTHN_UNSUPPORTED', 'accessToken': 'secret-token'}))
    emit(response(400, '/api/register',
                  '<html>secret-user@example.com 123456789</html>',
                  content_type='text/html'))
    page_events['console'](SimpleNamespace(
        type='error', text='secret-user@example.com 123456789'))
    diagnostics.drain()
    report = diagnostics.report()
    serialized = json.dumps(report)
    assert 'ATTESTATION_REJECTED' in serialized
    assert 'WEBAUTHN_UNSUPPORTED' in serialized
    assert 'credential_added' in serialized
    assert 'chase_error_body' in serialized
    assert 'chase_error_body_shape' in serialized
    assert 'request-12345678' in serialized
    assert report['summary']['credential_added_count'] == 1
    assert report['summary']['http_status_counts'] == {
        '200': 1, '500': 1, '400': 1}
    for secret in ('secret-query', 'secret-user', '123456789',
                   'secret-credential-id', 'secret-private-key',
                   'secret-token', '/api/register', '/api/passkey'):
        assert secret not in serialized


def test_passkey_diagnostics_reject_foreign_hosts_and_bound_event_count():
    assert _chase_endpoint('https://chase.com.evil.example/passkey') is None
    endpoint = _chase_endpoint(
        'https://secure.chase.com/private/123456789?token=secret')
    assert endpoint['host'] == 'secure.chase.com'
    assert '123456789' not in str(endpoint)
    assert 'secret' not in str(endpoint)
    assert _machine_codes({'code': 'pierre_private',
                           'errorCode': 'WEBAUTHN_REJECTED',
                           'message': 'secret-user@example.com'}) == [
                               {'field': 'errorCode',
                                'value': 'WEBAUTHN_REJECTED'}]
    diagnostics = ChasePasskeyDiagnostics(7)
    for index in range(MAX_EVENTS + 5):
        diagnostics.record('test_event', count=index)
    assert len(diagnostics.report()['events']) == MAX_EVENTS
    assert diagnostics.report()['dropped_events'] == 5


def test_passkey_diagnostic_report_is_encrypted_and_admin_only(
        clean_chase_connector, client, admin_user):
    assert client.get('/connections/chase/diagnostics/1/').status_code == 302
    client.post('/login/', data=admin_user)
    client.post(PATH, data={'action': 'save', 'username': 'test-user',
                            'password': 'test-password'})
    connection = db.session.query(Connections).filter_by(source='chase_browser').one()
    from pacioli.models import Users
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    run = ChaseBrowserRuns(connection_id=connection.id, requested_by_id=user.id,
                           status='cancelled', mode='passkey_enroll',
                           updated_at=datetime.now(timezone.utc))
    db.session.add(run)
    db.session.commit()
    diagnostics = ChasePasskeyDiagnostics(run.id)
    diagnostics.record('chase_response_detail',
                       codes=[{'field': 'errorCode',
                               'value': 'ATTESTATION_REJECTED'}])
    diagnostics.persist(force=True)
    raw = db.session.execute(text(
        'SELECT diagnostics FROM admin.chase_browser_runs WHERE id = :id'),
        {'id': run.id}).scalar()
    assert raw.startswith(PREFIX)
    assert 'ATTESTATION_REJECTED' not in raw
    response = client.get('/connections/chase/diagnostics/{}/'.format(run.id))
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.json['report']['events'][0]['codes'][0]['value'] == (
        'ATTESTATION_REJECTED')
    assert client.get('/connections/chase/diagnostics/99999/').status_code == 404


def test_legacy_passkey_test_uses_chase_passkey_action_when_available(monkeypatch):
    run = SimpleNamespace(mode='passkey_test', connection_id=1, browser_visible=False)
    monkeypatch.setattr(chase_browser, '_run', lambda run_id: run)
    monkeypatch.setattr(chase_browser, '_heartbeat', lambda run_id: run)
    monkeypatch.setattr(chase_browser, '_signed_in_location', lambda page: False)
    monkeypatch.setattr(chase_browser, '_has_saved_passkey', lambda connection_id: True)
    states = []
    monkeypatch.setattr(chase_browser, '_set_run',
                        lambda run_id, status: states.append(status))

    class Button:
        clicked = False

        def click(self, timeout):
            self.clicked = True

    button = Button()
    monkeypatch.setattr(chase_browser, '_passkey_sign_in_button',
                        lambda page: button)

    class Page:
        url = 'https://secure.chase.com/'

        def goto(self, url, **kwargs):
            assert url == self.url

    assert chase_browser._sign_in(
        Page(), 7, 'saved-user', 'saved-password') is True
    assert button.clicked is True
    assert states == ['waiting_for_mfa']


def test_passkey_test_never_falls_back_to_saved_password(monkeypatch):
    run = SimpleNamespace(mode='passkey_test', connection_id=1, browser_visible=False)
    monkeypatch.setattr(chase_browser, '_run', lambda run_id: run)
    monkeypatch.setattr(chase_browser, '_heartbeat', lambda run_id: run)
    monkeypatch.setattr(chase_browser, '_signed_in_location', lambda page: False)
    monkeypatch.setattr(chase_browser, '_has_saved_passkey', lambda connection_id: True)
    monkeypatch.setattr(chase_browser, '_passkey_sign_in_button', lambda page: None)
    monkeypatch.setattr(chase_browser, '_find_login_frame', lambda page: object())
    ticks = iter(range(100, 200))
    monkeypatch.setattr(chase_browser, 'time',
                        SimpleNamespace(monotonic=lambda: next(ticks)))

    class Page:
        url = 'https://secure.chase.com/'

        def goto(self, url, **kwargs):
            assert url == self.url

        def wait_for_timeout(self, milliseconds):
            pass

    with pytest.raises(chase_browser.ChaseBrowserAttention) as error:
        chase_browser._sign_in(Page(), 7, 'saved-user', 'saved-password')
    assert error.value.code == 'passkey_signin_unavailable'
