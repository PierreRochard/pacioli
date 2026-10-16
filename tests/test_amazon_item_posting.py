"""Explicit partial Amazon allocations and versioned residual review revisions."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.models import (AccountingEvidence, AmazonEvidenceBundles, BankFeedAccounts,
                           ClosedPeriods, JournalEntries, JournalEntryEvents)
from pacioli.functions import amazon_evidence as posting
from pacioli.functions.accounting_report import (amazon_support_evidence, source_differences,
                                                trial_balance_differences)
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.reconciliation import digest, dump
from pacioli.functions.source_identity import source_identity


@pytest.fixture
def item_settlement(clean_ledger):
    def make(refund=False):
        account_id = db.session.execute(text("""
            INSERT INTO ofx.acctfrom(subclass,name)
            VALUES('bankacctfrom','Chase Checking') RETURNING id
        """)).scalar()
        db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
        when = datetime(2026, 1, 2, 12, 34, 56, tzinfo=timezone.utc)
        fitid = uuid4().hex
        signed = Decimal('21.65') if refund else Decimal('-21.65')
        db.session.execute(text('''INSERT INTO ofx.stmttrn
            (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
            VALUES(:fitid,:aid,'OTHER',:when,:amount,'AMAZON RETAIL')'''),
            dict(fitid=fitid, aid=account_id, when=when, amount=signed))
        transaction_id = source_identity(db.session, account_id, fitid)
        original = post_pair(timestamp=when,
            debit='Chase Checking' if refund else 'Bookkeeping Suspense',
            credit='Bookkeeping Suspense' if refund else 'Chase Checking',
            functional_amount='21.65', transaction_source='ofx', transaction_id=transaction_id)
        key = uuid4().hex
        header = 'bundle_id,line_id,order_id,date,kind,title,amount,currency\n'
        if refund:
            rows = key + ',refund,order-1,2026-01-02,refund,Returned goods,-21.65,USD\n'
        else:
            rows = (key + ',book,order-1,2026-01-02,item,Reviewed book,12.00,USD\n' +
                    key + ',unknown,order-1,2026-01-02,item,Unresolved purpose,8.00,USD\n' +
                    key + ',tax,order-1,2026-01-02,tax,Tax,1.65,USD\n')
        bundle = posting.import_items_csv((header + rows).encode(), 'fixture reviewer')[0]
        db.session.commit()
        return dict(bundle_id=bundle.id, transaction_id=transaction_id, account_id=account_id,
                    fitid=fitid, date=when, original_id=original.id, amount=signed)
    return make


def allocation(source, *, books='12.00', remainder='9.65', suspense=True):
    rows = [dict(category='Books and Publications', amount=books)]
    if Decimal(remainder):
        rows.append(dict(category='Bookkeeping Suspense' if suspense else 'Groceries', amount=remainder))
    return posting.allocation_preview(source['bundle_id'], source['transaction_id'], rows,
                                      allow_suspense=suspense)


def snapshots():
    return dict(journals=[dict(row._mapping) for row in db.session.execute(text(
        'SELECT * FROM bookkeeping.journal_entries ORDER BY id'))],
        events=db.session.query(JournalEntryEvents).count(),
        evidence=db.session.query(AccountingEvidence).count(),
        bundles=[dict(row._mapping) for row in db.session.execute(text('SELECT * FROM amazon.evidence_bundles ORDER BY id'))])


def assert_preserves_source(source):
    entries = db.session.query(JournalEntries).filter_by(
        transaction_source='ofx', transaction_id=source['transaction_id']).all()
    assert sum(entry.functional_amount for entry in entries) == abs(source['amount'])
    for entry in entries:
        assert entry.source_amount == entry.functional_amount
        assert entry.source_currency == entry.functional_currency == 'USD'
        assert entry.timestamp == source['date']
        assert (entry.credit_subaccount if source['amount'] < 0 else entry.debit_subaccount) == 'Chase Checking'
    assert not source_differences(db.session)
    assert not trial_balance_differences(db.session)


def test_partial_allocation_is_opt_in_and_cannot_be_entirely_suspense(item_settlement):
    source = item_settlement()
    categories = [dict(category='Books and Publications', amount='12.00'),
                  dict(category='Bookkeeping Suspense', amount='9.65')]
    with pytest.raises(ValueError, match='reviewed'):
        posting.allocation_preview(source['bundle_id'], source['transaction_id'], categories)
    with pytest.raises(ValueError, match='at least one reviewed'):
        posting.allocation_preview(source['bundle_id'], source['transaction_id'],
            [dict(category='Bookkeeping Suspense', amount='21.65')], allow_suspense=True)
    with pytest.raises(ValueError, match='reviewed'):
        posting.allocation_preview(source['bundle_id'], source['transaction_id'],
            [categories[0], dict(category='Transfer Clearing', amount='9.65')], allow_suspense=True)
    with pytest.raises(ValueError, match='boolean'):
        posting.allocation_preview(source['bundle_id'], source['transaction_id'], categories, allow_suspense='true')
    before = snapshots()
    preview = allocation(source)
    assert preview['allow_suspense'] is True
    full = allocation(source, books='21.65', remainder='0', suspense=False)
    assert 'allow_suspense' not in full
    assert snapshots() == before


@pytest.mark.parametrize('refund', [False, True])
def test_partial_posting_and_replay_preserve_exact_source_and_audited_before_image(item_settlement, refund):
    source = item_settlement(refund)
    preview = allocation(source)
    before = snapshots()
    bundle = posting.book_allocation(preview, 'owner', 'Verified book; unidentified residual retained for review')
    db.session.commit()
    assert bundle.posting_digest == preview['digest']
    assert bundle.transaction_id == source['transaction_id']
    assert db.session.query(JournalEntries).count() == 2
    assert db.session.query(JournalEntryEvents).count() == before['events'] + 3
    assert db.session.query(JournalEntryEvents).filter_by(
        journal_entry_id=source['original_id'], action='DELETE').one().before_row
    proof = db.session.query(AccountingEvidence).filter_by(kind='amazon_allocation').one()
    assert json.loads(proof.payload)['preview'] == preview
    assert_preserves_source(source)
    after = snapshots()
    posting.book_allocation(preview, 'owner', 'Identical retry')
    db.session.commit()
    assert snapshots() == after


@pytest.mark.parametrize('bad', ['missing_marker', 'false_marker', 'total', 'source', 'closed'])
def test_partial_posting_rejects_changed_preview_or_closed_period_atomically(item_settlement, bad):
    source = item_settlement()
    preview = allocation(source)
    if bad == 'missing_marker':
        del preview['allow_suspense']
    elif bad == 'false_marker':
        preview['allow_suspense'] = False
    elif bad == 'total':
        preview['allocations'][0]['amount'] = '9.64'
    elif bad == 'source':
        db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON UPDATED DESCRIPTION' WHERE fitid=:id"),
                           {'id': source['fitid']})
        db.session.commit()
    else:
        db.session.add(ClosedPeriods(period='2026-01', journal_count=1,
            journal_digest='fixture', statement_snapshot={}))
        db.session.commit()
    before = snapshots()
    with pytest.raises(ValueError):
        posting.book_allocation(preview, 'owner', 'Invalid partial proposal')
    db.session.rollback()
    assert snapshots() == before


def test_revision_resolves_residual_with_versioned_proof_and_noop_replay(item_settlement):
    source = item_settlement()
    first = allocation(source)
    bundle = posting.book_allocation(first, 'owner', 'Initial partial item review')
    db.session.commit()
    before = snapshots()
    proposal = allocation(source, suspense=False)
    revised = posting.revise_allocation(proposal, 'owner', 'Receipt identifies remaining groceries',
                                         expected_posting_digest=first['digest'])
    db.session.commit()
    assert revised.id == bundle.id
    assert revised.posting_digest != first['digest']
    assert revised.posting_digest != proposal['digest']
    assert_preserves_source(source)
    entries = db.session.query(JournalEntries).all()
    assert {entry.debit_subaccount for entry in entries} == {'Books and Publications', 'Groceries'}
    assert db.session.query(JournalEntryEvents).count() == before['events'] + 4
    proofs = db.session.query(AccountingEvidence).filter_by(kind='amazon_allocation').order_by(AccountingEvidence.id).all()
    assert len(proofs) == 2
    payload = json.loads(proofs[-1].payload)
    assert payload['previous_preview'] == first
    assert payload['superseded_evidence_id'] == proofs[0].id
    assert payload['preview']['revision'] == dict(number=2, previous_posting_digest=first['digest'])
    assert payload['preview']['digest'] == revised.posting_digest
    after = snapshots()
    # Both a network retry using the old version and an explicit current-version
    # no-op retain the same proof and journal identities.
    posting.revise_allocation(proposal, 'owner', 'Retry', expected_posting_digest=first['digest'])
    posting.revise_allocation(proposal, 'owner', 'No change', expected_posting_digest=revised.posting_digest)
    posting.book_allocation(payload['preview'], 'owner', 'Replay current stored proof')
    db.session.commit()
    assert snapshots() == after


def test_conflicting_stale_revision_and_aba_cannot_consume_later_version(item_settlement):
    source = item_settlement()
    initial = allocation(source)
    posting.book_allocation(initial, 'owner', 'Initial partial review')
    db.session.commit()
    a_digest = initial['digest']
    middle = allocation(source, suspense=False)
    bundle = posting.revise_allocation(middle, 'owner', 'Resolve residual', a_digest)
    db.session.commit()
    b_digest = bundle.posting_digest
    conflicting = allocation(source, books='21.65', remainder='0', suspense=False)
    before = snapshots()
    with pytest.raises(ValueError, match='version changed'):
        posting.revise_allocation(conflicting, 'owner', 'Stale concurrent proposal', a_digest)
    db.session.rollback()
    assert snapshots() == before
    restored = posting.revise_allocation(initial, 'owner', 'Reopen disputed residual', b_digest)
    db.session.commit()
    assert restored.posting_digest not in (a_digest, b_digest)
    before = snapshots()
    with pytest.raises(ValueError, match='version changed'):
        posting.revise_allocation(middle, 'owner', 'Old approval cannot survive ABA', a_digest)
    db.session.rollback()
    assert snapshots() == before
    assert_preserves_source(source)


@pytest.mark.parametrize('failure', ['closed', 'source_drift', 'journal_drift', 'bad_digest', 'unallocated'])
def test_revision_requires_unchanged_current_proof_source_journals_and_open_period(item_settlement, failure):
    source = item_settlement()
    first = allocation(source)
    if failure != 'unallocated':
        posting.book_allocation(first, 'owner', 'Initial partial review')
        db.session.commit()
    proposal = allocation(source, suspense=False)
    expected = first['digest']
    if failure == 'closed':
        db.session.add(ClosedPeriods(period='2026-01', journal_count=2,
            journal_digest='fixture', statement_snapshot={}))
    elif failure == 'source_drift':
        db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON ALTERED SOURCE' WHERE fitid=:id"),
                           {'id': source['fitid']})
    elif failure == 'journal_drift':
        db.session.query(JournalEntries).filter_by(debit_subaccount='Books and Publications').one().debit_subaccount = 'Restaurants'
    elif failure == 'bad_digest':
        expected = '0' * 64
    db.session.commit()
    before = snapshots()
    with pytest.raises(ValueError):
        posting.revise_allocation(proposal, 'owner', 'Must reject invalid revision', expected)
    db.session.rollback()
    assert snapshots() == before


def test_revision_refund_retains_original_credit_cash_direction(item_settlement):
    source = item_settlement(refund=True)
    first = allocation(source)
    posting.book_allocation(first, 'owner', 'Partly identified returned goods')
    db.session.commit()
    proposal = allocation(source, suspense=False)
    posting.revise_allocation(proposal, 'owner', 'Identify returned groceries', first['digest'])
    db.session.commit()
    assert {entry.credit_subaccount for entry in db.session.query(JournalEntries)} == {'Books and Publications', 'Groceries'}
    assert_preserves_source(source)


def test_preview_requires_original_immutable_item_evidence(item_settlement):
    source = item_settlement()
    bundle = db.session.get(AmazonEvidenceBundles, source['bundle_id'])
    body = json.loads(bundle.details)
    body['lines'][0]['title'] = 'Changed purpose'
    bundle.details = dump(body)
    db.session.commit()
    with pytest.raises(ValueError, match='immutable item evidence'):
        allocation(source)


def test_new_allocation_cannot_forge_revision_metadata(item_settlement):
    source = item_settlement()
    preview = allocation(source)
    preview['revision'] = dict(number=2, previous_posting_digest='0' * 64)
    preview['digest'] = digest({key: value for key, value in preview.items() if key != 'digest'})
    before = snapshots()
    with pytest.raises(ValueError, match='Initial allocation'):
        posting.book_allocation(preview, 'owner', 'A revision must have existing evidence')
    db.session.rollback()
    assert snapshots() == before


@pytest.mark.parametrize('field,value', [
    ('functional_currency', 'EUR'), ('conversion_rate', Decimal('1')),
    ('conversion_source', 'unexpected conversion'), ('conversion_date', datetime(2026, 1, 2).date()),
])
def test_initial_allocation_cannot_launder_invalid_currency_metadata(item_settlement, monkeypatch, field, value):
    source = item_settlement()
    preview = allocation(source)
    before = snapshots()
    original = posting._lock_allocation

    def damaged_cached_row(*args):
        bundle, entries = original(*args)
        setattr(entries[0], field, value)
        return bundle, entries

    monkeypatch.setattr(posting, '_lock_allocation', damaged_cached_row)
    # PostgreSQL already rejects persisted invalid USD metadata. Simulate a
    # damaged decoded/cache value to verify the writer independently rejects it.
    with db.session.no_autoflush:
        with pytest.raises(ValueError, match='source-preserving'):
            posting.book_allocation(preview, 'owner', 'Invalid original journal metadata')
    db.session.rollback()
    assert snapshots() == before


def test_report_requires_intact_immutable_revision_ancestor(item_settlement):
    source = item_settlement()
    initial = allocation(source)
    posting.book_allocation(initial, 'owner', 'Initial partial review')
    db.session.commit()
    proposal = allocation(source, suspense=False)
    bundle = posting.revise_allocation(proposal, 'owner', 'Resolved residual', initial['digest'])
    db.session.commit()
    assert amazon_support_evidence(db.session)[1] == []
    ancestor = db.session.query(AccountingEvidence).filter_by(kind='amazon_allocation').order_by(AccountingEvidence.id).first()
    saved = ancestor.payload
    body = json.loads(saved)
    body['reason'] = 'Simulated altered ancestor'
    ancestor.payload = dump(body)
    with db.session.no_autoflush:
        supporting, issues = amazon_support_evidence(db.session)
        assert not supporting
        assert issues == [dict(bundle_id=bundle.id, problem='invalid_bank_allocation_support')]
    ancestor.payload = saved
    assert amazon_support_evidence(db.session)[1] == []


def test_report_cannot_hide_reordered_allocation_keys_by_sorting_legs(item_settlement):
    source = item_settlement()
    preview = allocation(source, suspense=False)
    bundle = posting.book_allocation(preview, 'owner', 'Two reviewed categories')
    db.session.commit()
    assert amazon_support_evidence(db.session)[1] == []
    left, right = db.session.query(JournalEntries).order_by(JournalEntries.allocation_key).all()
    # Preserve every account total, amount and source, but change which exact
    # reviewed leg belongs to each allocation key. Sorting would hide this drift.
    original_left = (left.debit_subaccount, left.functional_amount, left.source_amount)
    original_right = (right.debit_subaccount, right.functional_amount, right.source_amount)
    left.debit_subaccount, left.functional_amount, left.source_amount = original_right
    right.debit_subaccount, right.functional_amount, right.source_amount = original_left
    with db.session.no_autoflush:
        supporting, issues = amazon_support_evidence(db.session)
        assert not supporting
        assert issues == [dict(bundle_id=bundle.id, problem='invalid_bank_allocation_support')]
    db.session.rollback()
    assert amazon_support_evidence(db.session)[1] == []


@pytest.mark.parametrize('revision', [
    {'number': True, 'previous_posting_digest': '0' * 64},
    {'number': 3, 'previous_posting_digest': '0' * 64},
    {'number': 2, 'previous_posting_digest': 'invalid'},
])
def test_report_rejects_syntactically_hashed_but_invalid_revision_chain(item_settlement, revision):
    source = item_settlement()
    initial = allocation(source)
    bundle = posting.book_allocation(initial, 'owner', 'Initial partial review')
    db.session.commit()
    document = db.session.query(AccountingEvidence).filter_by(kind='amazon_allocation').one()
    payload = json.loads(document.payload)
    payload['preview']['revision'] = revision
    payload['preview']['digest'] = digest({key: value for key, value in payload['preview'].items() if key != 'digest'})
    # Simulate internally consistent hashes from an unsupported/corrupt decoder;
    # the read-only verifier must validate ancestry semantics too.
    document.payload = dump(payload)
    document.sha256 = digest(payload)
    bundle.posting_digest = payload['preview']['digest']
    with db.session.no_autoflush:
        supporting, issues = amazon_support_evidence(db.session)
        assert not supporting
        assert issues == [dict(bundle_id=bundle.id, problem='invalid_bank_allocation_support')]
    db.session.rollback()
