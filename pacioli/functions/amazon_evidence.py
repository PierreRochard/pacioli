"""Item evidence allocates the existing bank settlement; never posts cash twice."""
import csv
from datetime import date, datetime, timedelta
import io
import json
import re
from decimal import Decimal

from sqlalchemy import text
from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonEvidenceBundles, JournalEntries
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.reconciliation import (dump, digest, evidence, money, lock_ledger,
                                              require_open, midnight, utc)

FIELDS = ('bundle_id','line_id','order_id','date','kind','title','amount','currency')


def import_items_csv(raw, reviewer):
    """Canonical shipment/refund export. All tax/shipping/discount rows are explicit.

    Stable provider line IDs prevent same-title collapse. Positive amounts are
    purchases; negative amounts are refunds. A bundle is one bank settlement.
    """
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('Item evidence exceeds 5 MiB')
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    if not rows or len(rows) > 10000 or set(rows[0]) != set(FIELDS):
        raise ValueError('Use the documented Amazon evidence CSV columns')
    groups = {}
    seen = set()
    for row in rows:
        if any(not isinstance(row.get(k),str) or not row[k].strip() for k in FIELDS):
            raise ValueError('Every item field is required')
        if any(len(v)>2000 for v in row.values()):
            raise ValueError('Item field is too long')
        identity = (row['bundle_id'], row['line_id'])
        if identity in seen:
            raise ValueError('Duplicate line identity in an evidence file')
        seen.add(identity)
        if row['currency'] != 'USD' or row['kind'] not in ('item','tax','shipping','discount','refund'):
            raise ValueError('Unsupported currency or line kind')
        value = money(row['amount'])
        date.fromisoformat(row['date'])
        if not value or (row['kind'] in ('discount','refund') and value >= 0):
            raise ValueError('Refunds/discounts must be negative; zero lines are not needed')
        row['amount'] = str(value)
        groups.setdefault(row['bundle_id'],[]).append(row)
    imported = []
    for bundle_id, lines in sorted(groups.items()):
        lines.sort(key=lambda r:r['line_id'])
        if len({l['date'] for l in lines}) != 1 or not sum((money(l['amount']) for l in lines),Decimal(0)):
            raise ValueError('Each bundle needs one settlement date and a nonzero total')
        payload = dict(bundle_id=bundle_id, lines=lines)
        old = db.session.query(AmazonEvidenceBundles).filter_by(external_id=bundle_id).one_or_none()
        if old:
            if json.loads(old.details) != payload:
                raise ValueError('Existing item identity has conflicting evidence')
            imported.append(old)
            continue
        doc = evidence('amazon_items', payload, reviewer)
        row = AmazonEvidenceBundles(external_id=bundle_id, evidence_id=doc.id, details=dump(payload))
        db.session.add(row)
        imported.append(row)
    db.session.flush()
    return imported


def candidates(bundle):
    body = json.loads(bundle.details)
    total = sum((money(l['amount']) for l in body['lines']),Decimal(0))
    day = date.fromisoformat(body['lines'][0]['date'])
    return [dict(r) for r in db.session.execute(text('''SELECT id, date, amount, description, account
        FROM ofx.transactions WHERE amount=:amount AND date>=:start AND date<:end
        AND (description ILIKE '%AMAZON%' OR description ILIKE '%AMZN%')
        AND id NOT IN (SELECT transaction_id FROM amazon.evidence_bundles WHERE transaction_id IS NOT NULL)
        ORDER BY date, id'''), dict(amount=-total, start=midnight(day-timedelta(days=7)), end=midnight(day+timedelta(days=8))))]


def _bundle_body(bundle):
    """Mutable bundle pointers cannot replace their immutable source evidence."""
    try:
        body = json.loads(bundle.details)
        document = db.session.query(AccountingEvidence).get(bundle.evidence_id)
        if (not document or document.kind != 'amazon_items' or
                json.loads(document.payload) != body or digest(body) != document.sha256 or
                body['bundle_id'] != bundle.external_id or not body['lines'] or
                any(line['currency'] != 'USD' for line in body['lines'])):
            raise ValueError()
        return body
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Missing or changed immutable item evidence') from exc


