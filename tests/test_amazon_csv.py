"""CSV evidence must preserve multiplicity and never double-book bank movements."""
import csv
from datetime import datetime, timezone
import io
import hashlib
import json
import stat
import zipfile

import pytest
from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonEvidenceBundles, BankFeedAccounts, JournalEntries
from pacioli.functions import amazon_csv as a
from pacioli.functions.reconciliation import evidence, dump
from pacioli.functions.journal_posting import post_pair
from sqlalchemy import text


ROWS = b'Order ID,Product Name,Quantity,Total Owed\r\n123-1234567-1234567,Same item,1,10.00\r\n123-1234567-1234567,Same item,1,10.00\r\n'


def archive(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in files:z.writestr(name,value)
    return stream.getvalue()


def test_archive_preserves_duplicate_rows_bytes_encryption_and_replay(clean_ledger):
    raw=archive([('Retail.OrderHistory/part1.csv',ROWS),
        ('Retail.Refunds/refunds.csv',b'Order ID,Refund Amount\n123-1234567-1234567,-10.00\n'),
        ('Metadata.csv',b'Field,Value\nExport,Example\n'),('README.txt',b'Export notes')])
    doc,created=a.import_archive(raw,'Orders.zip','owner');db.session.commit()
    assert created
    manifest=json.loads(doc.payload)
    assert [t['rows'] for t in manifest['tables']]==[2,1,1]
    assert set(manifest['unparsed_members'])=={'README.txt'}
    assert manifest['tables'][2]['orders'] is None
    assert manifest['tables'][0]['source_sha256']==hashlib.sha256(ROWS).hexdigest()
    assert manifest['tables'][0]['source_bytes']==len(ROWS)
    assert manifest['tables'][0]['order_id_column']=='Order ID'
    table=db.session.get(AccountingEvidence, manifest['tables'][0]['evidence_id'])
    assert len(json.loads(table.payload)['rows'])==2
    count=db.session.query(AccountingEvidence).count()
    same,created=a.import_archive(raw,'renamed.zip','owner');db.session.commit()
    assert not created and same.id==doc.id and db.session.query(AccountingEvidence).count()==count
    assert a.original_archive(doc.id)==(raw,True)
    with zipfile.ZipFile(io.BytesIO(a.export_zip())) as z:
        snapshot_names=[name for name in z.namelist() if name.startswith('source_tables/')]
        assert len(snapshot_names)==3
        assert json.loads(z.read('manifest.json'))['source_imports'][0]['source_sha256']==manifest['source_sha256']
    stored=db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence')).scalars().all()
    assert all(s.startswith('fernet:') and 'Same item' not in s for s in stored)
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries')).scalar()==0
    refund_table=manifest['tables'][1]['evidence_id']
    assert list(csv.reader(io.StringIO(a.source_table_csv(refund_table).decode('utf-8-sig'))))[1][1]=="'-10.00"


def test_new_export_is_a_snapshot_not_an_overwrite_or_posting(clean_ledger):
    original,_=a.import_archive(ROWS,'Orders.csv','owner');db.session.commit()
    updated,_=a.import_archive(ROWS.replace(b'1,10.00',b'2,20.00'),'Orders.csv','owner');db.session.commit()
    assert original.id!=updated.id
    assert a.original_archive(original.id)==(ROWS,False)
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries')).scalar()==0


@pytest.mark.parametrize('bad',[
 b'Order ID,OrderID\na,b\n', b'Order ID,Title\na,b,extra\n', b'Order ID,Title\na\n',
 b'Order ID,Title\n,missing identity\n', b'Order ID,Title\na,"unterminated',
 b'Order ID,Title\na,\x00\n', b'Order ID,Title\na,\xff\n', b'',
])
def test_rejects_unusable_csv_without_partial_import(clean_ledger,bad):
    raw=archive([('good.csv',ROWS),('bad.csv',bad)]) if bad else bad
    with pytest.raises(ValueError):a.import_archive(raw,'Orders.zip' if raw.startswith(b'PK') else 'Orders.csv','owner')
    db.session.rollback()
    assert db.session.query(AccountingEvidence).count()==0


def test_archive_limits_and_unsafe_members_fail_closed(monkeypatch):
    for names in [[('../escape.csv',ROWS)],[('/absolute.csv',ROWS)],[('a.csv',ROWS),('a.csv',ROWS)]]:
        with pytest.raises(ValueError):a.parse_upload(archive(names),'Orders.zip')
    link=zipfile.ZipInfo('link.csv');link.external_attr=(stat.S_IFLNK|0o777)<<16
    with pytest.raises(ValueError):a.parse_upload(archive([(link,ROWS)]),'Orders.zip')
    with pytest.raises(ValueError):a.parse_upload(b'not a zip','Orders.zip')
    monkeypatch.setattr(a,'MAX_UNPACKED',len(ROWS)-1)
    with pytest.raises(ValueError):a.parse_upload(archive([('orders.csv',ROWS)]),'Orders.zip')
    monkeypatch.setattr(a,'MAX_UPLOAD',10)
    with pytest.raises(ValueError):a.parse_upload(ROWS,'Orders.csv')


def test_native_companions_and_repeated_digital_components_are_preserved(clean_ledger):
    companions = [
        ('Cart History.csv', b'ASIN,Date Added to Cart,Order Quantity\nexample,2026-09-01,1\n'),
        ('Digital Borrowed Items.csv', b'ASIN,Loan Creation Date,Loan Status\nexample,2020-01-01,Returned\n'),
        ('Delivery Photos.csv', b'Photograph ID,Photographed Date\nphoto-1,2026-09-01\n'),
        ('FileDescriptions.csv', b'File name,Description\nOrder History.csv,Retail orders\n'),
        ('Empty.csv', b'Field,Value\n'),
    ]
    digital = (b'Order ID,Digital Order Item ID,Component Type,Price,Transaction Amount\n'
               b'D01,item-1,Price Amount,10.00,10.00\n'
               b'D01,item-1,Tax,10.00,0.83\n'
               b'D01,item-1,Price Amount,10.00,-1.00\n')
    doc, _ = a.import_archive(archive(companions + [('Digital Content Orders.csv', digital)]),
                              'Your Orders.zip', 'owner')
    manifest = json.loads(doc.payload)
    assert [table['orders'] for table in manifest['tables']] == [None] * 5 + [1]
    assert [table['rows'] for table in manifest['tables']] == [1, 1, 1, 1, 0, 3]
    assert all(table['order_id_column'] is None for table in manifest['tables'][:5])
    saved = json.loads(db.session.get(AccountingEvidence, manifest['tables'][-1]['evidence_id']).payload)
    assert saved['rows'] == list(csv.reader(io.StringIO(digital.decode())))[1:]
    assert db.session.query(AmazonEvidenceBundles).count() == 0
    assert db.session.query(JournalEntries).count() == 0
    standalone, _ = a.import_archive(companions[3][1], 'FileDescriptions.csv', 'owner')
    assert json.loads(standalone.payload)['tables'][0]['orders'] is None


def test_companions_count_toward_combined_row_limit(monkeypatch):
    monkeypatch.setattr(a, 'MAX_ROWS', 2)
    with pytest.raises(ValueError, match='combined row limit'):
        a.parse_upload(archive([('orders.csv', ROWS), ('companion.csv', b'Field\nvalue\n')]), 'Orders.zip')


def test_corrupt_photo_rejects_whole_archive(clean_ledger):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('orders.csv', ROWS)
        z.writestr('media/delivery.jpeg', b'photo bytes retained only as source evidence')
    raw = bytearray(stream.getvalue())
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        member = z.getinfo('media/delivery.jpeg')
        offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
    raw[offset] ^= 1
    with pytest.raises(ValueError, match='invalid or unsupported'):
        a.import_archive(bytes(raw), 'Orders.zip', 'owner')
    assert db.session.query(AccountingEvidence).count() == 0


def test_native_photo_archive_above_old_limit_uploads_atomically(clean_ledger, client, admin_user, app):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('Order History.csv', ROWS)
        z.writestr('media/delivery.jpeg', b'x' * (13 * 1024 * 1024))
    raw = stream.getvalue()
    assert 12 * 1024 * 1024 < len(raw) < a.MAX_UPLOAD < app.config['MAX_CONTENT_LENGTH']
    client.post('/login/', data=admin_user)
    response = client.post('/reconciliation/amazon/', data={
        'action': 'import_archive', 'archive': (io.BytesIO(raw), 'Your Orders.zip')})
    assert response.status_code == 302
    doc = db.session.query(AccountingEvidence).filter_by(kind='amazon_csv_import').one()
    assert a.original_archive(doc.id) == (raw, True)
    assert json.loads(doc.payload)['unparsed_members'] == ['media/delivery.jpeg']
    assert db.session.query(JournalEntries).count() == 0


def test_exports_all_receipts_repeated_lines_and_existing_journals(clean_ledger):
    for i in range(105):
        evidence('amazon_receipt',dict(order_id=str(i),text='=DANGEROUS()\nReceipt '+str(i)), 'owner')
    lines=[dict(bundle_id='shipment',line_id='item:'+str(i),order_id='0',date='2026-01-01',kind='item',
                title='Same item',amount='10.00',currency='USD') for i in range(2)]
    proof=evidence('amazon_items',dict(bundle_id='shipment',lines=lines),'owner')
    from sqlalchemy import text
    account=db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) VALUES('bankacctfrom','Chase Checking') RETURNING id")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account,currency='USD'))
    fitid='12345678901234567890'
    db.session.execute(text("INSERT INTO ofx.stmttrn(fitid,acctfrom_id,trntype,dtposted,trnamt,name) VALUES(:id,:account,'OTHER','2026-01-01T00:00:00Z',-20,'AMAZON')"),{'id':fitid,'account':account})
    from pacioli.functions.source_identity import source_identity
    transaction=source_identity(db.session, account, fitid)
    db.session.add(AmazonEvidenceBundles(external_id='shipment',evidence_id=proof.id,
                   details=dump(dict(bundle_id='shipment',lines=lines)),transaction_id=transaction))
    post_pair(timestamp=datetime(2026,1,1,tzinfo=timezone.utc),debit='Groceries',credit='Chase Checking',
              functional_amount='20.00',transaction_source='ofx',transaction_id=transaction,allocation_key='amazon:000')
    db.session.commit()
    before=db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries')).scalar()
    files=a.accounting_exports()
    receipts=list(csv.DictReader(io.StringIO(files['receipts.csv'].decode('utf-8-sig'))))
    assert len(receipts)==105 and receipts[0]['receipt_text'].startswith("'=DANGEROUS")
    assert receipts[0]['review_status']=='has_booked_bundle' and receipts[1]['review_status']=='needs_review'
    items=list(csv.DictReader(io.StringIO(files['verified_items.csv'].decode('utf-8-sig'))))
    assert len(items)==2 and items[0]['line_id']!=items[1]['line_id']
    allocations=list(csv.DictReader(io.StringIO(files['journal_allocations.csv'].decode('utf-8-sig'))))
    assert len(allocations)==1 and allocations[0]['amount']=='20.00'
    assert allocations[0]['bank_transaction_id']==transaction
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries')).scalar()==before
    with zipfile.ZipFile(io.BytesIO(a.export_zip())) as z:
        assert set(z.namelist())==set(files)|{'README.txt','manifest.json'}
        assert json.loads(z.read('manifest.json'))['rows']['receipts.csv']==105
        assert z.read('receipts.csv')==files['receipts.csv']
    db.session.query(JournalEntries).filter_by(transaction_source='ofx', transaction_id=transaction).delete()
    db.session.execute(text('DELETE FROM ofx.stmttrn WHERE acctfrom_id=:account'),{'account':account})
    db.session.query(BankFeedAccounts).filter_by(ofx_account_id=account).delete()
    db.session.execute(text('DELETE FROM ofx.acctfrom WHERE id=:account'),{'account':account})
    db.session.commit()


