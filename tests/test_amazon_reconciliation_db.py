"""PostgreSQL provenance, snapshot and access controls for Amazon review."""
import csv
from datetime import datetime, timezone
import io
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonEvidenceBundles, BankFeedAccounts, JournalEntries
from pacioli.functions import amazon_csv, amazon_evidence
from pacioli.functions import amazon_reconciliation as review
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.reconciliation import digest, dump
from pacioli.functions.source_identity import source_identity


ORDER_ID = '123-1234567-1234567'


def retail(**overrides):
    row = {
        'Order ID': ORDER_ID, 'Order Status': 'Closed', 'Shipment Status': 'Shipped',
        'Currency': 'USD', 'Order Date': '2026-09-01T12:00:00Z',
        'Ship Date': '2026-09-03T00:30:00Z',
        'Carrier Name & Tracking Number': 'Carrier: synthetic-tracking-1',
        'Original Quantity': '1', 'Product Name': 'Synthetic household item',
        'Unit Price': '10.00', 'Unit Price Tax': '0.00', 'Shipping Charge': '0.00',
        'Total Discounts': '0.00', 'Total Amount': '10.00',
        'Shipment Item Subtotal': '10.00', 'Shipment Item Subtotal Tax': '0.00',
        'Payment Method Type': 'Visa - 7331',
    }
    row.update(overrides)
    return row


def archive(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return amazon_csv.import_archive(stream.getvalue().encode(), 'Order History.csv', 'fixture owner')[0]


@pytest.fixture
def amazon_source(clean_ledger):
    # Display name deliberately has no card suffix: only the actual OFX account
    # identifier can establish the source account for the native export.
    account_id = db.session.execute(text("""
        INSERT INTO ofx.acctfrom(subclass,name)
        VALUES('ccacctfrom','Credit Card') RETURNING id
    """)).scalar()
    db.session.execute(text('INSERT INTO ofx.ccacctfrom(id,acctid) VALUES(:id,:acctid)'),
                       {'id': account_id, 'acctid': uuid4().hex[:12] + '7331'})
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    fitid = uuid4().hex
    when = datetime(2026, 9, 3, 12, 34, 56, tzinfo=timezone.utc)
    db.session.execute(text('''
        INSERT INTO ofx.stmttrn(fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES(:fitid,:account_id,'DEBIT',:when,-10.00,'AMAZON RETAIL')
    '''), dict(fitid=fitid, account_id=account_id, when=when))
    transaction_id = source_identity(db.session, account_id, fitid)
    journal = post_pair(timestamp=when, debit='Bookkeeping Suspense', credit='Credit Card',
                        functional_amount='10.00', transaction_source='ofx',
                        transaction_id=transaction_id)
    source = archive([retail()])
    db.session.flush()
    return dict(account_id=account_id, transaction_id=transaction_id,
                journal_id=journal.id, archive_id=source.id)


def financial_fingerprints():
    return {table: digest([dict(row._mapping) for row in db.session.execute(text(
        'SELECT * FROM ' + table + ' ORDER BY 1'))]) for table in (
            'bookkeeping.journal_entries', 'bookkeeping.journal_entry_events',
            'bookkeeping.ofx_source_identities', 'bookkeeping.ofx_journal_sources',
            'ofx.stmttrn', 'admin.mappings', 'amazon.evidence_bundles')}


def test_amazon_review_database_account_identity_utc_and_read_only_result(amazon_source):
    before = financial_fingerprints()
    source_date = db.session.execute(text('SELECT date FROM ofx.transactions WHERE id=:id'),
                                     {'id': amazon_source['transaction_id']}).scalar()
    journal = db.session.get(JournalEntries, amazon_source['journal_id'])
    assert source_date.tzinfo is None
    assert journal.timestamp.tzinfo is not None
    result = review.build_report(db.session, amazon_source['archive_id'])
    record, = result['records']
    assert record['status'] == 'unique_candidate'
    candidate, = record['candidates']
    assert candidate['id'] == amazon_source['transaction_id']
    assert candidate['card_last4'] == '7331'
    assert candidate['account'] == 'Credit Card'
    assert candidate['journal_state'] == 'suspense'
    assert financial_fingerprints() == before


def test_amazon_review_saved_snapshot_is_read_only_encrypted_and_replay_safe(amazon_source, monkeypatch):
    db.session.commit()
    before = financial_fingerprints()
    original = review.build_report
    settings = []

    def inspect_snapshot(session, identifier):
        settings.append(tuple(session.execute(text("""
            SELECT current_setting('transaction_read_only'),
                   current_setting('transaction_isolation')
        """)).one()))
        return original(session, identifier)

    monkeypatch.setattr(review, 'build_report', inspect_snapshot)
    saved = review.build_and_save(amazon_source['archive_id'], 'fixture owner')
    identifier = saved.id
    db.session.commit()
    count = db.session.query(AccountingEvidence).count()
    repeated = review.build_and_save(amazon_source['archive_id'], 'different reviewer')
    db.session.commit()
    assert repeated.id == identifier
    assert db.session.query(AccountingEvidence).count() == count
    assert settings == [('on', 'repeatable read'), ('on', 'repeatable read')]
    raw = db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence WHERE id=:id'),
                             {'id': identifier}).scalar()
    assert raw.startswith('fernet:') and 'Synthetic household item' not in raw
    assert financial_fingerprints() == before