def allocation_preview(bundle_id, transaction_id, allocations, *, allow_suspense=False):
    """Reviewer explicitly allocates the all-in charge; no silent proportional tax guess."""
    if not isinstance(allow_suspense, bool):
        raise ValueError('Partial allocation must be explicitly enabled with a boolean')
    bundle = db.session.query(AmazonEvidenceBundles).get(bundle_id)
    if not bundle:
        raise ValueError('Missing item evidence')
    body = _bundle_body(bundle)
    total = sum((money(l['amount']) for l in body['lines']),Decimal(0))
    from pacioli.functions.accounting_report import chart_accounts
    chart = chart_accounts(db.session)
    if not allocations or len(allocations)>100:
        raise ValueError('One to 100 all-in allocations are required')
    normalized = []
    for a in allocations:
        amount = money(a['amount'])
        if (amount <= 0 or chart.get(a['category'],(None,))[0] not in ('Expenses','Assets')
                or a['category'] == 'Transfer Clearing'
                or (a['category'] == 'Bookkeeping Suspense' and not allow_suspense)):
            raise ValueError('Use positive amounts and reviewed expense or asset categories')
        normalized.append(dict(category=a['category'], amount=str(amount)))
    if not any(a['category'] != 'Bookkeeping Suspense' for a in normalized):
        raise ValueError('Partial allocation requires at least one reviewed non-suspense category')
    normalized.sort(key=lambda a:(a['category'],a['amount']))
    if sum((money(a['amount']) for a in normalized),Decimal(0)) != abs(total):
        raise ValueError('Allocations must include all items, tax, shipping and discounts exactly')
    source = db.session.execute(text('SELECT id, date, amount, description, account, account_id FROM ofx.transactions WHERE id=:id'),{'id':transaction_id}).first()
    if not source or source.amount != -total:
        raise ValueError('Item total and signed bank settlement must agree exactly')
    from pacioli.functions.reconciliation import account
    account(source.account_id)
    if abs((source.date.date()-date.fromisoformat(body['lines'][0]['date'])).days)>7:
        raise ValueError('Settlement is outside the seven-day matching window')
    if not any(s in (source.description or '').upper() for s in ('AMAZON','AMZN')):
        raise ValueError('Selected bank transaction is not an Amazon settlement')
    if any(a['category']==source.account for a in normalized):
        raise ValueError('Allocation cannot duplicate the bank account')
    # Existing legacy Amazon item postings would duplicate this evidence.
    orders = list({l['order_id'] for l in body['lines']})
    if db.session.execute(text('''SELECT 1 FROM bookkeeping.journal_entries j JOIN amazon.items i
        ON j.transaction_source='amazon' AND j.transaction_id=i.id::text
        WHERE i.order_id=ANY(:orders) LIMIT 1'''),{'orders':orders}).first():
        raise ValueError('Legacy Amazon journals already exist for this order; reconcile those first')
    payload = dict(bundle_id=bundle_id, evidence_digest=digest(body), source=dict(source, date=utc(source.date)), allocations=normalized)
    if allow_suspense:
        payload['allow_suspense'] = True
    payload = json.loads(dump(payload))
    payload['digest'] = digest(payload)
    return payload


def _revision_preview(preview, revision):
    """Ancestry is part of the digest, preventing A-to-B-to-A stale approvals."""
    if (not isinstance(revision, dict) or set(revision) != {'number', 'previous_posting_digest'}
            or type(revision['number']) is not int or not 2 <= revision['number'] <= 10000
            or not isinstance(revision['previous_posting_digest'], str)
            or not re.fullmatch('[0-9a-f]{64}', revision['previous_posting_digest'])):
        raise ValueError('Invalid allocation revision identity')
    result = {key: value for key, value in preview.items() if key != 'digest'}
    result['revision'] = dict(revision)
    result['digest'] = digest(result)
    return result


def _live_preview(preview):
    if 'allow_suspense' in preview and preview['allow_suspense'] is not True:
        raise ValueError('Partial allocation marker must be explicitly true')
    live = allocation_preview(preview['bundle_id'], preview['source']['id'],
                              preview['allocations'], allow_suspense=preview.get('allow_suspense', False))
    if 'revision' in preview:
        live = _revision_preview(live, preview['revision'])
    if live != preview:
        raise ValueError('Settlement evidence changed since preview')
    return live


