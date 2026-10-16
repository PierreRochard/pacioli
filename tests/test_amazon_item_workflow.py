"""Persisted item review conserves sources and never treats proposals as approval."""
import csv
from datetime import datetime, timezone
from decimal import Decimal
import io
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pacioli.extensions import db
from pacioli.models import (AccountingEvidence, AmazonEvidenceBundles, AmazonItemJobs,
                           AmazonItemRuns, BankFeedAccounts, ClosedPeriods, JournalEntries)
from pacioli.functions import amazon_csv
from pacioli.functions import amazon_item_advisor as advisor
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions.accounting_report import source_differences, trial_balance_differences
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.reconciliation import digest, dump
from pacioli.functions.source_identity import source_identity


def _financial_state():
    return {table: digest([dict(row._mapping) for row in db.session.execute(text(
        'SELECT * FROM ' + table + ' ORDER BY 1'))]) for table in (
            'bookkeeping.journal_entries', 'bookkeeping.journal_entry_events',
            'bookkeeping.ofx_source_identities', 'bookkeeping.ofx_journal_sources',
            'bookkeeping.trial_balances', 'ofx.stmttrn', 'admin.mappings', 'amazon.evidence_bundles')}


def _journal_trigger_states():
    return {row.tgname: row.tgenabled for row in db.session.execute(text(
        "SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid="
        "'bookkeeping.journal_entries'::regclass AND NOT tgisinternal ORDER BY tgname"))}


def _posting_state():
    return dict(financial=_financial_state(), triggers=_journal_trigger_states(),
        evidence=list(db.session.query(AccountingEvidence.id, AccountingEvidence.sha256).order_by(
            AccountingEvidence.id)))


def _observe_balance_rebuild(monkeypatch):
    from pacioli.functions import mapping_workflow
    rebuild = mapping_workflow.rebuild_trial_balances
    original_triggers = _journal_trigger_states()
    assert original_triggers['subaccount_insert_trigger'] == 'O'
    assert all(original_triggers[name] == 'O' for name in
               ('journal_event_trigger', 'journal_closed_period_guard', 'journal_source_link'))
    calls = []

    def observed():
        # Only the derived projection may be deferred. All source, audit and
        # period guards still run on each journal mutation in this transaction.
        assert _journal_trigger_states() == dict(original_triggers, subaccount_insert_trigger='D')
        calls.append(True)
        rebuild()

    monkeypatch.setattr(mapping_workflow, 'rebuild_trial_balances', observed)
    return calls, original_triggers


@pytest.fixture
def item_workflow_source(clean_ledger):
    account_id = db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) "
        "VALUES('ccacctfrom','Credit Card') RETURNING id")).scalar()
    db.session.execute(text('INSERT INTO ofx.ccacctfrom(id,acctid) VALUES(:id,:acctid)'),
                       dict(id=account_id, acctid=uuid4().hex[:12] + '7331'))
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    fitid = uuid4().hex
    when = datetime(2026, 9, 3, 12, 34, 56, tzinfo=timezone.utc)
    db.session.execute(text('''INSERT INTO ofx.stmttrn
        (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES(:fitid,:aid,'DEBIT',:when,-32.40,'AMAZON RETAIL')'''),
        dict(fitid=fitid, aid=account_id, when=when))
    transaction_id = source_identity(db.session, account_id, fitid)
    original = post_pair(timestamp=when, debit='Bookkeeping Suspense', credit='Credit Card',
        functional_amount='32.40', transaction_source='ofx', transaction_id=transaction_id)
    book = {
        'Order ID': '123-1234567-1234567', 'Order Status': 'Closed', 'Shipment Status': 'Shipped',
        'Currency': 'USD', 'Order Date': '2026-09-01T12:00:00Z', 'Ship Date': '2026-09-03T00:30:00Z',
        'Carrier Name & Tracking Number': 'Carrier: fixture-private-tracking',
        'Original Quantity': '1', 'Product Name': 'Fixture paperback novel',
        'Unit Price': '10.00', 'Unit Price Tax': '0.80', 'Shipping Charge': '0.00',
        'Total Discounts': '0.00', 'Total Amount': '10.80',
        'Shipment Item Subtotal': '30.00', 'Shipment Item Subtotal Tax': '2.40',
        'Payment Method Type': 'Visa - 7331', 'ASIN': 'B000000001', 'Department': 'Books',
        'Website': 'Amazon.com', 'Product Condition': 'New',
        'Gift Message': 'Not Available', 'Gift Recipient Contact': 'Not Available',
        'Gift Sender Name': 'Not Available', 'Purchase Order Number': 'Not Available',
        'Billing Address': 'PRIVATE BILLING ADDRESS', 'Shipping Address': 'PRIVATE SHIPPING ADDRESS',
    }
    bodywash = dict(book, **{'Product Name': 'Fixture ordinary body wash', 'ASIN': 'B000000002',
        'Department': 'Personal Care', 'Unit Price': '20.00', 'Unit Price Tax': '1.60',
        'Total Amount': '21.60'})
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(book))
    writer.writeheader(); writer.writerows([book, bodywash])
    archive = amazon_csv.import_archive(stream.getvalue().encode(), 'Order History.csv', 'fixture owner')[0]
    db.session.commit()
    return dict(account_id=account_id, transaction_id=transaction_id, original_id=original.id,
                archive_id=archive.id, fitid=fitid, when=when)


def _fake_infer(item, categories):
    book = 'paperback' in item['title']
    category = 'Books and Publications' if book else None
    answer = dict(item_id=item['item_id'], decision='suggest' if book else 'needs_purpose',
        category=category, evidence_refs=['product.title'],
        policy_refs=[advisor.POLICY_VERSION] + (['category:' + category] if category else []),
        reason='Synthetic item review evidence.',
        missing_fact=None if book else 'Fixture requests independent purpose confirmation.')
    return dict(answer=answer, category=category, validation_errors=[], raw_content=json.dumps(answer))


