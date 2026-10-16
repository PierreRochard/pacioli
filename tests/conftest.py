"""Tests use a newly created database and never reuse application data."""

from datetime import datetime, timezone
import os
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url


for name, value in {
    'SECRET_KEY': 'test-only-secret',
    'SECURITY_PASSWORD_SALT': 'test-only-salt',
    'MAIL_USERNAME': 'noreply@example.com',
    'MAIL_PASSWORD': 'test-only-password',
    'PGUSER': 'test-only-user',
    'PGPASSWORD': 'test-only-password',
    'PGHOST': 'localhost',
    'PGPORT': '5432',
    'PACIOLI_CREDENTIAL_KEY': Fernet.generate_key().decode('ascii'),
}.items():
    os.environ.setdefault(name, value)


@pytest.fixture(scope='session')
def database_url():
    admin_url = os.environ.get('PACIOLI_TEST_ADMIN_URL')
    if not admin_url:
        pytest.fail('set PACIOLI_TEST_ADMIN_URL to run PostgreSQL integration tests')

    parsed = make_url(admin_url)
    if parsed.get_backend_name() != 'postgresql' or parsed.database != 'postgres':
        pytest.fail('PACIOLI_TEST_ADMIN_URL must point to the postgres maintenance database')

    database_name = 'pacioli_test_' + uuid.uuid4().hex[:12]
    admin_engine = create_engine(admin_url, isolation_level='AUTOCOMMIT')

    def admin_sql(statement):
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(statement)

    created = False
    try:
        admin_sql('CREATE DATABASE "{}"'.format(database_name))
        created = True
        # Period labels for timestamptz values must not depend on the host's
        # local PostgreSQL timezone when tests run on another workstation.
        admin_sql('ALTER DATABASE "{}" SET timezone TO UTC'.format(database_name))
        yield str(parsed.set(database=database_name))
    finally:
        if created:
            admin_sql('DROP DATABASE "{}" WITH (FORCE)'.format(database_name))
        admin_engine.dispose()


@pytest.fixture(scope='session')
def app(database_url):
    from pacioli import create_app
    from pacioli.database.setup import populate_chart, upgrade_database
    from pacioli.extensions import db
    from pacioli.models import register_views
    from pacioli.settings import DevConfig

    class TestConfig(DevConfig):
        TESTING = True
        DEBUG = False
        WTF_CSRF_ENABLED = False
        MAIL_SUPPRESS_SEND = True
        SQLALCHEMY_DATABASE_URI = database_url

    application = create_app(TestConfig, env='test')
    with application.app_context():
        upgrade_database()
        populate_chart()
        register_views()

        import pacioli.views.admin_views
        import pacioli.views.bookkeeping_views
        import pacioli.views.accounting_views
        import pacioli.views.ofx_views
        import pacioli.views.amazon_views
        import pacioli.views.payroll_views
        import pacioli.views.tax_views

    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def app_context(app):
    from pacioli.extensions import db

    with app.app_context():
        yield
        db.session.remove()


@pytest.fixture
def clean_ledger(app_context):
    """Isolate journal and balance assertions within the disposable test DB."""
    from pacioli.extensions import db, run_sql

    sql = ('TRUNCATE bookkeeping.opening_balances, bookkeeping.transfer_match_legs, '
           'bookkeeping.transfer_matches, bookkeeping.bank_statements, '
           'bookkeeping.accounting_reviews, bookkeeping.accounting_evaluation_cases, '
           'amazon.policy_answers, amazon.product_rules, amazon.suspense_facts, amazon.suspense_items, amazon.suspense_plans, '
           'amazon.item_post_runs, amazon.item_jobs, amazon.item_runs, amazon.evidence_bundles, amazon.collection_runs, bookkeeping.accounting_evidence, '
           'bookkeeping.ofx_journal_sources, bookkeeping.journal_entries, bookkeeping.trial_balances, '
           'bookkeeping.journal_entry_events, bookkeeping.closed_periods, '
           'bookkeeping.accruals, bookkeeping.bank_feed_accounts, '
           'bookkeeping.ofx_source_identities, ofx.stmttrn, '
           'amazon.items, investments.brokerage_journal_sources, investments.valuation_points, '
           'investments.position_lines, investments.position_snapshots, investments.wash_adjustments, '
           'investments.realized_lots, investments.activity_observations, investments.brokerage_activity, '
           'investments.brokerage_opening_lines, investments.brokerage_openings, '
           'investments.share_move_rules, investments.share_move_proposals, '
           'investments.brokerage_sources, investments.brokerage_exports, investments.brokerage_accounts '
           'RESTART IDENTITY')
    db.session.remove()
    run_sql(sql)
    yield
    db.session.remove()
    run_sql(sql)


@pytest.fixture
def clean_amazon_orders(app_context):
    """The report-request test must choose from only its own order history."""
    from pacioli.extensions import db, run_sql

    sql = 'TRUNCATE amazon.items, amazon.orders RESTART IDENTITY'
    db.session.remove()
    run_sql(sql)
    yield
    db.session.remove()
    run_sql(sql)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(scope='session')
def admin_user(app):
    from flask_security.utils import hash_password
    from pacioli.extensions import db
    from pacioli.models import Roles, Users

    with app.app_context():
        role = Roles(name='administrator', description='administrator')
        user = Users(email='admin@example.com',
                     password=hash_password('test-password'),
                     active=True, confirmed_at=datetime.now(timezone.utc))
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()
    return {'email': 'admin@example.com', 'password': 'test-password'}
