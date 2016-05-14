from pacioli import db
from sqlalchemy.exc import ProgrammingError


def create_bookkeeping_views():
    try:
        db.engine.execute('DROP VIEW bookkeeping.detailed_journal_entries;')
    except ProgrammingError:
        pass
    db.engine.execute("""
    CREATE VIEW bookkeeping.detailed_journal_entries AS SELECT
            bookkeeping.journal_entries.id AS id,
            bookkeeping.journal_entries.transaction_source AS transaction_source,
            bookkeeping.journal_entries.transaction_id AS transaction_id,
            bookkeeping.journal_entries."timestamp" AS "timestamp",
            bookkeeping.journal_entries.debit_subaccount as debit_subaccount,
            bookkeeping.journal_entries.credit_subaccount as credit_subaccount,
            bookkeeping.journal_entries.functional_amount as functional_amount,
            CASE bookkeeping.journal_entries.transaction_source
              WHEN 'ofx' THEN concat(ofx.stmttrn.name, ofx.stmttrn.memo)
              WHEN 'amazon' THEN amazon.items.title
            END AS description
        FROM bookkeeping.journal_entries
        LEFT OUTER JOIN ofx.stmttrn ON concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id) = bookkeeping.journal_entries.transaction_id AND bookkeeping.journal_entries.transaction_source = 'ofx'
        LEFT OUTER JOIN amazon.items ON cast(amazon.items.id AS CHARACTER VARYING) = bookkeeping.journal_entries.transaction_id AND bookkeeping.journal_entries.transaction_source = 'amazon'
        LEFT OUTER JOIN ofx.acctfrom ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
        ORDER BY bookkeeping.journal_entries."timestamp" DESC;
    """)