"""Initialize the PostgreSQL schemas used by Pacioli and its OFX fork."""

import csv
from pathlib import Path

from ofxtools.ofxalchemy import Base as OFX_Base

from pacioli.extensions import db
from pacioli.models import Elements, Classifications, Accounts, Subaccounts


SCHEMAS = ('admin', 'bookkeeping', 'ofx', 'amazon', 'investments',
           'payroll', 'tax')


def ofx_engine():
    # The OFX fork declares unqualified tables. Keep them in the ofx schema.
    return db.engine.execution_options(schema_translate_map={None: 'ofx'})


def create_database():
    for schema in SCHEMAS:
        db.engine.execute('CREATE SCHEMA IF NOT EXISTS "{}"'.format(schema))

    db.create_all()
    from pacioli.database.workbench import install_workbench
    install_workbench()
    from pacioli.functions.mapping_workflow import install_mapping_columns
    with db.engine.begin() as connection:
        install_mapping_columns(connection)
    db.engine.execute("ALTER TABLE bookkeeping.accounts "
                      "ADD COLUMN IF NOT EXISTS is_cash boolean NOT NULL DEFAULT false")
    db.engine.execute("ALTER TABLE admin.connections "
                      "ADD COLUMN IF NOT EXISTS currency varchar")
    db.engine.execute("ALTER TABLE admin.connection_responses "
                      "ADD COLUMN IF NOT EXISTS import_status varchar(16) NOT NULL DEFAULT 'unreviewed', "
                      "ADD COLUMN IF NOT EXISTS import_error varchar(64)")
    from pacioli.database.accounting_migration import install_journal_constraints
    install_journal_constraints()
    OFX_Base.metadata.create_all(ofx_engine())
    from pacioli.database.source_identity import install_source_identities
    install_source_identities()
    from pacioli.database.brokerage import install_brokerage
    install_brokerage()

    from pacioli.database.sql_views import create_all
    create_all()
    db.engine.execute("INSERT INTO admin.integrity_migrations(version) VALUES('journal-money-v2') "
                      "ON CONFLICT DO NOTHING")


def populate_chart():
    """Load the bundled chart once, preserving existing account records."""
    chart = Path(__file__).resolve().parents[2] / 'Generic Chart of Accounts.csv'

    def ensure(model, **values):
        if db.session.query(model).filter_by(name=values['name']).first() is None:
            db.session.add(model(**values))
            db.session.flush()

    with chart.open(newline='', encoding='utf-8') as chart_file:
        for row in csv.DictReader(chart_file):
            ensure(Elements, name=row['Element'])
            ensure(Classifications, name=row['Classification'],
                   parent=row['Element'])
            ensure(Accounts, name=row['Account'],
                   cash_source=row['Cash Source'],
                   parent=row['Classification'])
            ensure(Subaccounts, name=row['Subaccount'],
                   parent=row['Account'])

    # A dedicated flag is stable even when cash accounts are renamed.
    cash = db.session.query(Accounts).filter_by(name='Cash').first()
    if cash is not None:
        cash.is_cash = True

    db.session.commit()
