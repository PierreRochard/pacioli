"""Alembic migrations build the test database; these tests keep them honest."""

import importlib.util
import re

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from flask import current_app

from pacioli.database.migration_scope import CONFIGURE_ARGS
from pacioli.extensions import MIGRATIONS, db


def revision_module(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / 'versions' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_database_is_at_the_newest_revision(app_context):
    script = ScriptDirectory.from_config(current_app.extensions['migrate'].migrate.get_config())
    with db.engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == tuple(script.get_heads())


def test_models_match_the_migrated_schema(app_context):
    # A model change without a migration fails here; `manage.py db migrate` drafts one.
    with db.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts=CONFIGURE_ARGS)
        assert compare_metadata(context, db.metadata) == []


def test_baseline_refuses_a_database_that_already_has_a_schema(app_context):
    baseline = revision_module('0001_baseline')
    with db.engine.connect() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='db stamp 0001'):
                baseline.upgrade()


def test_baseline_sql_changes_only_its_own_transaction():
    sql = (MIGRATIONS / 'versions' / '0001_baseline.sql').read_text()
    assert not re.search(r'^\\', sql, re.M), 'psql meta-commands are not SQL'
    assert not re.search(r'^SET (?!LOCAL )', sql, re.M)
    assert "set_config('search_path', '', true)" in sql


LAYOUT = '''
SELECT 'constraint ' || conrelid::regclass || ' ' || conname || ' ' || pg_get_constraintdef(oid)
  FROM pg_constraint WHERE conrelid IN ('admin.users'::regclass, 'ofx.stmttrn'::regclass,
       'ofx.invbanktran'::regclass, 'bookkeeping.ofx_source_identities'::regclass)
UNION ALL
SELECT 'index ' || pg_get_indexdef(indexrelid) FROM pg_index
 WHERE indrelid IN ('admin.users'::regclass, 'ofx.stmttrn'::regclass, 'ofx.invbanktran'::regclass)
UNION ALL
SELECT 'view ' || oid::regclass || ' ' || pg_get_viewdef(oid) FROM pg_class
 WHERE oid IN ('ofx.investment_transactions'::regclass, 'ofx.cost_bases'::regclass)
ORDER BY 1
'''
PRE_ALEMBIC = '''
ALTER TABLE admin.users DROP CONSTRAINT users_fs_uniquifier_key;
CREATE UNIQUE INDEX users_fs_uniquifier_key ON admin.users (fs_uniquifier);
ALTER TABLE bookkeeping.ofx_source_identities
    DROP CONSTRAINT ofx_source_identities_acctfrom_id_fitid_fkey;
ALTER TABLE ofx.stmttrn DROP CONSTRAINT stmttrn_pkey,
    ADD CONSTRAINT stmttrn_pkey PRIMARY KEY (fitid, acctfrom_id);
ALTER TABLE ofx.invbanktran DROP CONSTRAINT invbanktran_pkey,
    ADD CONSTRAINT invbanktran_pkey PRIMARY KEY (fitid, acctfrom_id);
ALTER TABLE bookkeeping.ofx_source_identities ADD CONSTRAINT
    ofx_source_identities_acctfrom_id_fitid_fkey FOREIGN KEY (acctfrom_id, fitid)
    REFERENCES ofx.stmttrn(acctfrom_id, fitid) ON DELETE CASCADE;
DROP VIEW ofx.cost_bases;
DROP VIEW ofx.investment_transactions;
CREATE VIEW ofx.investment_transactions AS SELECT subclass, acctfrom_id FROM ofx.invtran;
CREATE VIEW ofx.cost_bases AS SELECT subclass FROM ofx.investment_transactions;
'''


def test_alignment_turns_the_pre_alembic_layout_into_the_baseline(app_context):
    align = revision_module('0002_align_pre_alembic').align
    db.session.remove()
    with db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            baseline = connection.exec_driver_sql(LAYOUT).scalars().all()
            align(connection)
            assert connection.exec_driver_sql(LAYOUT).scalars().all() == baseline
            connection.exec_driver_sql(PRE_ALEMBIC)
            assert connection.exec_driver_sql(LAYOUT).scalars().all() != baseline
            align(connection)
            assert connection.exec_driver_sql(LAYOUT).scalars().all() == baseline
        finally:
            transaction.rollback()