def test_amazon_review_missing_bank_currency_cannot_become_candidate(amazon_source):
    db.session.delete(db.session.get(BankFeedAccounts, amazon_source['account_id']))
    db.session.flush()
    result = review.build_report(db.session, amazon_source['archive_id'])
    assert result['records'][0]['status'] == 'needs_evidence'


def test_amazon_review_manifest_and_table_tampering_are_rejected(amazon_source):
    doc = db.session.get(AccountingEvidence, amazon_source['archive_id'])
    manifest = json.loads(doc.payload)
    original = doc.payload
    doc.payload = dump(dict(manifest, filename='changed.csv'))
    # Persisted evidence is immutable. Simulate a damaged decoded/cache value
    # without trying to defeat that independent PostgreSQL write protection.
    with db.session.no_autoflush:
        with pytest.raises(ValueError, match='manifest'):
            review.build_report(db.session, doc.id)
    doc.payload = original
    table = db.session.get(AccountingEvidence, manifest['tables'][0]['evidence_id'])
    contents = json.loads(table.payload)
    contents['rows'][0][contents['headers'].index('Total Amount')] = '999.00'
    table.payload = dump(contents)
    with db.session.no_autoflush:
        with pytest.raises(ValueError, match='source table'):
            review.build_report(db.session, doc.id)


def book_source(source):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(amazon_evidence.FIELDS)
    writer.writerow(['fixture-bundle', 'item-1', ORDER_ID, '2026-09-03',
                     'item', 'Synthetic household item', '10.00', 'USD'])
    bundle = amazon_evidence.import_items_csv(stream.getvalue().encode(), 'fixture owner')[0]
    preview = amazon_evidence.allocation_preview(bundle.id, source['transaction_id'],
                                                [dict(category='Home Maintenance', amount='10.00')])
    amazon_evidence.book_allocation(preview, 'fixture owner', 'Verified synthetic settlement')
    db.session.commit()
    return bundle.id


@pytest.mark.parametrize('drift', ['category', 'bundle', 'proof', 'posting_digest', 'source_description', 'source_timestamp'])
def test_amazon_review_existing_allocation_requires_unchanged_evidence(amazon_source, drift):
    bundle_id = book_source(amazon_source)
    baseline = review.build_report(db.session, amazon_source['archive_id'])
    assert baseline['records'][0]['status'] == 'already_booked'
    if drift == 'category':
        journal = db.session.query(JournalEntries).filter_by(
            transaction_source='ofx', transaction_id=amazon_source['transaction_id']).one()
        journal.debit_subaccount = 'Restaurants'
    elif drift == 'bundle':
        bundle = db.session.get(AmazonEvidenceBundles, bundle_id)
        body = json.loads(bundle.details)
        body['lines'][0]['title'] = 'Different unsupported purpose'
        bundle.details = dump(body)
    elif drift == 'proof':
        proof = db.session.query(AccountingEvidence).filter_by(kind='amazon_allocation').one()
        body = json.loads(proof.payload)
        body['preview']['allocations'][0]['category'] = 'Restaurants'
        proof.payload = dump(body)
    elif drift in ('source_description', 'source_timestamp'):
        if drift == 'source_description':
            db.session.execute(text("UPDATE ofx.stmttrn SET name='AMAZON DIFFERENT SOURCE' WHERE acctfrom_id=:aid"),
                               {'aid': amazon_source['account_id']})
        else:
            db.session.execute(text("UPDATE ofx.stmttrn SET dtposted=dtposted+interval '1 hour' WHERE acctfrom_id=:aid"),
                               {'aid': amazon_source['account_id']})
            journal = db.session.query(JournalEntries).filter_by(
                transaction_source='ofx', transaction_id=amazon_source['transaction_id']).one()
            from datetime import timedelta
            journal.timestamp += timedelta(hours=1)
    else:
        db.session.get(AmazonEvidenceBundles, bundle_id).posting_digest = '0' * 64
    if drift != 'proof':
        db.session.flush()
    # SQLAlchemy 2 autoflushes before text queries too; keep the tampered proof unflushed.
    with db.session.no_autoflush:
        after = financial_fingerprints()
        result = review.build_report(db.session, amazon_source['archive_id'])
        assert result['records'][0]['status'] == 'needs_evidence'
        assert result['records'][0]['candidates'][0]['existing_bundle_id'] == bundle_id
        assert financial_fingerprints() == after


