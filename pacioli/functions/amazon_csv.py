"""Lossless Amazon CSV evidence intake and spreadsheet-safe accounting exports.

Provider rows are evidence, not proof that an order total is a bank settlement.
Preserve duplicates and original bytes; journal allocation remains explicit.
"""
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
import zipfile

from sqlalchemy import text
from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonEvidenceBundles, JournalEntries
from pacioli.functions.reconciliation import dump, evidence

MAX_UPLOAD = 64 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
MAX_ROWS = 100000
ZIP_READ_CHUNK = 1024 * 1024


def _key(value):
    return re.sub('[^a-z0-9]', '', value.casefold())


def _table(raw):
    try:
        decoded = raw.decode('utf-8-sig')
        if '\x00' in decoded:
            raise ValueError('CSV contains NUL characters')
        reader = csv.reader(io.StringIO(decoded, newline=''), strict=True)
        headers = next(reader)
        normalized = [_key(h) for h in headers]
        if not headers or len(headers) > 250 or any(not h for h in normalized) or len(set(normalized)) != len(headers):
            raise ValueError('CSV headers must be nonempty and distinct')
        # A native export also includes companion tables, such as digital
        # borrowing and delivery photos, without an order identity. Preserve
        # those tables without interpreting their rows as purchases.
        identity = normalized.index('orderid') if 'orderid' in normalized else None
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError('CSV row width does not match its header')
            if any(len(cell) > 100000 for cell in row):
                raise ValueError('CSV cell exceeds the evidence limit')
            if identity is not None and not row[identity].strip():
                raise ValueError('Order CSV row has no Order ID')
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValueError('CSV exceeds the row limit')
        return dict(headers=headers, rows=rows,
                    order_id_column=headers[identity] if identity is not None else None,
                    source_sha256=hashlib.sha256(raw).hexdigest(), source_bytes=len(raw))
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise ValueError('Amazon CSV must be valid UTF-8 with a header') from exc