def _prepared(source, *, approve=True):
    run = workflow.create_run(source['archive_id'], 'fixture owner', think=False)
    run_id = run.id
    db.session.commit()
    workflow.run_worker(run_id, infer_fn=_fake_infer)
    jobs = db.session.query(AmazonItemJobs).filter_by(run_id=run_id).order_by(AmazonItemJobs.id).all()
    book = next(job for job in jobs if job.state == 'suggested')
    bodywash = next(job for job in jobs if job.state == 'held')
    if approve:
        workflow.review_item(book.id, 'Books and Publications',
                             'Independently inspected the source paperback item and its costs.', 'fixture owner')
        db.session.commit()
    return run_id, book.id, bodywash.id


def _post(run_id, expected=None):
    result = workflow.post_reviewed(run_id, expected or workflow.review_digest(run_id),
                                   'fixture owner', 'Approve the exact displayed item allocations.')
    db.session.commit()
    return result


def _assert_source(source, expected):
    rows = db.session.query(JournalEntries).filter_by(transaction_source='ofx',
                                                     transaction_id=source['transaction_id']).all()
    assert {row.debit_subaccount: row.functional_amount for row in rows} == expected
    assert sum(row.functional_amount for row in rows) == Decimal('32.40')
    for row in rows:
        assert row.credit_subaccount == 'Credit Card'
        assert row.timestamp == source['when']
        assert row.source_currency == row.functional_currency == 'USD'
        assert row.source_amount == row.functional_amount
    assert not source_differences(db.session)
    assert not trial_balance_differences(db.session)


def test_run_dedupes_and_fake_model_never_sees_private_source_or_writes_ledger(item_workflow_source):
    source = item_workflow_source
    before = _financial_state()
    run = workflow.create_run(source['archive_id'], 'fixture owner', think=False)
    run_id = run.id
    db.session.commit()
    assert workflow.create_run(source['archive_id'], 'another reviewer', think=False).id == run_id
    db.session.commit()
    assert db.session.query(AmazonItemRuns).count() == 1
    assert db.session.query(AmazonItemJobs).count() == 2
    observed = []
    def infer(item, categories):
        observed.append(item)
        return _fake_infer(item, categories)
    workflow.run_worker(run_id, infer_fn=infer)
    assert len(observed) == 2
    encoded = json.dumps(observed)
    for private in ('PRIVATE BILLING', 'PRIVATE SHIPPING', 'fixture-private-tracking', '7331', 'amount', 'ASIN'):
        assert private not in encoded
    assert _financial_state() == before
    status = workflow.run_status(run_id)
    assert status['state'] == 'completed' and status['counts']['suggested'] == status['counts']['held'] == 1
    assert status['counts']['reviewed'] == 0
    raw_manifest = db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence '
        'WHERE id=(SELECT manifest_id FROM amazon.item_runs WHERE id=:id)'), dict(id=run_id)).scalar()
    raw_proposals = db.session.execute(text('SELECT proposal FROM amazon.item_jobs WHERE run_id=:id'), dict(id=run_id)).scalars().all()
    assert raw_manifest.startswith('fernet:')
    assert all(raw.startswith('fernet:') and 'paperback' not in raw for raw in raw_proposals)
    workflow.run_worker(run_id, infer_fn=lambda *args: pytest.fail('A completed run must not infer again'))
    assert _financial_state() == before


def test_unreviewed_suggestions_remain_entirely_in_suspense(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source, approve=False)
    before = _financial_state()
    result = _post(run_id)
    assert result['posted'] == result['revised'] == 0 and result['held_settlements'] == 1
    assert _financial_state() == before
    _assert_source(item_workflow_source, {'Bookkeeping Suspense': Decimal('32.40')})