def _lock_allocation(preview, reviewer, reason):
    lock_ledger(reviewer, reason)
    # Keep the bank source, currency registration and bundle stable across all
    # checks and journal replacement. Refresh cached ORM objects after waiting
    # for locks so another completed writer cannot leave stale in-memory state.
    db.session.execute(text('LOCK TABLE ofx.stmttrn, ofx.acctfrom, '
                            'bookkeeping.bank_feed_accounts IN SHARE MODE'))
    bundle = db.session.query(AmazonEvidenceBundles).populate_existing().filter_by(
        id=preview['bundle_id']).with_for_update().one_or_none()
    if not bundle:
        raise ValueError('Missing item evidence')
    entries = db.session.query(JournalEntries).populate_existing().filter_by(
        transaction_source='ofx', transaction_id=preview['source']['id']).order_by(
        JournalEntries.allocation_key).all()
    return bundle, entries


def _assert_entries(preview, entries):
    source = preview['source']
    signed = money(source['amount'])
    if not entries or len(entries) != len(preview['allocations']):
        raise ValueError('Posted allocation has drifted')
    for i, (entry, allocation) in enumerate(zip(entries, preview['allocations'])):
        debit, credit = ((allocation['category'], source['account']) if signed < 0
                         else (source['account'], allocation['category']))
        if (entry.allocation_key != 'amazon:{:03d}'.format(i)
                or entry.transaction_source != 'ofx' or entry.transaction_id != source['id']
                or entry.debit_subaccount != debit or entry.credit_subaccount != credit
                or entry.functional_amount != money(allocation['amount'])
                or entry.source_amount != money(allocation['amount'])
                or utc(entry.timestamp) != datetime.fromisoformat(source['date'])
                or entry.source_currency != 'USD' or entry.functional_currency != 'USD'
                or any(getattr(entry, field) is not None for field in
                       ('conversion_rate', 'conversion_source', 'conversion_date'))):
            raise ValueError('Posted allocation has drifted')


def _current_proof(bundle, entries, proof_documents=None):
    """Require immutable proof and its unbroken ancestry, not only a pointer."""
    proofs = {}
    # Internal read-heavy callers may preload immutable evidence once. The
    # documents still pass every payload, source, journal and ancestry check.
    # An omitted/newly committed proof fails closed instead of using a pointer.
    documents = (db.session.query(AccountingEvidence).populate_existing().filter_by(kind='amazon_allocation')
                 if proof_documents is None else proof_documents)
    for document in documents:
        if not isinstance(document, AccountingEvidence) or document.kind != 'amazon_allocation':
            continue
        try:
            payload = json.loads(document.payload)
            previous = payload['preview']
            if (digest(payload) != document.sha256 or previous['bundle_id'] != bundle.id
                    or digest({key: value for key, value in previous.items() if key != 'digest'}) != previous['digest']):
                continue
            proofs[previous['digest']] = (document, previous)
        except (KeyError, TypeError, ValueError):
            continue
    current = proofs.get(bundle.posting_digest)
    if not current:
        raise ValueError('Missing or changed allocation proof')
    document, preview = current
    if bundle.transaction_id != preview['source']['id']:
        raise ValueError('Posted allocation source identity changed')
    _live_preview(preview)
    _assert_entries(preview, entries)
    ancestor = preview
    seen = set()
    while 'revision' in ancestor:
        _revision_preview({key: value for key, value in ancestor.items()
                           if key not in ('digest', 'revision')}, ancestor['revision'])
        revision = ancestor['revision']
        old_digest = revision['previous_posting_digest']
        if old_digest in seen or old_digest not in proofs:
            raise ValueError('Allocation revision ancestry is missing or changed')
        seen.add(old_digest)
        older = proofs[old_digest][1]
        if (older.get('revision', {}).get('number', 1) != revision['number'] - 1
                or older['source'] != preview['source']
                or older['evidence_digest'] != preview['evidence_digest']):
            raise ValueError('Allocation revision ancestry does not preserve its source')
        ancestor = older
    return document, preview


def _write_allocations(bundle, preview, reason):
    source = preview['source']
    signed = money(source['amount'])
    when = datetime.fromisoformat(source['date'])
    for i, allocation in enumerate(preview['allocations']):
        post_pair(timestamp=when, debit=allocation['category'] if signed < 0 else source['account'],
            credit=source['account'] if signed < 0 else allocation['category'],
            functional_amount=allocation['amount'], transaction_source='ofx',
            transaction_id=source['id'], allocation_key='amazon:{:03d}'.format(i),
            memo='Amazon evidence {}: {}'.format(bundle.id, reason))
    bundle.transaction_id = source['id']
    bundle.posting_digest = preview['digest']
    db.session.flush()


