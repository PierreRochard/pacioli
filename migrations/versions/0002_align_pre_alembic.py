"""Align a database built before Alembic with the baseline.

Three objects in the live database differed from what the installers build
today: a unique index on admin.users that the baseline creates as a constraint,
OFX primary keys with their columns in the opposite order, and an OFX view
whose columns came out in a different order. Each step checks first, so on a
database built from revision 0001 this revision changes nothing.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from alembic import op


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

SOURCE_IDENTITY_FK = (
    'ALTER TABLE bookkeeping.ofx_source_identities ADD CONSTRAINT '
    'ofx_source_identities_acctfrom_id_fitid_fkey FOREIGN KEY (acctfrom_id, fitid) '
    'REFERENCES ofx.stmttrn(acctfrom_id, fitid) ON DELETE CASCADE')
# Copied from 0001_baseline.sql; cost_bases depends on investment_transactions.
VIEWS = """
CREATE VIEW ofx.investment_transactions AS
 SELECT invtran.acctfrom_id,
    invtran.fitid,
    invtran.srvrtid,
    invtran.dttrade,
    invtran.dtsettle,
    invtran.reversalfitid,
    invtran.memo,
    invtran.subclass,
    invtran.id,
    acctfrom.name AS account_name,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf_secinfo.secname
            WHEN 'sellmf'::text THEN sellmf_secinfo.secname
            WHEN 'reinvest'::text THEN reinvest_secinfo.secname
            ELSE NULL::text
        END AS secname,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf_secinfo.ticker
            WHEN 'sellmf'::text THEN sellmf_secinfo.ticker
            WHEN 'reinvest'::text THEN reinvest_secinfo.ticker
            ELSE NULL::text
        END AS ticker,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf.units
            WHEN 'sellmf'::text THEN sellmf.units
            WHEN 'reinvest'::text THEN reinvest.units
            ELSE NULL::numeric
        END AS units,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN buymf.unitprice
            WHEN 'sellmf'::text THEN sellmf.unitprice
            WHEN 'reinvest'::text THEN reinvest.unitprice
            ELSE NULL::numeric
        END AS unitprice,
        CASE invtran.subclass
            WHEN 'buymf'::text THEN (buymf.total * ('-1'::integer)::numeric)
            WHEN 'sellmf'::text THEN (sellmf.total * ('-1'::integer)::numeric)
            WHEN 'reinvest'::text THEN (reinvest.total * ('-1'::integer)::numeric)
            ELSE NULL::numeric
        END AS total
   FROM (((((((ofx.invtran
     LEFT JOIN ofx.buymf ON (((buymf.id = invtran.id) AND ((invtran.subclass)::text = 'buymf'::text))))
     LEFT JOIN ofx.sellmf ON (((sellmf.id = invtran.id) AND ((invtran.subclass)::text = 'sellmf'::text))))
     LEFT JOIN ofx.reinvest ON (((reinvest.id = invtran.id) AND ((invtran.subclass)::text = 'reinvest'::text))))
     LEFT JOIN ofx.secinfo buymf_secinfo ON ((buymf_secinfo.id = buymf.secinfo_id)))
     LEFT JOIN ofx.secinfo sellmf_secinfo ON ((sellmf_secinfo.id = sellmf.secinfo_id)))
     LEFT JOIN ofx.secinfo reinvest_secinfo ON ((reinvest_secinfo.id = reinvest.secinfo_id)))
     JOIN ofx.acctfrom ON ((acctfrom.id = invtran.acctfrom_id)))
  ORDER BY invtran.dttrade DESC;
CREATE VIEW ofx.cost_bases AS
 SELECT investment_transactions.secname,
    sum(investment_transactions.units) AS total_units,
    sum(investment_transactions.total) AS cost_basis,
    q1.ticker,
    q1.adjusted_close AS close,
    (q1.adjusted_close * sum(investment_transactions.units)) AS market_value,
    ((q1.adjusted_close * sum(investment_transactions.units)) - sum(investment_transactions.total)) AS pnl,
    (((q1.adjusted_close * sum(investment_transactions.units)) - sum(investment_transactions.total)) / sum(investment_transactions.total)) AS pnl_percent,
    q2.date AS price_date
   FROM ((ofx.investment_transactions
     JOIN ( SELECT security_prices.ticker,
            max(security_prices.date) AS date
           FROM investments.security_prices
          GROUP BY security_prices.ticker) q2 ON (((q2.ticker)::text = investment_transactions.ticker)))
     JOIN investments.security_prices q1 ON ((((q1.ticker)::text = investment_transactions.ticker) AND (q2.date = q1.date))))
  GROUP BY investment_transactions.secname, q1.ticker, q2.date, q1.adjusted_close
  ORDER BY (sum(investment_transactions.total));
"""


def align(connection):
    def scalar(sql):
        return connection.exec_driver_sql(sql).scalar()

    if not scalar("SELECT 1 FROM pg_constraint WHERE conrelid = 'admin.users'::regclass "
                  "AND conname = 'users_fs_uniquifier_key'"):
        connection.exec_driver_sql(
            'ALTER TABLE admin.users ADD CONSTRAINT users_fs_uniquifier_key '
            'UNIQUE USING INDEX users_fs_uniquifier_key')
    old_keys = [table for table in ('stmttrn', 'invbanktran') if scalar(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
        "'ofx.{0}'::regclass AND conname = '{0}_pkey'".format(table)
    ) == 'PRIMARY KEY (fitid, acctfrom_id)']
    if old_keys:
        connection.exec_driver_sql('ALTER TABLE bookkeeping.ofx_source_identities DROP CONSTRAINT '
                                   'ofx_source_identities_acctfrom_id_fitid_fkey')
        for table in old_keys:
            connection.exec_driver_sql(
                'ALTER TABLE ofx.{0} DROP CONSTRAINT {0}_pkey, '
                'ADD CONSTRAINT {0}_pkey PRIMARY KEY (acctfrom_id, fitid)'.format(table))
        connection.exec_driver_sql(SOURCE_IDENTITY_FK)
    if scalar("SELECT attname FROM pg_attribute WHERE attrelid = "
              "'ofx.investment_transactions'::regclass AND attnum = 1") != 'acctfrom_id':
        connection.exec_driver_sql('DROP VIEW ofx.cost_bases')
        connection.exec_driver_sql('DROP VIEW ofx.investment_transactions')
        connection.exec_driver_sql(VIEWS)


def upgrade():
    align(op.get_bind())


def downgrade():
    pass  # The old layout was accidental; nothing depends on it.