def parse_upload(raw, filename):
    if not raw or len(raw) > MAX_UPLOAD:
        raise ValueError('Choose a nonempty Amazon CSV or ZIP no larger than {} MiB'
                         .format(MAX_UPLOAD // (1024 * 1024)))
    tables, ignored = [], []
    if filename.casefold().endswith('.csv'):
        table = _table(raw)
        tables.append((PurePosixPath(filename).name, table))
    elif filename.casefold().endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                if len(members) > 1000 or sum(m.file_size for m in members) > MAX_UNPACKED:
                    raise ValueError('Archive exceeds the member or expanded-size limit')
                names = set()
                expanded = row_count = 0
                for member in members:
                    path = PurePosixPath(member.filename)
                    if (path.is_absolute() or '..' in path.parts or '\\' in member.filename or
                            member.filename in names or stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1):
                        raise ValueError('Archive has an unsafe, duplicate or encrypted member')
                    names.add(member.filename)
                    if member.is_dir():
                        if member.file_size:
                            raise ValueError('Archive directory contains file data')
                        continue
                    is_csv = member.filename.casefold().endswith('.csv')
                    parts = []
                    member_bytes = 0
                    with archive.open(member) as source:
                        # Read every file to EOF so zipfile validates its CRC,
                        # including photos and notes retained only in the exact
                        # original archive. Non-CSV bytes are never accumulated
                        # or extracted to the filesystem.
                        while True:
                            chunk = source.read(min(ZIP_READ_CHUNK, MAX_UNPACKED - expanded + 1))
                            if not chunk:
                                break
                            member_bytes += len(chunk)
                            expanded += len(chunk)
                            if member_bytes > member.file_size or expanded > MAX_UNPACKED:
                                raise ValueError('Archive member exceeds its declared size or expanded-size limit')
                            if is_csv:
                                parts.append(chunk)
                    if member_bytes != member.file_size:
                        raise ValueError('Archive member exceeds its declared size')
                    if not is_csv:
                        ignored.append(member.filename)
                    else:
                        table = _table(b''.join(parts))
                        row_count += len(table['rows'])
                        if row_count > MAX_ROWS:
                            raise ValueError('Archive exceeds the combined row limit')
                        tables.append((member.filename, table))
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
            raise ValueError('Amazon ZIP archive is invalid or unsupported') from exc
    else:
        raise ValueError('Choose Amazon’s original CSV or ZIP export')
    if not tables:
        raise ValueError('No CSV tables found; the export layout needs review')
    if sum(len(table['rows']) for _, table in tables) > MAX_ROWS:
        raise ValueError('Archive exceeds the combined row limit')
    return tables, ignored


def import_archive(raw, filename, reviewer):
    """Stage immutable encrypted snapshots; caller commits atomically.

    Repeated titles and even identical source rows retain their multiplicity.
    A later export is a new snapshot, never silently merged into the old one.
    """
    sha = hashlib.sha256(raw).hexdigest()
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_csv_import'))"))
    for old in db.session.query(AccountingEvidence).filter_by(kind='amazon_csv_import'):
        if json.loads(old.payload)['source_sha256'] == sha:
            return old, False
    tables, ignored = parse_upload(raw, filename)
    source = evidence('amazon_csv_source', dict(sha256=sha, base64=base64.b64encode(raw).decode('ascii')), reviewer)
    saved = []
    for name, table in tables:
        doc = evidence('amazon_csv_table', table, reviewer)
        order_column = table['order_id_column']
        index = table['headers'].index(order_column) if order_column is not None else None
        saved.append(dict(filename=name, evidence_id=doc.id, rows=len(table['rows']),
                          orders=len({row[index] for row in table['rows']}) if index is not None else None,
                          order_id_column=order_column, columns=table['headers'],
                          source_sha256=table['source_sha256'], source_bytes=table['source_bytes']))
    manifest = evidence('amazon_csv_import', dict(source_id=source.id, source_sha256=sha,
        filename=PurePosixPath(filename).name, tables=saved, unparsed_members=ignored,
        state='source_evidence_saved'), reviewer)
    return manifest, True


def spreadsheet_cell(value):
    """Text from external receipts must not become spreadsheet formulas."""
    value = '' if value is None else str(value)
    if (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))
            or re.fullmatch(r'\d{15,}',value)):
        return "'" + value
    return value


def csv_bytes(headers, rows, numeric=()):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([spreadsheet_cell(h) for h in headers])
    numeric = set(numeric)
    for row in rows:
        writer.writerow([str(value) if key in numeric and value is not None else spreadsheet_cell(value)
                         for key, value in zip(headers, row)])
    return output.getvalue().encode('utf-8-sig')


def source_table_csv(identifier):
    doc = db.session.get(AccountingEvidence, identifier)
    if not doc or doc.kind != 'amazon_csv_table':
        raise ValueError('Select an imported Amazon CSV table')
    table = json.loads(doc.payload)
    # Every source column is untrusted text; this is a viewing/export copy.
    return csv_bytes(table['headers'], table['rows'])


def original_archive(identifier):
    doc = db.session.get(AccountingEvidence, identifier)
    if not doc or doc.kind != 'amazon_csv_import':
        raise ValueError('Select an Amazon archive import')
    manifest = json.loads(doc.payload)
    source = db.session.get(AccountingEvidence, manifest['source_id'])
    content = base64.b64decode(json.loads(source.payload)['base64'], validate=True)
    if hashlib.sha256(content).hexdigest() != manifest['source_sha256']:
        raise ValueError('Stored archive hash mismatch')
    return content, manifest['filename'].casefold().endswith('.zip')