def test_downloads_and_archive_upload_require_authentication_and_csrf(clean_ledger,client,admin_user,app):
    assert client.get('/reconciliation/amazon/export/all.zip/').status_code==302
    assert client.get('/reconciliation/amazon/table/1/').status_code==302
    assert client.get('/reconciliation/amazon/source/1/').status_code==302
    client.post('/login/',data=admin_user)
    response=client.post('/reconciliation/amazon/',data={'action':'import_archive','archive':(io.BytesIO(ROWS),'Orders.csv')})
    assert response.status_code==302
    doc=db.session.query(AccountingEvidence).filter_by(kind='amazon_csv_import').one()
    table=json.loads(doc.payload)['tables'][0]['evidence_id']
    for path in ['export/all.zip/','export/receipts.csv/','table/'+str(table)+'/', 'source/'+str(doc.id)+'/']:
        response=client.get('/reconciliation/amazon/'+path)
        assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
        assert response.headers['Content-Disposition'].startswith('attachment;')
    assert client.get('/reconciliation/amazon/table/'+str(doc.id)+'/').status_code==404
    assert client.get('/reconciliation/amazon/export/unknown/').status_code==404
    app.config['WTF_CSRF_ENABLED']=True
    try:assert client.post('/reconciliation/amazon/',data={'action':'import_archive'}).status_code==400
    finally:app.config['WTF_CSRF_ENABLED']=False
