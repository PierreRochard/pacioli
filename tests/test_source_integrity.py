"""Source completeness, exact bank legs, and cutoff-scoped close controls."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.accounting_report import (
    audit_ledger, journal_money_differences, source_differences,
)
from pacioli.functions.journal_posting import post_allocations, post_pair, reverse_entry
from pacioli.functions.period_close import (
    close_month, closed_period_differences, journal_fingerprint, statement_snapshot,
)
from pacioli.models import AmazonItems, BankFeedAccounts, ClosedPeriods, JournalEntries
from tests.test_chase_qfx import isolate_imported_accounts  # noqa: F401


JAN = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)
FEB = datetime(2025, 2, 1, tzinfo=timezone.utc)


@pytest.fixture
def imported_source(clean_ledger, clean_amazon_orders):
    aid = db.session.execute(text("""INSERT INTO ofx.acctfrom(subclass,name)
        VALUES('bankacctfrom','Chase Checking') RETURNING id""")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=aid, currency='USD'))
    db.session.flush()

    def add(amount='-100', fitid='purchase', when=JAN, description='Source integrity fixture'):
        db.session.execute(text("""INSERT INTO ofx.stmttrn
            (acctfrom_id,fitid,dtposted,trntype,trnamt,name)
            VALUES(:aid,:fitid,:when,'OTHER',:amount,:description)"""),
            dict(aid=aid, fitid=fitid, when=when.replace(tzinfo=None), amount=Decimal(amount), description=description))
        from pacioli.functions.source_identity import source_identity
        return source_identity(db.session, aid, fitid)
    add.account_id = aid
    return add


def raw_entry(identifier, **changes):
    values = dict(timestamp=JAN, debit_subaccount='Groceries', credit_subaccount='Chase Checking',
        functional_amount=Decimal('100'), functional_currency='USD', source_amount=Decimal('100'),
        source_currency='USD', transaction_source='ofx', transaction_id=identifier)
    values.update(changes)
    row = JournalEntries(**values)
    db.session.add(row)
    db.session.flush()
    return row


def test_offsetting_unposted_sources_block_audit_and_close(imported_source):
    ids = {imported_source(), imported_source('100', 'deposit')}
    db.session.commit()
    problems = audit_ledger(db.session)
    assert {i['transaction_id'] for i in problems if i['kind'] == 'source_amount'} == ids
    assert all(i['booked'] == 0 for i in problems if i['kind'] == 'source_amount')
    with pytest.raises(ValueError, match='Accounting check'):
        close_month(db.session, '2025-01')
    assert db.session.query(ClosedPeriods).count() == 0


@pytest.mark.parametrize('changes, expected', [
    ({'debit_subaccount': 'Chase Checking', 'credit_subaccount': 'Groceries'}, 'bank_account_or_direction'),
    ({'credit_subaccount': 'Chase Savings'}, 'bank_account_or_direction'),
    ({'timestamp': JAN + timedelta(days=1)}, 'posting_date'),
    ({'timestamp': FEB + timedelta(days=1)}, 'posting_date'),
])
def test_independent_audit_detects_direct_writer_source_contract_violations(
        imported_source, changes, expected):
    identifier = imported_source()
    row = raw_entry(identifier, **changes)
    db.session.commit()
    differences = source_differences(db.session)
    assert any(i.get('journal_id') == row.id and expected in i['problems'] for i in differences)
    assert any(i.get('journal_id') == row.id and expected in i.get('problems', [])
               for i in audit_ledger(db.session, end=FEB))


@pytest.mark.parametrize('changes, message', [
    ({'debit': 'Chase Checking', 'credit': 'Groceries'}, 'signed movement'),
    ({'credit': 'Chase Savings'}, 'signed movement'),
    ({'timestamp': JAN + timedelta(seconds=1)}, 'exact OFX'),
    ({'functional_amount': '101'}, 'exceed'),
    ({'functional_amount': '80', 'source_amount': '100', 'source_currency': 'EUR',
      'conversion_rate': '.8', 'conversion_source': 'fixture', 'conversion_date': date(2025, 1, 2)}, 'relabeled'),
])
def test_posting_service_rejects_source_contract_violations(imported_source, changes, message):
    kwargs = dict(timestamp=JAN, debit='Groceries', credit='Chase Checking', functional_amount='100',
        transaction_source='ofx', transaction_id=imported_source())
    kwargs.update(changes)
    with pytest.raises(ValueError, match=message):
        post_pair(**kwargs)
    assert db.session.query(JournalEntries).count() == 0


@pytest.mark.parametrize('amount', ['60', '120'])
def test_partial_and_excessive_raw_allocations_are_visible(imported_source, amount):
    identifier = imported_source()
    raw_entry(identifier, functional_amount=Decimal(amount), source_amount=Decimal(amount))
    db.session.commit()
    assert source_differences(db.session) == [dict(source='ofx', transaction_id=identifier,
        booked=Decimal(amount), source_amount=Decimal('100'))]


def test_full_split_replay_and_timezone_preserve_source(imported_source):
    identifier = imported_source()
    kwargs = dict(timestamp=JAN.astimezone(timezone(timedelta(hours=-7))), source='ofx',
        transaction_id=identifier, fixed_account='Chase Checking', total='100',
        allocations=[('food', 'Groceries', '60'), ('books', 'Books and Publications', '40')])
    posted = post_allocations(**kwargs)
    db.session.commit()
    assert len(posted) == 2 and not source_differences(db.session)
    assert {e.id for e in post_allocations(**kwargs)} == {e.id for e in posted}
    with pytest.raises(ValueError, match='different allocation'):
        post_allocations(**dict(kwargs, timestamp=JAN + timedelta(days=1)))
    assert audit_ledger(db.session) == []


def test_future_suspense_and_bank_balance_do_not_block_prior_month(imported_source):
    identifier = imported_source()
    post_pair(timestamp=JAN, debit='Groceries', credit='Chase Checking', functional_amount='100',
        transaction_source='ofx', transaction_id=identifier)
    future = imported_source('-20', 'future', FEB + timedelta(days=3))
    post_pair(timestamp=FEB + timedelta(days=3), debit='Bookkeeping Suspense',
        credit='Chase Checking', functional_amount='20', transaction_source='ofx', transaction_id=future)
    imported_source('-35', 'future-unposted', FEB + timedelta(days=5))
    db.session.execute(text("""INSERT INTO ofx.ledgerbal(acctfrom_id,dtasof,balamt)
        VALUES(:aid,'2025-02-20',999)"""), {'aid': imported_source.account_id})
    db.session.commit()
    assert {'classification_pending', 'bank_balance'} <= {i['kind'] for i in audit_ledger(db.session)}
    assert audit_ledger(db.session, end=FEB) == []
    closed = close_month(db.session, '2025-01')
    db.session.commit()
    assert closed.journal_count == 1 and closed.statement_snapshot['source_count'] == 1
    assert not closed_period_differences(db.session)


def test_late_backdated_source_invalidates_closed_source_evidence(imported_source):
    closed = close_month(db.session, '2025-01')
    db.session.commit()
    prior = closed.statement_snapshot['source_digest']
    imported_source()
    db.session.commit()
    differences = closed_period_differences(db.session)
    assert differences[0]['period'] == '2025-01'
    assert differences[0]['digest_matches']  # No journal was changed.
    assert differences[0]['source_coverage_recorded'] and not differences[0]['source_matches']
    assert closed.statement_snapshot['source_digest'] == prior


def test_legacy_close_is_reported_as_unverified_without_rewriting_it(imported_source):
    snapshot = statement_snapshot(db.session, '2025-01')
    for key in ('source_contract_version', 'source_count', 'source_digest'):
        snapshot.pop(key)
    count, digest = journal_fingerprint(db.session, FEB)
    legacy = ClosedPeriods(period='2025-01', journal_count=count, journal_digest=digest,
                           statement_snapshot=snapshot)
    db.session.add(legacy)
    db.session.commit()
    differences = closed_period_differences(db.session)
    assert not differences[0]['source_coverage_recorded']
    assert differences[0]['statement_matches']
    assert legacy.statement_snapshot == snapshot


def test_imported_source_reversal_requires_reviewed_amendment(imported_source):
    identifier = imported_source()
    original = post_pair(timestamp=JAN, debit='Groceries', credit='Chase Checking', functional_amount='100',
        transaction_source='ofx', transaction_id=identifier)
    db.session.commit()
    reverse_entry(original.id, timestamp=FEB, reason='Explicit compensating correction')
    db.session.commit()
    assert not source_differences(db.session)  # Original allocation remains intact.
    assert any(i['kind'] == 'source_correction' and i['original_journal_id'] == original.id
               for i in audit_ledger(db.session))
    assert audit_ledger(db.session, end=FEB) == []


def test_amazon_sources_require_booking_and_preserve_shipment_day(imported_source):
    item = AmazonItems(item_total=Decimal('10'), currency='USD', title='Legacy receipt',
                       shipment_date=date(2025, 1, 2))
    db.session.add(item)
    db.session.flush()
    assert any(i['transaction_id'] == str(item.id) and i['booked'] == 0
               for i in source_differences(db.session))
    with pytest.raises(ValueError, match='shipment date'):
        post_pair(timestamp=JAN + timedelta(days=1), debit='Books and Publications',
            credit='Amazon Payable', functional_amount='10', transaction_source='amazon', transaction_id=str(item.id))
    post_pair(timestamp=JAN, debit='Books and Publications', credit='Amazon Payable', functional_amount='10',
        transaction_source='amazon', transaction_id=str(item.id))
    db.session.commit()
    assert not source_differences(db.session)


@pytest.mark.parametrize('changes', [
    {'conversion_rate': None}, {'conversion_source': None}, {'conversion_source': ' '},
    {'conversion_date': None}, {'conversion_rate': Decimal('Infinity')},
    {'conversion_rate': Decimal('NaN')}, {'conversion_rate': Decimal('.9')},
])
def test_independent_money_audit_does_not_trust_conversion_constraints(changes):
    # An old/externally written row can be inspected without weakening the live
    # CHECK constraints merely to seed a corrupt fixture.
    row = JournalEntries(id=42, timestamp=JAN, debit_subaccount='Rent', credit_subaccount='Accounts Payable',
        functional_amount=Decimal('80'), functional_currency='USD', source_amount=Decimal('100'),
        source_currency='EUR', conversion_rate=Decimal('.8'), conversion_source='quote', conversion_date=JAN.date())
    for key, value in changes.items():
        setattr(row, key, value)
    class RecordedRows:
        def query(self, model):
            return [row]
    assert journal_money_differences(RecordedRows()) == [dict(journal_id=42, problems=['foreign_conversion_evidence'])]


def test_deleted_registry_row_cannot_hide_unposted_raw_source(imported_source):
    identifier = imported_source()
    db.session.execute(text('DELETE FROM bookkeeping.ofx_source_identities WHERE public_id=:id'),
                       {'id': identifier})
    db.session.commit()
    assert db.session.execute(text('SELECT count(*) FROM ofx.transactions')).scalar() == 0
    differences = audit_ledger(db.session)
    assert any(i.get('problem') == 'missing_source_identity' for i in differences)
    with pytest.raises(ValueError, match='Accounting check'):
        close_month(db.session, '2025-01')


def test_deleted_journal_association_is_detected_independently(imported_source):
    identifier = imported_source()
    entry = post_pair(timestamp=JAN, debit='Groceries', credit='Chase Checking', functional_amount='100',
        transaction_source='ofx', transaction_id=identifier)
    db.session.commit()
    db.session.execute(text('DELETE FROM bookkeeping.ofx_journal_sources WHERE journal_entry_id=:id'),
                       {'id': entry.id})
    db.session.commit()
    assert any(i['kind'] == 'source_identity' and i['journal_id'] == entry.id
               for i in audit_ledger(db.session))


@pytest.mark.parametrize('tamper', ['digest', 'receipt', 'bank_amount', 'category'])
def test_amazon_support_exemption_requires_live_evidence_and_bank_allocation(imported_source, tamper):
    from pacioli.functions import amazon_evidence
    from pacioli.models import AmazonOrders
    identifier = imported_source(description='AMAZON RETAIL')
    post_pair(timestamp=JAN, debit='Bookkeeping Suspense', credit='Chase Checking', functional_amount='100',
        transaction_source='ofx', transaction_id=identifier)
    db.session.flush()
    raw = (b'bundle_id,line_id,order_id,date,kind,title,amount,currency\n'
           b'integrity-bundle,line-1,integrity-order,2025-01-02,item,Book,100,USD\n')
    bundle = amazon_evidence.import_items_csv(raw, 'fixture reviewer')[0]
    preview = amazon_evidence.allocation_preview(bundle.id, identifier,
        [dict(category='Books and Publications', amount='100')])
    amazon_evidence.book_allocation(preview, 'fixture reviewer', 'Receipt verified')
    db.session.add(AmazonOrders(id='integrity-order', order_date=date(2025, 1, 1)))
    db.session.flush()
    item = AmazonItems(order_id='integrity-order', item_total=Decimal('100'), currency='USD',
        title='Legacy copy of the receipt', shipment_date=date(2025, 1, 2))
    db.session.add(item)
    db.session.commit()
    assert audit_ledger(db.session) == []
    if tamper == 'digest':
        bundle.posting_digest = '0' * 64
    elif tamper == 'receipt':
        body = json.loads(bundle.details)
        body['lines'][0]['title'] = 'Changed evidence'
        bundle.details = json.dumps(body)
    else:
        entry = db.session.query(JournalEntries).filter_by(transaction_source='ofx', transaction_id=identifier).one()
        if tamper == 'bank_amount':
            entry.functional_amount = entry.source_amount = Decimal('90')
        else:
            entry.debit_subaccount = 'Groceries'
    db.session.commit()
    issues = audit_ledger(db.session)
    assert any(i['kind'] == 'amazon_support_evidence' and i['bundle_id'] == bundle.id for i in issues)
    assert any(i.get('source') == 'amazon' and i.get('transaction_id') == str(item.id)
               and i.get('problem') == 'unposted_source' for i in issues)


def test_source_registration_cannot_post_to_a_misclassified_bank_account(imported_source):
    identifier = imported_source()
    db.session.execute(text("UPDATE ofx.acctfrom SET name='Salary' WHERE id=:id"),
                       {'id': imported_source.account_id})
    db.session.commit()
    with pytest.raises(ValueError, match='balance-sheet account'):
        post_pair(timestamp=JAN, debit='Groceries', credit='Salary', functional_amount='100',
            transaction_source='ofx', transaction_id=identifier)
    assert db.session.query(JournalEntries).count() == 0
    assert any(i.get('problem') == 'invalid_source_account_or_currency' for i in audit_ledger(db.session))
