"""Which database objects Alembic's autogenerate compares with the models.

Before Pacioli adopted Alembic, raw SQL created some tables that have no
SQLAlchemy model, and the OFX importer writes the ofx schema with SQL. Leaving
them out keeps `manage.py db migrate` from proposing to drop them. Any other difference
between the models and the database is a change that needs a migration.
"""

from flask import current_app


SCHEMAS = frozenset(('admin', 'bookkeeping', 'amazon', 'investments', 'payroll', 'tax'))
SQL_MANAGED_TABLES = frozenset((
    'admin.integrity_migrations',
    'bookkeeping.ofx_journal_sources',
    'bookkeeping.ofx_source_identities',
    'investments.activity_observations',
    'investments.brokerage_accounts',
    'investments.brokerage_activity',
    'investments.brokerage_exports',
    'investments.brokerage_journal_sources',
    'investments.brokerage_opening_lines',
    'investments.brokerage_openings',
    'investments.brokerage_sources',
    'investments.position_lines',
    'investments.position_snapshots',
    'investments.price_request_universe',
    'investments.realized_lots',
    'investments.share_move_proposals',
    'investments.share_move_rules',
    'investments.symbol_map',
    'investments.valuation_points',
    'investments.wash_adjustments',
))


def include_name(name, type_, parent_names):
    return name in SCHEMAS if type_ == 'schema' else True


def include_object(obj, name, type_, reflected, compare_to):
    if type_ != 'table':
        return True
    views = current_app.config['MODEL_MAP'].get(obj.schema, {})
    return (obj.schema in SCHEMAS and name not in views and
            '{}.{}'.format(obj.schema, name) not in SQL_MANAGED_TABLES)


# The baseline empties search_path for its transaction, so name the version
# table's schema rather than rely on it.
CONFIGURE_ARGS = dict(include_schemas=True, include_name=include_name,
                      include_object=include_object, compare_type=True,
                      version_table_schema='public')
