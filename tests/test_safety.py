from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from sqlalchemy import text

from pacioli.extensions import db, run_sql
from pacioli.models import ConnectionResponses, Connections, JournalEntries, Subaccounts, TrialBalances
from pacioli.secure_fields import PREFIX, encrypt_existing_data


def test_historical_chase_password_probe_is_unavailable(app, client):
    path = '/connections/test_chase_legacy/'
    assert all(rule.rule != path for rule in app.url_map.iter_rules())
    assert client.get(path).status_code == 404
    assert client.post(path, data={'username': 'dummy', 'password': 'dummy'}).status_code == 404


def test_connection_secrets_are_encrypted_and_legacy_rows_can_be_migrated(app_context):
    connection = Connections(source='ofx', user='test', password='bank-secret')
    db.session.add(connection)
    db.session.flush()
    response = ConnectionResponses(connection_id=connection.id, response='<OFX>private</OFX>')
    db.session.add(response)
    db.session.commit()

    stored = run_sql(text(
        'SELECT password FROM admin.connections WHERE id = :id'
    ), {'id': connection.id}).scalar()
    stored_response = run_sql(text(
        'SELECT response FROM admin.connection_responses WHERE id = :id'
    ), {'id': response.id}).scalar()
    assert stored.startswith(PREFIX) and 'bank-secret' not in stored
    assert stored_response.startswith(PREFIX) and '<OFX>' not in stored_response
    db.session.expire_all()
    assert db.session.get(Connections, connection.id).password == 'bank-secret'
    assert db.session.get(ConnectionResponses, response.id).response == '<OFX>private</OFX>'

    legacy_id = run_sql(text(
        "INSERT INTO admin.connections (source, password) "
        "VALUES ('ofx', 'legacy-secret') RETURNING id"
    )).scalar()
    assert encrypt_existing_data(db.engine) == 1
    assert encrypt_existing_data(db.engine) == 0
    db.session.expire_all()
    assert db.session.get(Connections, legacy_id).password == 'legacy-secret'
    assert run_sql(text(
        'SELECT password FROM admin.connections WHERE id = :id'
    ), {'id': legacy_id}).scalar().startswith(PREFIX)


def test_connection_admin_pages_do_not_render_saved_password(
        app_context, client, admin_user):
    password = 'private-' + uuid4().hex
    connection = Connections(source='ofx', user='test', password=password)
    db.session.add(connection)
    db.session.commit()
    client.post('/login/', data=admin_user)
    for path in ('/connections/',
                 '/connections/new/',
                 '/connections/edit/?id={}'.format(connection.id),
                 '/connections/details/?id={}'.format(connection.id)):
        response = client.get(path)
        assert response.status_code == 200
        assert password.encode() not in response.data
    edit_path = '/connections/edit/?id={}'.format(connection.id)
    page = client.get(edit_path)
    # The Flask-Admin form's token; the navbar's logout form carries its own.
    token = re.search(rb'id="csrf_token"[^>]*value="([^"]+)"', page.data).group(1)
    edit_data = {
        'source': 'ofx', 'type': 'Checking', 'user': 'changed',
        'password': ''}
    assert client.post(edit_path, data=edit_data).status_code == 200
    assert db.session.get(Connections, connection.id).user == 'test'
    edit_data['csrf_token'] = token
    response = client.post(edit_path, data=edit_data, follow_redirects=False)
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Connections, connection.id).password == password


def test_refresh_requires_post_and_csrf_and_preserves_balances(
        app_context, app, client, admin_user):
    name = 'Refresh ' + uuid4().hex[:10]
    db.session.add(Subaccounts(name=name, parent='Rent Expense'))
    db.session.commit()
    db.session.add(JournalEntries(
        timestamp=datetime(2036, 2, 10, tzinfo=timezone.utc),
        debit_subaccount=name, credit_subaccount='Accounts Payable',
        functional_amount=Decimal('9'), functional_currency='USD',
        source_amount=Decimal('9'), source_currency='USD'))
    db.session.commit()

    def balance():
        return (db.session.query(TrialBalances.net_balance)
                .filter_by(subaccount=name, period_interval='YYYY-MM',
                           period='2036-02').scalar())

    assert balance() == 9
    db.session.remove()
    client.post('/login/', data=admin_user)
    old_setting = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        page = client.get('/trialbalances/')
        assert page.status_code == 200
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1)
        db.session.remove()
        endpoint = '/trialbalances/refresh_subaccounts/'
        assert client.get(endpoint).status_code == 405
        assert client.post(endpoint).status_code == 400
        db.session.remove()
        assert client.post(endpoint, data={'csrf_token': token}).status_code == 302
        assert balance() == 9
    finally:
        app.config['WTF_CSRF_ENABLED'] = old_setting