def accounting_exports():
    """Export every saved record, not the page's 100-row display window."""
    bundles = db.session.query(AmazonEvidenceBundles).order_by(AmazonEvidenceBundles.id).all()
    allocated_orders = {line['order_id'] for b in bundles if b.transaction_id
                        for line in json.loads(b.details)['lines']}
    receipt_rows = []
    for doc in db.session.query(AccountingEvidence).filter_by(kind='amazon_receipt').order_by(AccountingEvidence.id):
        body = json.loads(doc.payload)
        # A receipt can support multiple shipments; do not label an entire order
        # fully reconciled merely because one bundle is linked.
        receipt_rows.append([doc.id, body['order_id'], doc.created_at, doc.sha256,
                             'has_booked_bundle' if body['order_id'] in allocated_orders else 'needs_review', body['text']])
    item_rows, allocation_rows = [], []
    for bundle in bundles:
        body = json.loads(bundle.details)
        for line in body['lines']:
            item_rows.append([body['bundle_id'], line['line_id'], line['order_id'], line['date'],
                line['kind'], line['title'], line['amount'], line['currency'],
                bundle.transaction_id or '', 'booked' if bundle.transaction_id else 'unbooked'])
        if bundle.transaction_id:
            entries = db.session.query(JournalEntries).filter_by(transaction_source='ofx',transaction_id=bundle.transaction_id).order_by(JournalEntries.allocation_key)
            for entry in entries:
                allocation_rows.append([bundle.external_id, entry.id, entry.transaction_id, entry.allocation_key,
                    entry.timestamp, entry.debit_subaccount, entry.credit_subaccount,
                    entry.functional_amount, entry.source_currency])
    return {
        'receipts.csv': csv_bytes(['evidence_id','order_id','captured_at','sha256','review_status','receipt_text'],receipt_rows),
        'verified_items.csv': csv_bytes(['bundle_id','line_id','order_id','date','kind','title','amount','currency','bank_transaction_id','status'],item_rows,numeric=('amount',)),
        'journal_allocations.csv': csv_bytes(['bundle_id','journal_id','bank_transaction_id','allocation_key','timestamp','debit','credit','amount','currency'],allocation_rows,numeric=('amount',)),
    }


def export_zip():
    files = accounting_exports()
    source_tables=[]
    for doc in db.session.query(AccountingEvidence).filter_by(kind='amazon_csv_table').order_by(AccountingEvidence.id):
        table=json.loads(doc.payload)
        name='source_tables/{}.csv'.format(doc.id)
        files[name]=csv_bytes(table['headers'],table['rows'])
        source_tables.append(dict(file=name,evidence_id=doc.id,sha256=doc.sha256,rows=len(table['rows'])))
    imports=[dict(evidence_id=d.id,**json.loads(d.payload)) for d in db.session.query(AccountingEvidence)
             .filter_by(kind='amazon_csv_import').order_by(AccountingEvidence.id)]
    manifest=dict(generated_at=datetime.now(timezone.utc).isoformat(),
        coverage='All saved evidence; complete Amazon account history is not asserted.',
        source_tables=source_tables,source_imports=imports,
        rows={name:sum(1 for _ in csv.reader(io.StringIO(content.decode('utf-8-sig'))))-1
              for name,content in files.items()})
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,content in files.items():
            archive.writestr(name,content)
        archive.writestr('manifest.json',dump(manifest))
        archive.writestr('README.txt',
            'Pacioli Amazon evidence export. CSV files contain all saved rows, not only the visible page.\n'
            'receipts.csv includes the original visible receipt text, which may contain shipping details.\n'
            'A receipt is evidence, not a new cash posting or a guarantee of complete historical coverage.\n'
            'verified_items.csv contains reviewed shipment/refund lines; journal_allocations.csv contains booked entries.\n'
            'source_tables/ contains each distinct imported provider table snapshot, when available.\n'
            'Overlapping snapshots must not be summed as independent purchases. manifest.json records their provenance.\n'
            'Bank transaction identifiers are text; preserve them as text when opening a spreadsheet.\n'
            'Potential spreadsheet formulas in source text are prefixed with an apostrophe. These report CSVs\n'
            'are for review/export, not the eight-column canonical import format.\n')
    return output.getvalue()