def book_allocation(preview, reviewer, reason):
    bundle, entries = _lock_allocation(preview, reviewer, reason)
    live = _live_preview(preview)
    source = live['source']
    signed = money(source['amount'])
    if bundle.posting_digest:
        _, current = _current_proof(bundle, entries)
        if current != preview:
            raise ValueError('Settlement already has a different allocation')
        return bundle
    if bundle.transaction_id or 'revision' in preview:
        raise ValueError('Initial allocation cannot claim an existing posting or revision')
    if db.session.query(AmazonEvidenceBundles).filter(
            AmazonEvidenceBundles.transaction_id == source['id'],
            AmazonEvidenceBundles.id != bundle.id).first():
        raise ValueError('Settlement is already reserved by another item bundle')
    if len(entries)!=1 or entries[0].allocation_key!='0':
        raise ValueError('Settlement must have exactly one original bank journal')
    old = entries[0]
    when = datetime.fromisoformat(source['date'])
    bank_side = old.credit_subaccount if signed<0 else old.debit_subaccount
    category = old.debit_subaccount if signed<0 else old.credit_subaccount
    if (category!='Bookkeeping Suspense' or bank_side!=source['account'] or old.functional_amount!=abs(signed)
        or old.source_amount!=abs(signed) or old.source_currency!='USD' or old.functional_currency!='USD'
        or utc(old.timestamp)!=when or any(getattr(old, field) is not None for field in
            ('conversion_rate', 'conversion_source', 'conversion_date'))):
        raise ValueError('Only source-preserving suspense settlements can be allocated')
    require_open(old.timestamp)
    # Audited DELETE/INSERT keeps the original before-image and every new leg.
    db.session.delete(old)
    db.session.flush()
    _write_allocations(bundle, live, reason)
    evidence('amazon_allocation',dict(preview=preview,reason=reason),reviewer)
    return bundle


def revise_allocation(preview, reviewer, reason, expected_posting_digest):
    """Revise an existing allocation against an explicit current version.

    ``preview`` comes from allocation_preview. The revision's ancestry is added
    here, after checking the immutable prior proof, original source, exact current
    journals and period. Nothing commits: callers own the atomic transaction.
    """
    if (not isinstance(expected_posting_digest, str)
            or not re.fullmatch('[0-9a-f]{64}', expected_posting_digest)):
        raise ValueError('An exact current posting digest is required for revision')
    if 'revision' in preview:
        raise ValueError('Create a fresh allocation preview before revising')
    bundle, entries = _lock_allocation(preview, reviewer, reason)
    if not bundle.posting_digest or not bundle.transaction_id:
        raise ValueError('Only an existing allocated settlement can be revised')
    document, previous = _current_proof(bundle, entries)
    live = _live_preview(preview)
    if live['source'] != previous['source'] or live['evidence_digest'] != previous['evidence_digest']:
        raise ValueError('A revision must preserve the original signed bank source and item evidence')
    previous_base = {key: value for key, value in previous.items() if key not in ('digest', 'revision')}
    previous_base['digest'] = digest(previous_base)
    same = live == previous_base
    if expected_posting_digest != bundle.posting_digest:
        # Retry of the just-completed identical revision is safe. A different
        # proposal or an older ancestor is stale and cannot consume this version.
        if not (same and previous.get('revision', {}).get('previous_posting_digest') == expected_posting_digest):
            raise ValueError('Allocation version changed; preview the current posting again')
        return bundle
    if same:
        return bundle
    require_open(datetime.fromisoformat(previous['source']['date']))
    revised = _revision_preview(live, dict(number=previous.get('revision', {}).get('number', 1) + 1,
                                         previous_posting_digest=bundle.posting_digest))
    for entry in entries:
        db.session.delete(entry)
    db.session.flush()
    _write_allocations(bundle, revised, reason)
    evidence('amazon_allocation', dict(preview=revised, previous_preview=previous,
        superseded_evidence_id=document.id, reason=reason), reviewer)
    return bundle
