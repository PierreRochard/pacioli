"""Real PostgreSQL regressions for identity migration and database money checks."""
from datetime import datetime, date, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pacioli.extensions import db, run_sql
from pacioli.models import JournalEntries, BankFeedAccounts, JournalEntryEvents
from pacioli.database.source_identity import install_source_identities
from pacioli.database.accounting_migration import install_journal_constraints
from pacioli.functions.source_identity import source_identity
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.period_close import journal_fingerprint


WHEN = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)


def bank(identifier=None):
    if identifier is None:
        identifier = db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) "
            "VALUES('bankacctfrom','Chase Checking') RETURNING id")).scalar()
    else:
        db.session.execute(text("INSERT INTO ofx.acctfrom(id,subclass,name) "
            "VALUES(:id,'bankacctfrom','Chase Checking')"), {'id': identifier})
    db.session.add(BankFeedAccounts(ofx_account_id=identifier, currency='USD'))
    db.session.flush()
    return identifier


def source(account, fitid):
    db.session.execute(text('''INSERT INTO ofx.stmttrn(acctfrom_id,fitid,dtposted,trntype,trnamt,name)
        VALUES(:account,:fitid,'2025-01-02 12:00:00','CREDIT',100,'Fixture')'''),
        {'account': account, 'fitid': fitid})


def journal(**changes):
    values = dict(timestamp=WHEN, debit_subaccount='Chase Checking', credit_subaccount='Salary',
        functional_amount=Decimal('80'), functional_currency='USD', source_amount=Decimal('100'),
        source_currency='EUR', conversion_rate=Decimal('.8'),
        conversion_source='fixture', conversion_date=date(2025,1,2))
    values.update(changes)
    return JournalEntries(**values)


def test_distinct_sources_with_colliding_legacy_keys_remain_distinct(clean_ledger):
    from pacioli.models import Transactions
    small = bank()
    large = bank(int('9' + str(small)))
    source(small, 'collision9')
    source(large, 'collision')
    first = source_identity(db.session, small, 'collision9')
    second = source_identity(db.session, large, 'collision')
    assert first != second
    assert db.session.query(Transactions).filter(Transactions.id.in_([first, second])).count() == 2
    entry = post_pair(timestamp=WHEN, debit='Chase Checking', credit='Salary', functional_amount='100',
                      transaction_source='ofx', transaction_id=first)
    db.session.commit()
    rows = {r.id: r.journal_entry_id for r in db.session.query(Transactions)
            .filter(Transactions.id.in_([first, second]))}
    assert rows == {first: entry.id, second: None}
    db.session.remove()
    install_source_identities()
    assert source_identity(db.session, small, 'collision9') == first
    assert source_identity(db.session, large, 'collision') == second


def test_legacy_migration_preserves_journals_events_and_fingerprint(clean_ledger):
    account = bank()
    fitid = uuid4().hex
    legacy = fitid + str(account)
    db.session.commit()
    run_sql('ALTER TABLE ofx.stmttrn DISABLE TRIGGER register_ofx_source')
    run_sql('ALTER TABLE bookkeeping.journal_entries DISABLE TRIGGER journal_source_link')
    try:
        source(account, fitid)
        entry = journal(source_currency='USD', source_amount=Decimal('80'), conversion_rate=None,
                        conversion_source=None, conversion_date=None,
                        transaction_source='ofx', transaction_id=legacy)
        db.session.add(entry)
        db.session.commit()
        before = journal_fingerprint(db.session, datetime(2026,1,1,tzinfo=timezone.utc))
        events = db.session.query(JournalEntryEvents).count()
        db.session.remove()
        install_source_identities()
        install_source_identities()
        assert source_identity(db.session, account, fitid) == legacy
        assert journal_fingerprint(db.session, datetime(2026,1,1,tzinfo=timezone.utc)) == before
        assert db.session.query(JournalEntryEvents).count() == events
        assert db.session.execute(text('SELECT count(*) FROM bookkeeping.ofx_journal_sources')).scalar() == 1
    finally:
        db.session.rollback()
        run_sql('ALTER TABLE ofx.stmttrn ENABLE TRIGGER register_ofx_source')
        run_sql('ALTER TABLE bookkeeping.journal_entries ENABLE TRIGGER journal_source_link')


def test_ambiguous_legacy_migration_rolls_back_without_guessing(clean_ledger):
    small = bank()
    large = bank(int('8' + str(small)))
    db.session.commit()
    run_sql('ALTER TABLE ofx.stmttrn DISABLE TRIGGER register_ofx_source')
    try:
        source(small, 'collision8')
        source(large, 'collision')
        db.session.commit()
        with pytest.raises(ValueError, match='Ambiguous legacy'):
            install_source_identities()
        assert db.session.execute(text('SELECT count(*) FROM bookkeeping.ofx_source_identities')).scalar() == 0
        assert db.session.execute(text('SELECT count(*) FROM ofx.stmttrn')).scalar() == 2
    finally:
        db.session.rollback()
        run_sql('ALTER TABLE ofx.stmttrn ENABLE TRIGGER register_ofx_source')


def test_raw_ofx_journal_requires_real_source_reference(clean_ledger):
    with pytest.raises(IntegrityError):
        db.session.add(journal(transaction_source='ofx', transaction_id='not-a-source'))
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize('field,value', [
    ('conversion_rate', None), ('conversion_source', None), ('conversion_source', '  '),
    ('conversion_date', None), ('conversion_rate', Decimal('Infinity')),
    ('conversion_rate', Decimal('NaN')), ('source_amount', Decimal('Infinity')),
    ('functional_amount', Decimal('Infinity')), ('functional_amount', Decimal('NaN')),
])
def test_database_rejects_missing_or_nonfinite_fx_evidence(clean_ledger, field, value):
    with pytest.raises(IntegrityError):
        db.session.add(journal(**{field: value}))
        db.session.commit()
    db.session.rollback()
    assert db.session.query(JournalEntries).count() == 0


def test_constraint_upgrade_replaces_existing_named_definition(clean_ledger):
    run_sql('ALTER TABLE bookkeeping.journal_entries DROP CONSTRAINT journal_conversion_evidence')
    run_sql('ALTER TABLE bookkeeping.journal_entries ADD CONSTRAINT journal_conversion_evidence CHECK (conversion_rate > 0)')
    install_journal_constraints()
    with pytest.raises(IntegrityError):
        db.session.add(journal(conversion_rate=None))
        db.session.commit()
    db.session.rollback()


def test_constraint_upgrade_preserves_invalid_history_and_rolls_back(clean_ledger):
    run_sql('ALTER TABLE bookkeeping.journal_entries DROP CONSTRAINT journal_conversion_evidence')
    run_sql('ALTER TABLE bookkeeping.journal_entries ADD CONSTRAINT journal_conversion_evidence CHECK (conversion_rate > 0)')
    row = journal(conversion_rate=None)
    db.session.add(row)
    db.session.commit()
    identifier = row.id
    db.session.remove()
    try:
        with pytest.raises(ValueError, match='journal_conversion_evidence'):
            install_journal_constraints()
        assert db.session.get(JournalEntries, identifier).conversion_rate is None
        definition = db.session.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='bookkeeping.journal_entries'::regclass AND conname='journal_conversion_evidence'")).scalar()
        assert 'IS TRUE' not in definition
    finally:
        db.session.query(JournalEntries).filter_by(id=identifier).delete()
        db.session.commit()
        install_journal_constraints()
