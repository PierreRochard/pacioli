"""Bring the database schema up to date and load the bundled chart."""

import csv
from pathlib import Path

from pacioli.extensions import db
from pacioli.models import Elements, Classifications, Accounts, Subaccounts


def ofx_engine():
    # The OFX fork declares unqualified tables. Keep them in the ofx schema.
    return db.engine.execution_options(schema_translate_map={None: 'ofx'})


def upgrade_database():
    """Apply every pending Alembic migration; see migrations/README."""
    from flask_migrate import upgrade
    upgrade()


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