def test_amazon_review_routes_filter_csv_authentication_and_csrf(
        amazon_source, client, admin_user, app, monkeypatch):
    source = archive([retail(), retail(**{
        'Order ID': '123-1234567-7654321', 'Product Name': '=UNTRUSTED()',
        'Unit Price': '20.00', 'Total Amount': '20.00', 'Shipment Item Subtotal': '20.00',
    }), retail(**{'Order ID': '=EXCLUDED()', 'Order Status': 'Cancelled'})])
    archive_id = source.id
    db.session.commit()
    before = financial_fingerprints()
    assert client.get('/reconciliation/amazon/').status_code == 302
    assert client.get('/reconciliation/amazon/reconciliation/1/csv/').status_code == 302
    assert client.post('/reconciliation/amazon/', data={
        'action': 'reconcile_archive', 'archive_id': archive_id}).status_code == 302
    assert not db.session.query(AccountingEvidence).filter_by(kind='amazon_reconciliation').count()
    client.post('/login/', data=admin_user)
    response = client.post('/reconciliation/amazon/', data={
        'action': 'reconcile_archive', 'archive_id': archive_id})
    assert response.status_code == 302
    saved = db.session.query(AccountingEvidence).filter_by(kind='amazon_reconciliation').one()
    base = '/reconciliation/amazon/?reconciliation_id=' + str(saved.id)
    page = client.get(base + '&match_status=unique_candidate')
    assert page.status_code == 200
    assert page.headers['Cache-Control'] == 'no-store'
    assert b'Synthetic household item' in page.data
    assert client.get(base + '&match_status=invalid').status_code == 400
    assert client.get(base + '&match_page=0').status_code == 400
    assert client.get(base + '&match_page=999').status_code == 400
    assert review.review_page(saved.id, status='unique_candidate')['total'] == 1
    assert review.review_page(saved.id, status='unmatched')['total'] == 1
    exported = client.get('/reconciliation/amazon/reconciliation/' + str(saved.id) + '/csv/')
    assert exported.status_code == 200
    assert exported.headers['Cache-Control'] == 'private, no-store'
    assert exported.headers['X-Content-Type-Options'] == 'nosniff'
    rows = list(csv.DictReader(io.StringIO(exported.data.decode('utf-8-sig'))))
    assert len(rows) == 2
    assert {row['Order ID'] for row in rows} == {ORDER_ID, '123-1234567-7654321'}
    csv_path = '/reconciliation/amazon/reconciliation/' + str(saved.id) + '/csv/'
    unmatched = client.get(csv_path + '?part=unmatched_bank')
    assert unmatched.status_code == 200
    assert list(csv.DictReader(io.StringIO(unmatched.data.decode('utf-8-sig')))) == []
    excluded = client.get(csv_path + '?part=exclusions')
    assert excluded.status_code == 200
    excluded_rows = list(csv.DictReader(io.StringIO(excluded.data.decode('utf-8-sig'))))
    assert len(excluded_rows) == 1 and excluded_rows[0]['Order ID'] == "'=EXCLUDED()"
    assert client.get(csv_path + '?part=invalid').status_code == 404
    assert client.get('/reconciliation/amazon/reconciliation/' + str(archive_id) + '/csv/').status_code == 404
    count = db.session.query(AccountingEvidence).filter_by(kind='amazon_reconciliation').count()
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    assert client.post('/reconciliation/amazon/', data={
        'action': 'reconcile_archive', 'archive_id': archive_id}).status_code == 400
    assert db.session.query(AccountingEvidence).filter_by(kind='amazon_reconciliation').count() == count
    assert financial_fingerprints() == before
