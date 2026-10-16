"""Baseline: the schema built by the pre-Alembic installers.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from pathlib import Path

from alembic import op
from sqlalchemy import inspect


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).with_suffix('.sql')


def upgrade():
    connection = op.get_bind()
    if inspect(connection).has_table('users', schema='admin'):
        raise RuntimeError(
            'This database already has a Pacioli schema from before Alembic. Do not '
            'rebuild it: run `manage.py db stamp 0001`, then `manage.py db upgrade`.')
    # A DBAPI cursor without parameters, so the dump's PL/pgSQL ":=" and "%"
    # reach PostgreSQL untouched by bind or format parsing.
    cursor = connection.connection.cursor()
    try:
        cursor.execute(SQL.read_text(encoding='utf-8'))
    finally:
        cursor.close()


def downgrade():
    raise NotImplementedError('The baseline revision cannot be downgraded')