def test_reports_render_when_selected_period_has_no_entries(client, admin_user):
    client.post('/login/', data=admin_user)
    for path in ('/income-statements/YYYY-MM/1900-01/',
                 '/balance-sheet/YYYY-MM/1900-01/',
                 '/journalentries/Rent/YYYY-MM/1900-01/'):
        assert client.get(path).status_code == 200


def _refuse_mail(message):
    raise AssertionError('A database backup must never be mailed')


def test_backup_uses_configured_database_and_keeps_private_archive(
        monkeypatch, tmp_path):
    import manage
    from pacioli.extensions import mail

    captured = {}

    def fake_dump(command, check, env):
        captured['command'] = command
        captured['password'] = env.get('PGPASSWORD')
        archive = command[command.index('--file') + 1]
        with open(archive, 'wb') as output:
            output.write(b'PGDMP-test')

    monkeypatch.setenv('PACIOLI_BACKUP_DIR', str(tmp_path))
    monkeypatch.setattr(manage.shutil, 'which', lambda name: '/usr/bin/pg_dump')
    monkeypatch.setattr(manage.subprocess, 'run', fake_dump)
    monkeypatch.setattr(mail, 'send', _refuse_mail)
    old_url = manage.app.config['SQLALCHEMY_DATABASE_URI']
    manage.app.config['SQLALCHEMY_DATABASE_URI'] = (
        'postgresql+psycopg2://backup-user:backup-pass@localhost:5432/customdb')
    try:
        result = manage.app.test_cli_runner().invoke(args=['backup_db'])
        assert result.exit_code == 0, result.output
    finally:
        manage.app.config['SQLALCHEMY_DATABASE_URI'] = old_url
    assert captured['command'][-1] == 'customdb'
    assert 'backup-pass' not in captured['command']
    assert captured['password'] == 'backup-pass'
    [archive] = tmp_path.iterdir()
    assert archive.read_bytes() == b'PGDMP-test'
    assert archive.stat().st_mode & 0o777 == 0o600


def test_backup_archive_is_readable_by_pg_restore(app, monkeypatch, tmp_path):
    import manage
    from pacioli.extensions import mail

    monkeypatch.setenv('PACIOLI_BACKUP_DIR', str(tmp_path))
    monkeypatch.setattr(mail, 'send', _refuse_mail)
    old_url = manage.app.config['SQLALCHEMY_DATABASE_URI']
    manage.app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI']
    try:
        result = manage.app.test_cli_runner().invoke(args=['backup_db'])
        assert result.exit_code == 0, result.output
    finally:
        manage.app.config['SQLALCHEMY_DATABASE_URI'] = old_url

    [archive] = tmp_path.iterdir()
    bindir = Path(subprocess.check_output(['pg_config', '--bindir'], text=True).strip())
    contents = subprocess.check_output([str(bindir / 'pg_restore'), '--list',
                                        str(archive)], text=True)
    assert 'journal_entries' in contents


def test_subaccount_descriptions_render_as_text(app_context, client, admin_user):
    payload = '<img src=x onerror="window.pwned=1">'
    name = 'Escaped ' + uuid4().hex
    db.session.add(Subaccounts(name=name, parent='Cash', description=payload))
    db.session.commit()
    try:
        client.post('/login/', data=admin_user)
        page = client.get('/subaccounts/?search=' + name)
        assert page.status_code == 200
        assert payload.encode() not in page.data
        assert b'&lt;img src=x' in page.data
        assert b'ckeditor' not in client.get('/subaccounts/new/').data
    finally:
        db.session.query(Subaccounts).filter_by(name=name).delete()
        db.session.commit()