def test_posting_progress_counts_settlements_not_multiple_item_components(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    events = []
    result = workflow.post_reviewed(run_id, workflow.review_digest(run_id), 'fixture owner',
        'Approve one settlement containing several item and tax lines.',
        report=lambda stage, **counts: events.append((stage, counts)))
    db.session.commit()
    assert result['posted'] == 1
    counts = [data['processed'] for stage, data in events if data['total'] == 1]
    assert counts == sorted(counts) and set(counts) <= {0, 1}
    assert [data['processed'] for stage, data in events if stage == 'settlement_ready'] == [1]


def test_partial_post_and_old_request_digest_replay_preserve_every_source_cent(item_workflow_source, monkeypatch):
    run_id, _, _ = _prepared(item_workflow_source)
    rebuilds, triggers = _observe_balance_rebuild(monkeypatch)
    request_digest = workflow.review_digest(run_id)
    result = _post(run_id, request_digest)
    assert result['posted'] == 1 and result['revised'] == 0
    assert Decimal(result['classified_amount']) == Decimal('10.80')
    assert Decimal(result['suspense_amount']) == Decimal('21.60')
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Bookkeeping Suspense': Decimal('21.60')})
    assert rebuilds == [True] and _journal_trigger_states() == triggers
    events = db.session.execute(text("SELECT action FROM bookkeeping.journal_entry_events "
                                    "ORDER BY id")).scalars().all()
    assert events.count('DELETE') == 1 and events.count('INSERT') == 3
    before = _posting_state()
    repeated = _post(run_id, request_digest)
    assert repeated['posted'] == repeated['revised'] == 0 and repeated['unchanged'] == 1
    assert _posting_state() == before and rebuilds == [True]


def test_later_independent_review_revises_only_remaining_suspense_and_replays(item_workflow_source, monkeypatch):
    run_id, _, bodywash = _prepared(item_workflow_source)
    _post(run_id)
    bundle = db.session.query(AmazonEvidenceBundles).one()
    old_digest = bundle.posting_digest
    workflow.review_item(bodywash, 'Personal Care', 'Independently confirmed ordinary personal body wash.', 'fixture owner')
    db.session.commit()
    rebuilds, triggers = _observe_balance_rebuild(monkeypatch)
    request_digest = workflow.review_digest(run_id)
    result = _post(run_id, request_digest)
    assert result['revised'] == 1 and result['posted'] == 0
    assert bundle.posting_digest != old_digest
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Personal Care': Decimal('21.60')})
    assert rebuilds == [True] and _journal_trigger_states() == triggers
    proofs = db.session.query(AccountingEvidence).filter_by(kind='amazon_allocation').order_by(AccountingEvidence.id).all()
    latest = json.loads(proofs[-1].payload)
    assert latest['preview']['revision']['previous_posting_digest'] == old_digest
    before = _posting_state()
    assert _post(run_id, request_digest)['unchanged'] == 1
    assert _posting_state() == before and rebuilds == [True]


@pytest.mark.parametrize('drift', ['description', 'card', 'currency', 'competing_source', 'competing_account'])
def test_source_or_account_match_drift_rejects_entire_post_without_financial_writes(item_workflow_source, drift):
    source = item_workflow_source
    run_id, _, _ = _prepared(source)
    expected = workflow.review_digest(run_id)
    if drift == 'description':
        db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON CHANGED' WHERE fitid=:id"), dict(id=source['fitid']))
    elif drift == 'card':
        db.session.execute(text("UPDATE ofx.ccacctfrom SET acctid='different9999' WHERE id=:id"), dict(id=source['account_id']))
    elif drift == 'currency':
        db.session.get(BankFeedAccounts, source['account_id']).currency = 'EUR'
    elif drift == 'competing_source':
        db.session.execute(text('''INSERT INTO ofx.stmttrn
            (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
            VALUES(:fitid,:aid,'DEBIT',:when,-32.40,'AMAZON RETAIL')'''),
            dict(fitid=uuid4().hex, aid=source['account_id'], when=source['when']))
    else:
        other = db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) "
            "VALUES('ccacctfrom','Credit Card') RETURNING id")).scalar()
        db.session.execute(text("INSERT INTO ofx.ccacctfrom(id,acctid) VALUES(:id,'another7331')"), dict(id=other))
        db.session.add(BankFeedAccounts(ofx_account_id=other, currency='USD'))
    db.session.commit()
    before = _posting_state()
    with pytest.raises(ValueError):
        workflow.post_reviewed(run_id, expected, 'fixture owner', 'Must reject changed matching evidence.')
    # A caller catching the error must not accidentally commit posting writes.
    db.session.commit()
    assert _posting_state() == before


def test_old_decision_digest_is_rejected_after_second_session_update_despite_cached_job(item_workflow_source):
    run_id, book_id, _ = _prepared(item_workflow_source)
    expected = workflow.review_digest(run_id)
    cached = db.session.get(AmazonItemJobs, book_id)
    old_id = cached.decision_id
    original = json.loads(db.session.get(AccountingEvidence, old_id).payload)
    second = Session(bind=db.engine)
    try:
        revised = dict(original, category=None, reason='Independent reviewer requests more evidence.',
                       reviewer='second reviewer', previous_decision_id=old_id)
        doc = AccountingEvidence(kind='amazon_item_decision', sha256=digest(revised),
                                 payload=dump(revised), reviewer='second reviewer')
        second.add(doc); second.flush()
        new_id = doc.id
        second.query(AmazonItemJobs).filter_by(id=book_id).update({'decision_id': new_id})
        second.commit()
    finally:
        second.close()
    assert cached.decision_id == old_id  # Deliberately stale ORM identity map.
    before = _financial_state()
    with pytest.raises(ValueError, match='changed'):
        workflow.post_reviewed(run_id, expected, 'fixture owner', 'An old approval cannot consume a changed decision.')
    assert cached.decision_id == new_id
    db.session.rollback()
    assert _financial_state() == before


def test_review_refreshes_cached_job_state_before_accepting_decision(item_workflow_source):
    run_id, book_id, _ = _prepared(item_workflow_source, approve=False)
    cached = db.session.get(AmazonItemJobs, book_id)
    assert cached.state == 'suggested'
    second = Session(bind=db.engine)
    try:
        second.query(AmazonItemJobs).filter_by(id=book_id).update({'state': 'running'})
        second.commit()
    finally:
        second.close()
    with pytest.raises(ValueError, match='Wait'):
        workflow.review_item(book_id, 'Books and Publications', 'Stale view must be refused.', 'fixture owner')
    db.session.rollback()
    assert db.session.get(AmazonItemJobs, book_id).decision_id is None


