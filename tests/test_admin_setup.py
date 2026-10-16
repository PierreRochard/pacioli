"""First-admin setup stays one-use and separate from normal registration."""

import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.schema import CreateTable

from pacioli import create_app
from pacioli.admin_setup import issue_admin_link
from pacioli.extensions import db, run_sql
from pacioli.models import AdminSetupLinks, Roles, Users, roles_users
from pacioli.settings import DevConfig


@pytest.fixture
def bootstrap_app(database_url):
    parsed = make_url(database_url)
    admin = create_engine(str(parsed.set(database='postgres')),
                          isolation_level='AUTOCOMMIT')

    def admin_sql(statement):
        with admin.connect() as connection:
            connection.exec_driver_sql(statement)

    name = 'pacioli_bootstrap_' + uuid4().hex[:12]
    admin_sql('CREATE DATABASE "{}"'.format(name))

    class BootstrapConfig(DevConfig):
        TESTING = True
        WTF_CSRF_ENABLED = True
        SQLALCHEMY_DATABASE_URI = str(parsed.set(database=name))

    app = create_app(BootstrapConfig, env='test')
    with app.app_context():
        run_sql('CREATE SCHEMA admin')
        # The legacy OFX models register global metadata events, so create
        # only the admin tables instead of invoking that unrelated metadata.
        for table in (Roles.__table__, Users.__table__, roles_users,
                      AdminSetupLinks.__table__):
            run_sql(CreateTable(table))
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        admin_sql('DROP DATABASE "{}" WITH (FORCE)'.format(name))
        admin.dispose()


def test_admin_magic_link_is_private_one_use_and_csrf_protected(bootstrap_app):
    with bootstrap_app.app_context():
        link = issue_admin_link(db.session, 'https://pacioli.lan')
        db.session.commit()
        token = link.partition('#')[2]
        stored = db.session.query(AdminSetupLinks).one()
        assert token not in stored.token_digest
        assert link.startswith('https://pacioli.lan/setup/admin#')

    client = bootstrap_app.test_client()
    page = client.get('/setup/admin')
    assert page.status_code == 200
    assert client.head('/setup/admin').status_code == 200
    assert b'Create your administrator account' in page.data
    assert b'location.hash.slice(1)' in page.data
    assert token.encode() not in page.data
    assert page.headers['Cache-Control'] == 'no-store'
    csrf = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1)
    form = {'token': token, 'email': 'owner@example.com',
            'password': 'a-long-unique-password',
            'confirm_password': 'a-long-unique-password'}

    assert client.post('/setup/admin', data=form).status_code == 400
    with bootstrap_app.app_context():
        assert db.session.query(Users).count() == 0

    form['csrf_token'] = csrf.decode()
    form['password'] = 'short'
    form['confirm_password'] = 'short'
    assert client.post('/setup/admin', data=form).status_code == 400

    form['password'] = 'a-long-unique-password'
    form['confirm_password'] = form['password']
    assert client.post('/setup/admin', data=form).status_code == 302
    with bootstrap_app.app_context():
        user = db.session.query(Users).one()
        assert user.email == 'owner@example.com'
        assert user.has_role('administrator')
        assert db.session.query(AdminSetupLinks).one().used_at is not None
        with pytest.raises(ValueError, match='already exists'):
            issue_admin_link(db.session, 'https://pacioli.lan')
        db.session.rollback()
    assert client.post('/setup/admin', data=form).status_code == 404
    assert client.get('/setup/admin').status_code == 404


def test_new_admin_link_revokes_previous_link(bootstrap_app):
    with bootstrap_app.app_context():
        old = issue_admin_link(db.session, 'https://pacioli.lan')
        db.session.commit()
        new = issue_admin_link(db.session, 'https://pacioli.lan')
        db.session.commit()
        assert old != new
        assert db.session.query(AdminSetupLinks).filter_by(revoked_at=None).count() == 1

    client = bootstrap_app.test_client()
    page = client.get('/setup/admin')
    csrf = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1)
    form = {'csrf_token': csrf.decode(), 'email': 'owner@example.com',
            'password': 'a-long-unique-password',
            'confirm_password': 'a-long-unique-password'}
    form['token'] = old.partition('#')[2]
    assert client.post('/setup/admin', data=form).status_code == 404
    form['token'] = new.partition('#')[2]
    assert client.post('/setup/admin', data=form).status_code == 302
