"""Lookup stable source keys; never construct an identifier by concatenation."""
from sqlalchemy import text


def source_identity(session, account_id, fitid):
    row = session.execute(text('''SELECT public_id FROM bookkeeping.ofx_source_identities
        WHERE acctfrom_id=:account AND fitid=:fitid'''),
        {'account': account_id, 'fitid': fitid}).one()
    return row[0]