def test_closed_period_rejects_posting_atomically(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    expected = workflow.review_digest(run_id)
    db.session.add(ClosedPeriods(period='2026-09', journal_count=1,
                                journal_digest='fixture', statement_snapshot={}))
    db.session.commit()
    before = _posting_state()
    with pytest.raises(ValueError, match='closed'):
        workflow.post_reviewed(run_id, expected, 'fixture owner', 'Closed periods are protected.')
    db.session.commit()
    assert _posting_state() == before
    assert db.session.query(AmazonEvidenceBundles).count() == 0


def _two_settlement_run(source):
    """Two distinct native shipments so failure after the first is truly partial."""
    _, tables = workflow._source_tables(db.session, source['archive_id'])
    rows = [dict(zip(tables[0]['headers'], values)) for values in tables[0]['rows']]
    other = dict(rows[0], **{
        'Order ID': '123-7654321-7654321', 'Order Date': '2026-09-09T12:00:00Z',
        'Ship Date': '2026-09-11T00:30:00Z', 'Unit Price': '40.00', 'Unit Price Tax': '3.20',
        'Total Amount': '43.20', 'Shipment Item Subtotal': '40.00',
        'Shipment Item Subtotal Tax': '3.20', 'ASIN': 'B000000003',
        'Product Name': 'Fixture second paperback novel',
        'Carrier Name & Tracking Number': 'Carrier: second-private-tracking',
    })
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=tables[0]['headers'])
    writer.writeheader(); writer.writerows(rows + [other])
    archive = amazon_csv.import_archive(stream.getvalue().encode(), 'Order History.csv', 'fixture owner')[0]
    fitid = uuid4().hex
    when = datetime(2026, 9, 11, 12, 34, 56, tzinfo=timezone.utc)
    db.session.execute(text('''INSERT INTO ofx.stmttrn
        (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES(:fitid,:aid,'DEBIT',:when,-43.20,'AMAZON RETAIL')'''),
        dict(fitid=fitid, aid=source['account_id'], when=when))
    transaction_id = source_identity(db.session, source['account_id'], fitid)
    post_pair(timestamp=when, debit='Bookkeeping Suspense', credit='Credit Card',
        functional_amount='43.20', transaction_source='ofx', transaction_id=transaction_id)
    db.session.commit()
    run = workflow.create_run(archive.id, 'fixture owner', think=False)
    run_id = run.id
    db.session.commit()
    workflow.run_worker(run_id, infer_fn=_fake_infer)
    jobs = workflow._jobs(run_id)
    assert len(jobs) == 3
    for job in jobs:
        if job.state == 'suggested':
            workflow.review_item(job.id, 'Books and Publications',
                'Independently inspected this source paperback and its item costs.', 'fixture owner')
    db.session.commit()
    return run_id


@pytest.mark.parametrize('failure', ['midbatch', 'currency_constraint', 'rebuild', 'invalid_balances', 'after_restore'])
def test_bulk_post_failure_rolls_back_its_savepoint_even_when_caller_commits(
        item_workflow_source, monkeypatch, failure):
    from pacioli.functions import mapping_workflow
    run_id = _two_settlement_run(item_workflow_source)
    expected = workflow.review_digest(run_id)
    before = _posting_state()
    real_rebuild = mapping_workflow.rebuild_trial_balances
    visited = []

    def report(stage, **counts):
        if stage == 'settlement_ready':
            visited.append(counts['processed'])
            if failure in ('midbatch', 'currency_constraint') and counts['processed'] == 1:
                assert counts['prepared_new'] == 1
                assert db.session.query(AccountingEvidence).filter_by(kind='amazon_item_allocation').count() == 1
                assert _journal_trigger_states()['subaccount_insert_trigger'] == 'D'
                if failure == 'currency_constraint':
                    # Bypass Python intentionally to prove database money
                    # constraints still reject writes while balances are deferred.
                    db.session.execute(text('''INSERT INTO bookkeeping.journal_entries
                        (timestamp,debit_subaccount,credit_subaccount,functional_amount,
                         functional_currency,source_amount,source_currency,allocation_key)
                        SELECT timestamp,debit_subaccount,credit_subaccount,functional_amount,
                               'EUR',source_amount,source_currency,'invalid-currency-proof'
                        FROM bookkeeping.journal_entries ORDER BY id LIMIT 1'''))
                    pytest.fail('Database accepted an unsupported functional currency')
                raise RuntimeError('Synthetic failure after one prepared settlement')
        if stage == 'ready_to_commit' and failure == 'after_restore':
            assert _journal_trigger_states() == before['triggers']
            assert db.session.query(AccountingEvidence).filter_by(kind='amazon_item_batch').count() == 1
            raise RuntimeError('Synthetic failure after restoration and posting evidence')

    def rebuild():
        assert _journal_trigger_states() == dict(before['triggers'], subaccount_insert_trigger='D')
        real_rebuild()
        if failure == 'rebuild':
            raise RuntimeError('Synthetic failure after rebuilding derived balances')
        if failure == 'invalid_balances':
            db.session.execute(text('UPDATE bookkeeping.trial_balances SET net_balance=net_balance+1 '
                'WHERE id=(SELECT min(id) FROM bookkeeping.trial_balances)'))

    with monkeypatch.context() as patch:
        patch.setattr(mapping_workflow, 'rebuild_trial_balances', rebuild)
        with pytest.raises((RuntimeError, ValueError, DBAPIError)):
            workflow.post_reviewed(run_id, expected, 'fixture owner',
                                   'Approve the two explicitly reviewed shipments.', report=report)
    # Deliberately do not rollback here: the public API must already have
    # restored every financial/evidence write and the transactional trigger DDL.
    assert visited == ([1] if failure in ('midbatch', 'currency_constraint') else [1, 2])
    assert _posting_state() == before
    db.session.commit()
    assert _posting_state() == before
    assert not trial_balance_differences(db.session) and not source_differences(db.session)
    assert _post(run_id, expected)['posted'] == 2
    assert _journal_trigger_states() == before['triggers']
    assert not trial_balance_differences(db.session) and not source_differences(db.session)


