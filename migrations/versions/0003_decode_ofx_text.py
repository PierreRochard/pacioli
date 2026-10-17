"""Decode character references in imported OFX text.

ofxtools stored element text without decoding character references, so a
payee written "A &amp; B" in the QFX was stored with "&amp;". Pacioli's OFX
reader decodes them, and an import compares each FITID's stored text with the
source, so the stored text has to match. This revision decodes transaction
names and memos, and the mapping keywords copied from them, by the reader's
rule: the five named references and numeric references, each ending in ';'.
Any other '&' is literal and stays. The original values are kept in
ofx.text_before_0003 so that the downgrade restores them exactly.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

import json
import re

from alembic import op
from sqlalchemy import text


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

REFERENCE = re.compile(r'&(amp|lt|gt|quot|apos|#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6});')
NAMED = {'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"', 'apos': "'"}

# (table, key columns, text column)
COLUMNS = (('ofx.stmttrn', ('acctfrom_id', 'fitid'), 'name'),
           ('ofx.stmttrn', ('acctfrom_id', 'fitid'), 'memo'),
           ('admin.mappings', ('id',), 'keyword'))


def decode(value):
    def character(match):
        name = match.group(1)
        if name in NAMED:
            return NAMED[name]
        code = int(name[2:], 16) if name[1] in 'xX' else int(name[1:])
        if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF or code < 0x20 or 0x7F <= code <= 0x9F:
            raise ValueError('A stored character reference is not a printable character')
        return chr(code)
    return REFERENCE.sub(character, value)


def upgrade():
    connection = op.get_bind()
    connection.execute(text("""
        CREATE TABLE ofx.text_before_0003 (
            table_name text NOT NULL,
            row_key jsonb NOT NULL,
            column_name text NOT NULL,
            original text NOT NULL,
            PRIMARY KEY (table_name, row_key, column_name))
    """))
    for table, keys, column in COLUMNS:
        rows = connection.execute(text('SELECT {}, {} FROM {} WHERE {} LIKE :pattern'.format(
            ', '.join(keys), column, table, column)), {'pattern': '%&%;%'}).all()
        for row in rows:
            key, original = dict(zip(keys, row[:-1])), row[-1]
            decoded = decode(original)
            if decoded == original:
                continue
            connection.execute(text(
                'INSERT INTO ofx.text_before_0003 VALUES (:table, CAST(:key AS jsonb), :column, :original)'),
                {'table': table, 'key': json.dumps(key, sort_keys=True), 'column': column,
                 'original': original})
            connection.execute(text('UPDATE {} SET {} = :value WHERE {}'.format(
                table, column, ' AND '.join('{0} = :{0}'.format(k) for k in keys))),
                dict(key, value=decoded))


def downgrade():
    connection = op.get_bind()
    for table, keys, column in COLUMNS:
        rows = connection.execute(text(
            'SELECT row_key, original FROM ofx.text_before_0003 '
            'WHERE table_name = :table AND column_name = :column'),
            {'table': table, 'column': column}).all()
        for key, original in rows:
            connection.execute(text('UPDATE {} SET {} = :value WHERE {}'.format(
                table, column, ' AND '.join('{0} = :{0}'.format(k) for k in keys))),
                dict(key, value=original))
    connection.execute(text('DROP TABLE ofx.text_before_0003'))
