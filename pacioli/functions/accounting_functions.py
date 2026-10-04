"""Shared expressions for the UTC reporting periods used by the ledger."""

from pacioli.extensions import db


def journal_period(timestamp_column, interval):
    return db.func.to_char(db.func.timezone('UTC', timestamp_column), interval)