def test_bulk_post_refuses_an_initially_disabled_balance_trigger(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    expected = workflow.review_digest(run_id)
    db.session.execute(text('ALTER TABLE bookkeeping.journal_entries '
                            'DISABLE TRIGGER subaccount_insert_trigger'))
    db.session.commit()
    try:
        before = _posting_state()
        with pytest.raises(ValueError, match='trigger must be enabled'):
            workflow.post_reviewed(run_id, expected, 'fixture owner',
                                   'A preexisting disabled accounting projection is not safe to change.')
        db.session.commit()
        assert _posting_state() == before
    finally:
        db.session.rollback()
        db.session.execute(text('ALTER TABLE bookkeeping.journal_entries '
                                'ENABLE TRIGGER subaccount_insert_trigger'))
        db.session.commit()


def test_all_hold_correction_of_posted_basket_is_explicitly_rejected(item_workflow_source):
    run_id, book_id, _ = _prepared(item_workflow_source)
    _post(run_id)
    workflow.review_item(book_id, None, 'Withdraw the earlier classification pending new evidence.', 'fixture owner')
    db.session.commit()
    expected = workflow.review_digest(run_id)
    before = _financial_state()
    with pytest.raises(ValueError, match='separate reviewed reversal'):
        workflow.post_reviewed(run_id, expected, 'fixture owner', 'Do not silently leave a superseded classification.')
    db.session.rollback()
    assert _financial_state() == before


def test_decision_preserves_original_proposal_when_mutable_job_result_changes(item_workflow_source):
    run_id, book_id, _ = _prepared(item_workflow_source)
    job = db.session.get(AmazonItemJobs, book_id)
    decision = json.loads(db.session.get(AccountingEvidence, job.decision_id).payload)
    original_proposal = json.loads(job.proposal)
    assert decision['proposal'] == original_proposal
    assert decision['proposal_digest'] == digest(original_proposal)
    job.proposal = dump(dict(original_proposal, raw_content='A later diagnostic response.'))
    db.session.commit()
    immutable = json.loads(db.session.get(AccountingEvidence, job.decision_id).payload)
    assert immutable['proposal'] == original_proposal
    assert immutable['proposal_digest'] == digest(immutable['proposal'])


def test_refreshed_partial_run_preserves_posted_reviews_without_claiming_new_model_output(item_workflow_source, monkeypatch):
    old_run, book_id, _ = _prepared(item_workflow_source)
    _post(old_run)
    original_decision = db.session.get(AmazonItemJobs, book_id).decision_id
    original_proof = json.loads(db.session.get(AccountingEvidence, original_decision).payload)
    monkeypatch.setattr(advisor, 'POLICY_VERSION', advisor.POLICY_VERSION + '-refreshed')
    before = _financial_state()
    refreshed = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner', think=False)
    assert refreshed.id != old_run
    refreshed_id = refreshed.id
    db.session.commit()
    copied = next(j for j in workflow._jobs(refreshed_id) if j.decision_id is not None)
    assert copied.proposal is None
    decision = workflow._decision(copied)
    assert decision['category'] == 'Books and Publications'
    assert decision['proposal'] is decision['proposal_digest'] is None
    assert decision['carry_forward']['source_decision_id'] == original_decision
    assert decision['carry_forward']['source_run_id'] == old_run
    assert decision['carry_forward']['source_reviewer'] == original_proof['reviewer']
    assert decision['carry_forward']['source_reason'] == original_proof['reason']
    assert decision['item_digest'] == decision['carry_forward']['source_item_digest']
    observed = []
    def infer(item, categories):
        observed.append(item['title'])
        return _fake_infer(item, categories)
    workflow.run_worker(refreshed_id, infer_fn=infer)
    assert observed == ['Fixture ordinary body wash']
    assert _financial_state() == before
    rows = workflow.page(refreshed_id)['rows']
    bodywash = next(row for row in rows if 'body wash' in row['item']['title'])
    workflow.review_item(bodywash['id'], 'Personal Care', 'Source identifies personal body wash.', 'fixture owner')
    db.session.commit()
    approved = workflow.review_digest(refreshed_id)
    rebuilds, triggers = _observe_balance_rebuild(monkeypatch)
    result = _post(refreshed_id, approved)
    assert result['revised'] == 1 and result['posted'] == 0
    _assert_source(item_workflow_source, {'Books and Publications': Decimal('10.80'),
                                        'Personal Care': Decimal('21.60')})
    assert rebuilds == [True] and _journal_trigger_states() == triggers
    after = _posting_state()
    assert _post(refreshed_id, approved)['unchanged'] == 1
    assert _posting_state() == after and rebuilds == [True]


def test_resumed_worker_refuses_changed_model_pin(item_workflow_source, monkeypatch):
    run = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    run_id = run.id
    db.session.commit()
    before = _financial_state()
    monkeypatch.setitem(advisor.PROFILES, 'text14', ('changed-model', '0' * 64, 13000))
    with pytest.raises(ValueError, match='pinned model changed'):
        workflow.run_worker(run_id, infer_fn=_fake_infer)
    assert _financial_state() == before
    assert db.session.get(AmazonItemRuns, run_id).state == 'needs_attention'


def test_old_posting_digest_is_rejected_after_valid_second_session_revision_despite_cached_bundle(
        item_workflow_source, monkeypatch):
    from pacioli.functions import amazon_evidence as allocation

    source = item_workflow_source
    run_id, _, _ = _prepared(source)
    _post(run_id)
    cached = db.session.query(AmazonEvidenceBundles).one()
    old_posting_digest = cached.posting_digest
    old_request_digest = workflow.review_digest(run_id)
    bundle_id = cached.id
    second = Session(bind=db.engine)
    try:
        # Exercise the actual validated revision writer against a separate
        # transaction while retaining session A's cached original bundle.
        with monkeypatch.context() as patch:
            patch.setattr(db, 'session', second)
            proposal = allocation.allocation_preview(bundle_id, source['transaction_id'], [
                dict(category='Books and Publications', amount='10.80'),
                dict(category='Personal Care', amount='21.60'),
            ])
            changed = allocation.revise_allocation(proposal, 'second reviewer',
                'Independent receipt review resolved the remaining body wash.', old_posting_digest)
            new_posting_digest = changed.posting_digest
            second.commit()
    finally:
        second.close()
    assert new_posting_digest != old_posting_digest
    assert cached.posting_digest == old_posting_digest
    before = _financial_state()
    with pytest.raises(ValueError, match='Item decisions or current allocations changed'):
        workflow.post_reviewed(run_id, old_request_digest, 'fixture owner',
                               'An earlier batch approval cannot consume a later revision.')
    assert cached.posting_digest == new_posting_digest
    db.session.rollback()
    assert _financial_state() == before
    _assert_source(source, {'Books and Publications': Decimal('10.80'),
                            'Personal Care': Decimal('21.60')})


def test_oversized_item_request_is_reviewable_and_does_not_starve_later_jobs(item_workflow_source):
    source = item_workflow_source
    manifest = json.loads(db.session.get(AccountingEvidence, source['archive_id']).payload)
    table = json.loads(db.session.get(AccountingEvidence, manifest['tables'][0]['evidence_id']).payload)
    table['rows'][0][table['headers'].index('Product Name')] = 'Fixture paperback ' + 'x' * 9000
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(table['headers']); writer.writerows(table['rows'])
    archive = amazon_csv.import_archive(stream.getvalue().encode(), 'Order History.csv', 'fixture owner')[0]
    db.session.commit()
    run = workflow.create_run(archive.id, 'fixture owner')
    run_id = run.id
    db.session.commit()
    before = _financial_state()
    observed = []

    def infer(item, categories):
        observed.append(item['title'])
        return _fake_infer(item, categories)

    workflow.run_worker(run_id, infer_fn=infer)
    first, later = db.session.query(AmazonItemJobs).filter_by(run_id=run_id).order_by(AmazonItemJobs.id).all()
    assert observed == ['Fixture ordinary body wash']
    assert first.state == 'failed' and later.state == 'held'
    failed_proposal = json.loads(first.proposal)
    assert failed_proposal['failure_phase'] == 'request_validation'
    assert failed_proposal['failure_code'] == 'item_exceeds_byte_budget'
    assert failed_proposal['retryable'] is False
    assert failed_proposal['answer'] is None
    assert len(first.proposal) < 512 and 'paperback' not in first.proposal
    assert workflow.run_status(run_id)['state'] == 'needs_attention'
    assert _financial_state() == before
    workflow.run_worker(run_id, infer_fn=lambda *args: pytest.fail('Permanent input failures must not be retried'))
    assert json.loads(first.proposal) == failed_proposal
    assert workflow.run_status(run_id)['state'] == 'needs_attention'
    assert _financial_state() == before
    # The failure is no longer stuck in "running", so a human can record a
    # source-based hold without waiting for a model that cannot accept this title.
    decision = workflow.review_item(first.id, None,
        'Source title exceeds the model budget; retain suspense pending manual inspection.', 'fixture owner')
    db.session.commit()
    assert json.loads(decision.payload)['category'] is None
    workflow.run_worker(run_id, infer_fn=lambda *args: pytest.fail('Reviewed failures must remain skipped'))
    assert workflow.run_status(run_id)['state'] == 'completed'
    assert _financial_state() == before


@pytest.mark.parametrize('failure_kind', ['runtime_value_error', 'resource_error', 'configuration_error'])
def test_shared_worker_failures_stop_batch_remain_retryable_and_never_expose_exception_text(
        item_workflow_source, monkeypatch, failure_kind):
    from pacioli.functions.chase_local_bridge import LocalChaseError

    run = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    run_id = run.id
    db.session.commit()
    before = _financial_state()
    observed = []
    exception = (LocalChaseError('private resource diagnostic') if failure_kind == 'resource_error'
                 else ValueError('invalid_categories' if failure_kind == 'configuration_error'
                                 else 'item_exceeds_byte_budget'))

    def shared_failure(item, categories):
        observed.append(item['item_id'])
        # The same text as an input failure must still stop the batch when it
        # comes from inference rather than the pure request-construction stage.
        raise exception

    with monkeypatch.context() as patch:
        if failure_kind == 'configuration_error':
            def invalid_shared_config(*args, **kwargs):
                raise exception
            patch.setattr(advisor, 'request_for', invalid_shared_config)
        with pytest.raises(type(exception)):
            workflow.run_worker(run_id, infer_fn=shared_failure)
    assert len(observed) == (0 if failure_kind == 'configuration_error' else 1)
    first, later = db.session.query(AmazonItemJobs).filter_by(run_id=run_id).order_by(AmazonItemJobs.id).all()
    assert first.state == 'failed' and later.state == 'queued'
    failure = json.loads(first.proposal)
    assert failure['failure_phase'] == 'runtime' and failure['retryable'] is True
    assert failure['failure_code'] == type(exception).__name__
    assert str(exception) not in first.proposal
    assert workflow.run_status(run_id)['state'] == 'needs_attention'
    assert _financial_state() == before
    workflow.run_worker(run_id, infer_fn=_fake_infer)
    assert first.state == 'suggested' and later.state == 'held'
    assert workflow.run_status(run_id)['state'] == 'completed'
    assert _financial_state() == before


def test_item_category_install_is_additive_idempotent_and_operating(clean_ledger):
    from pacioli.models import Accounts, Classifications, Subaccounts
    before = _financial_state()
    workflow.install_item_categories(db.session)
    first = {name: db.session.query(Subaccounts).filter_by(name=name).one().id
             for name in workflow.ITEM_CATEGORY_NAMES}
    assert workflow.install_item_categories(db.session) == []
    for name, identifier in first.items():
        subaccount = db.session.get(Subaccounts, identifier)
        account = db.session.get(Accounts, subaccount.parent)
        classification = db.session.get(Classifications, account.parent)
        assert subaccount.name == name and subaccount.description == advisor.CATEGORY_DEFINITIONS[name]
        assert account.cash_source == 'Operating' and account.is_cash is False
        assert classification.parent == 'Expenses'
        assert not subaccount.tax_tags
    assert _financial_state() == before


@pytest.mark.parametrize('collision', ['classification', 'account_parent', 'cash_source', 'is_cash', 'subaccount_parent'])
def test_item_category_install_rejects_existing_structure_without_repairing_it(clean_ledger, collision):
    from pacioli.models import Accounts, Classifications, Subaccounts
    workflow.install_item_categories(db.session)
    account = db.session.get(Accounts, 'Household Supplies Expense')
    subaccount = db.session.query(Subaccounts).filter_by(name='Household Supplies').one()
    classification = db.session.get(Classifications, 'Ordinary Personal Purchases')
    target, attribute, value = {
        'classification': (classification, 'parent', 'Assets'),
        'account_parent': (account, 'parent', 'Fixed Costs'),
        'cash_source': (account, 'cash_source', 'Investing'),
        'is_cash': (account, 'is_cash', True),
        'subaccount_parent': (subaccount, 'parent', 'Medical Expense'),
    }[collision]
    setattr(target, attribute, value)
    db.session.flush()
    before = _financial_state()
    with pytest.raises(ValueError, match='conflict'):
        workflow.install_item_categories(db.session)
    assert getattr(target, attribute) == value
    assert _financial_state() == before


def test_partial_refresh_does_not_revive_a_superseded_review(item_workflow_source):
    run_id, book_id, _ = _prepared(item_workflow_source)
    _post(run_id)
    workflow.review_item(book_id, None, 'Owner withdrew this approval pending more evidence.', 'fixture owner')
    db.session.commit()
    before = _financial_state()
    refreshed = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    assert not any(job.decision_id for job in workflow._jobs(refreshed.id))
    assert workflow._manifest(refreshed)['carried_decisions'] == {}
    assert _financial_state() == before


def test_partial_refresh_does_not_carry_a_category_removed_from_policy(item_workflow_source, monkeypatch):
    run_id, _, _ = _prepared(item_workflow_source)
    _post(run_id)
    monkeypatch.delitem(advisor.CATEGORY_DEFINITIONS, 'Books and Publications')
    before = _financial_state()
    refreshed = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    assert not any(job.decision_id for job in workflow._jobs(refreshed.id))
    assert 'Books and Publications' not in workflow._manifest(refreshed)['categories']
    assert _financial_state() == before


def test_withdrawal_after_refresh_invalidates_carried_approval(item_workflow_source):
    old_run, book_id, _ = _prepared(item_workflow_source)
    _post(old_run)
    refreshed = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    refreshed_id = refreshed.id
    db.session.commit()
    workflow.run_worker(refreshed_id, infer_fn=_fake_infer)
    residual = next(row for row in workflow.page(refreshed_id)['rows'] if 'body wash' in row['item']['title'])
    workflow.review_item(residual['id'], 'Personal Care', 'Ordinary personal body wash.', 'fixture owner')
    workflow.review_item(book_id, None, 'Withdraw the original book review pending investigation.', 'fixture owner')
    db.session.commit()
    before = _financial_state()
    with pytest.raises(ValueError, match='carried item review changed'):
        workflow.post_reviewed(refreshed_id, workflow.review_digest(refreshed_id),
                               'fixture owner', 'A copied review must respect later withdrawal of its source.')
    db.session.rollback()
    assert _financial_state() == before


def test_partial_refresh_requires_exact_original_source_items(item_workflow_source, monkeypatch):
    from copy import deepcopy
    run_id, _, _ = _prepared(item_workflow_source)
    _post(run_id)
    real_project = workflow.project_records
    def changed_projection(*args, **kwargs):
        rows = deepcopy(real_project(*args, **kwargs))
        rows[0]['items'][0]['title'] = 'A different source product with the same price'
        return rows
    monkeypatch.setattr(workflow, 'project_records', changed_projection)
    before = _financial_state()
    with pytest.raises(ValueError, match='occurrences differ'):
        workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    db.session.rollback()
    assert _financial_state() == before


def test_partial_refresh_rejects_conflicting_posted_item_decision_proof(item_workflow_source):
    run_id, _, _ = _prepared(item_workflow_source)
    _post(run_id)
    proof = db.session.query(AccountingEvidence).filter_by(kind='amazon_item_allocation').one()
    conflicting = dict(json.loads(proof.payload), decisions=[])
    db.session.add(AccountingEvidence(kind='amazon_item_allocation', sha256=digest(conflicting),
        payload=dump(conflicting), reviewer='fixture conflicting evidence'))
    db.session.commit()
    before = _financial_state()
    with pytest.raises(ValueError, match='one original item-review proof'):
        workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    db.session.rollback()
    assert _financial_state() == before


def test_intervening_allocation_change_invalidates_carried_reviews_even_with_fresh_token(item_workflow_source):
    from pacioli.functions import amazon_evidence as allocation
    old_run, _, _ = _prepared(item_workflow_source)
    _post(old_run)
    refreshed = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    refreshed_id = refreshed.id
    db.session.commit()
    workflow.run_worker(refreshed_id, infer_fn=_fake_infer)
    bodywash = next(row for row in workflow.page(refreshed_id)['rows'] if 'body wash' in row['item']['title'])
    workflow.review_item(bodywash['id'], 'Personal Care', 'Source establishes ordinary personal body wash.', 'fixture owner')
    db.session.commit()
    bundle = db.session.query(AmazonEvidenceBundles).one()
    other = allocation.allocation_preview(bundle.id, item_workflow_source['transaction_id'], [
        dict(category='Books and Publications', amount='10.80'),
        dict(category='Entertainment', amount='21.60'),
    ])
    allocation.revise_allocation(other, 'another reviewer', 'Separately reviewed replacement allocation.', bundle.posting_digest)
    db.session.commit()
    before = _financial_state()
    with pytest.raises(ValueError, match='carried forward'):
        workflow.post_reviewed(refreshed_id, workflow.review_digest(refreshed_id),
                               'fixture owner', 'A new token cannot revive carried approvals from a superseded allocation.')
    db.session.rollback()
    assert _financial_state() == before


def test_fully_posted_baskets_are_not_added_to_a_refresh(item_workflow_source):
    run_id, _, bodywash = _prepared(item_workflow_source)
    workflow.review_item(bodywash, 'Personal Care', 'Ordinary personal body wash.', 'fixture owner')
    db.session.commit()
    _post(run_id)
    before = _financial_state()
    with pytest.raises(ValueError, match='No new unique retail settlements'):
        workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    db.session.rollback()
    assert _financial_state() == before


def test_manual_pending_items_can_resume_qwen_but_actual_holds_and_reviews_do_not(item_workflow_source):
    run = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    run_id = run.id
    for job in workflow._jobs(run_id):
        job.state = 'held'
        assert job.proposal is None
    run.state = 'needs_attention'
    db.session.commit()
    observed = []
    def infer(item, categories):
        observed.append(item['title'])
        return _fake_infer(item, categories)
    before = _financial_state()
    workflow.run_worker(run_id, infer_fn=infer)
    assert sorted(observed) == ['Fixture ordinary body wash', 'Fixture paperback novel']
    workflow.run_worker(run_id, infer_fn=lambda *args: pytest.fail('Real model holds must not automatically rerun'))
    assert _financial_state() == before


def test_exact_product_rules_are_only_suggestions_and_need_no_gpu(item_workflow_source, monkeypatch):
    from pacioli.functions import amazon_suspense, chase_local_model
    run = workflow.create_run(item_workflow_source['archive_id'], 'fixture owner')
    run_id = run.id
    db.session.commit()
    def rule(item, categories):
        assert item['asin'] and item['marketplace'] and item['source_refs']
        category = 'Books and Publications' if 'paperback' in item['title'] else 'Personal Care'
        answer = dict(item_id=item['item_id'], decision='suggest', category=category,
            evidence_refs=['product.title'], policy_refs=[advisor.POLICY_VERSION, 'category:' + category],
            reason='Previously reviewed exact product rule.', missing_fact=None)
        return dict(answer=answer, category=category, validation_errors=[],
                    origin='reviewed_exact_product_rule', source_rule_id=123)
    monkeypatch.setattr(amazon_suspense, 'product_suggestion', rule)
    monkeypatch.setattr(chase_local_model, 'LocalModel', lambda *args, **kwargs: pytest.fail('Rules do not require a GPU'))
    before = _financial_state()
    workflow.run_worker(run_id)
    jobs = workflow._jobs(run_id)
    assert all(job.state == 'suggested' and job.decision_id is None for job in jobs)
    assert all(json.loads(job.proposal)['origin'] == 'reviewed_exact_product_rule' for job in jobs)
    assert _post(run_id)['posted'] == 0
    assert _financial_state() == before


def test_direct_item_review_cannot_bypass_a_curated_property_hold(item_workflow_source):
    from pacioli.functions import amazon_suspense as suspense
    from tests.test_amazon_suspense import _plan
    plan_id, _, _, _ = _plan(item_workflow_source, gate='needs_property')
    held = suspense.page(plan_id)['items'][0]
    before = _financial_state()
    decision_id = db.session.get(AmazonItemJobs, held['job_id']).decision_id
    with pytest.raises(ValueError, match='accounting-policy treatment'):
        workflow.review_item(held['job_id'], 'Personal Care',
            'A category chosen on the old item page must not override a controller hold.', 'fixture owner')
    db.session.rollback()
    assert db.session.get(AmazonItemJobs, held['job_id']).decision_id == decision_id
    assert _financial_state() == before


def test_fact_correction_started_before_queue_invalidates_frozen_financial_approval(item_workflow_source, monkeypatch):
    from pacioli.functions import amazon_suspense as suspense, amazon_item_posting as posting
    from tests.test_amazon_suspense import _plan, _review
    plan_id, doc, _, _ = _plan(item_workflow_source, gate='needs_fact')
    item_id = doc['items'][0]['item_id']
    suspense.save_fact(plan_id, item_id, 'Original owner-confirmed ordinary use.',
                       suspense.page(plan_id)['token'], 'fixture owner')
    db.session.commit()
    reviewed = _review(plan_id, [item_id])
    run_id = reviewed['run_id']
    job = db.session.query(AmazonItemJobs).filter_by(run_id=run_id, item_id=item_id).one()
    original = workflow._decision(job)
    assert original['suspense_control']['fact_evidence_id'] is not None
    approved = workflow.review_digest(run_id)
    # An already-started fact transaction can legitimately pass the idle check
    # before another session queues posting; its later commit must stale that
    # request, even though no item decision ID itself changed.
    correction = Session(bind=db.engine)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, 'session', correction)
            suspense.save_fact(plan_id, item_id, 'Corrected owner evidence requires fresh classification review.',
                               suspense.page(plan_id)['token'], 'second reviewer')
        queued = posting.create_request(run_id, approved, 'fixture owner', 'Approve the original reviewed source fact.')
        queued_id = queued.id
        db.session.commit()
        correction.commit()
    finally:
        correction.close()
    assert workflow.review_digest(run_id) != approved
    before = _financial_state()
    with pytest.raises(ValueError, match='changed'):
        posting.run_worker(queued_id)
    assert posting.status(queued_id)['state'] == 'failed'
    assert _financial_state() == before
    # Merely fetching a newer token is not an independent review of the new
    # fact. The immutable decision must also be rebound explicitly.
    with pytest.raises(ValueError, match='source fact changed'):
        workflow.post_reviewed(run_id, workflow.review_digest(run_id),
                               'fixture owner', 'A fresh request cannot reuse stale fact evidence.')
    db.session.rollback()
    assert _financial_state() == before
