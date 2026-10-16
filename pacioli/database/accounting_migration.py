"""Fail-closed, repeatable PostgreSQL journal constraint installation."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from pacioli.extensions import db
from pacioli.models import JournalEntries


TABLE = 'bookkeeping.journal_entries'
NEW_COLUMNS = (
    "ADD COLUMN IF NOT EXISTS allocation_key varchar NOT NULL DEFAULT '0'",
    'ADD COLUMN IF NOT EXISTS conversion_rate numeric',
    'ADD COLUMN IF NOT EXISTS conversion_source varchar',
    'ADD COLUMN IF NOT EXISTS conversion_date date',
    'ADD COLUMN IF NOT EXISTS memo varchar',
    'ADD COLUMN IF NOT EXISTS mapping_decision json',
)
OLD_CONSTRAINTS = ('journal_entries_unique_constraint',
                   'check_functional_amount_positive',
                   'check_source_amount_positive')


def _constraints(connection):
    return {row[0] for row in connection.exec_driver_sql("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'bookkeeping.journal_entries'::regclass
    """)}


def _add_columns(connection):
    for clause in NEW_COLUMNS:
        connection.exec_driver_sql('ALTER TABLE {} {}'.format(TABLE, clause))


def install_journal_constraints():
    """Install new constraints only if every historical posting already qualifies.

    Any failure rolls back column additions as well. No amounts are rounded or
    source data rewritten; the raised error names failing record IDs.
    """
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout='10s'")
        _add_columns(connection)
        constraints = sorted(
            (item for item in JournalEntries.__table__.constraints
             if isinstance(item, CheckConstraint)), key=lambda item: item.name)
        for constraint in constraints:
            condition = str(constraint.sqltext.compile(dialect=db.engine.dialect))
            failures = [row[0] for row in connection.exec_driver_sql(
                'SELECT id FROM {} WHERE ({}) IS NOT TRUE ORDER BY id LIMIT 10'.format(
                    TABLE, condition))]
            if failures:
                raise ValueError('Historical journal entries violate {}: IDs {}. '
                                 'Review and correct them before installing controls.'
                                 .format(constraint.name, failures))
        installed = _constraints(connection)
        for name in OLD_CONSTRAINTS:
            if name in installed:
                connection.exec_driver_sql('ALTER TABLE {} DROP CONSTRAINT {}'.format(TABLE, name))
        installed = _constraints(connection)
        # Replace named checks inside the same locked transaction after preflight.
        # A name-only check leaves older (weaker) definitions installed forever.
        for constraint in constraints:
            expression = str(constraint.sqltext.compile(dialect=db.engine.dialect))
            if constraint.name in installed:
                connection.exec_driver_sql('ALTER TABLE {} DROP CONSTRAINT {}'.format(TABLE, constraint.name))
            connection.exec_driver_sql('ALTER TABLE {} ADD CONSTRAINT {} CHECK (({}) IS TRUE)'.format(
                TABLE, constraint.name, expression))
        unique = next(item for item in JournalEntries.__table__.constraints
                      if isinstance(item, UniqueConstraint) and
                      item.name == 'journal_entries_source_allocation_unique')
        if unique.name not in _constraints(connection):
            connection.exec_driver_sql('ALTER TABLE {} ADD CONSTRAINT {} UNIQUE '
                               '(transaction_id, transaction_source, allocation_key)'.format(
                                   TABLE, unique.name))
        connection.exec_driver_sql("""CREATE OR REPLACE FUNCTION bookkeeping.preserve_mapping_decision()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.mapping_decision IS NOT NULL AND
               OLD.mapping_decision::jsonb IS DISTINCT FROM NEW.mapping_decision::jsonb THEN
                RAISE EXCEPTION 'Mapping decision evidence cannot be changed';
            END IF;
            RETURN NEW; END $$;
            DROP TRIGGER IF EXISTS mapping_decision_immutable ON bookkeeping.journal_entries;
            CREATE TRIGGER mapping_decision_immutable BEFORE UPDATE ON bookkeeping.journal_entries
            FOR EACH ROW EXECUTE FUNCTION bookkeeping.preserve_mapping_decision();""")


def prepare_journal_columns():
    """Add nullable conversion fields to an old schema for reviewed backfill."""
    with db.engine.begin() as connection:
        _add_columns(connection)
