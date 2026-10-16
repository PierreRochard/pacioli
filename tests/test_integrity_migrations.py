"""Real PostgreSQL regressions for OFX source identity and database money checks."""
from datetime import datetime, date, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pacioli.extensions import db
from pacioli.models import JournalEntries, BankFeedAccounts
from pacioli.functions.source_identity import source_identity
from pacioli.functions.journal_posting import post_pair


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
