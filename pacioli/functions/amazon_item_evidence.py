"""Read-only, source-linked item evidence for Amazon retail classification.

The settlement normalizer establishes cash arithmetic. This projection retains
one semantic item per original retail row and associates that row's exact money
components with it. Source observations are not cross-export purchase identities.
The deliberately smaller model view has no payment or private recipient fields.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal

from pacioli.functions.amazon_normalization import (
    _amount, _digest, _missing, _money, _quantity, normalize_tables,
)


VERSION = 'amazon-item-evidence-v1'
_RECORD_FIELDS = ('key', 'kind', 'order_id', 'currency', 'date', 'amount',
                  'card_last4', 'lines', 'source_rows', 'blockers')
_COMPONENT_FIELDS = {
    'item': ['Unit Price', 'Original Quantity'],
    'tax': ['Unit Price Tax', 'Original Quantity'],
    'shipping': ['Shipping Charge'],
    'discount': ['Total Discounts'],
}
_GIFT_FIELDS = ('Gift Message', 'Gift Recipient Contact', 'Gift Sender Name')
_MODEL_FIELDS = ('item_id', 'title', 'department', 'quantity', 'condition', 'blockers')


def _optional(value):
    return None if value is None or _missing(value) else value.strip()


def _index(tables):
    """Normalize once per projection, validating table/row identity as well."""
    normalized = normalize_tables(tables)
    return ({table['evidence_id']: table for table in tables},
            {record['key']: record for record in normalized['records']})


def _project(record, table_index, normalized_index):
    if (record.get('kind') != 'retail_purchase' or record.get('currency') != 'USD'
            or record.get('blockers') != []):
        raise ValueError('Only eligible USD retail purchases support item projection')
    current = normalized_index.get(record.get('key'))
    if not current or any(record.get(field) != current.get(field) for field in _RECORD_FIELDS):
        raise ValueError('Retail settlement differs from immutable source normalization')

    items = []
    represented = set()
    for ref in record['source_rows']:
        identity = (ref['table_id'], ref['row_number'])
        if identity in represented:
            raise ValueError('A retail source occurrence may appear only once')
        represented.add(identity)
        table = table_index[ref['table_id']]
        cells = table['rows'][ref['row_number'] - 2]
        row = dict(zip(table['headers'], cells))
        quantity = int(_quantity(row['Original Quantity']))
        unit_price, unit_tax = _money(row['Unit Price']), _money(row['Unit Price Tax'])
        values = dict(item=unit_price * quantity, tax=unit_tax * quantity,
                      shipping=_money(row['Shipping Charge']),
                      discount=_money(row['Total Discounts']))
        total = _money(row['Total Amount'])
        if sum(values.values(), Decimal('0')) != total:
            raise ValueError('Retail item components do not equal the source total')
        lines = [line for line in record['lines'] if line['source_rows'] == [ref]]
        expected = [(kind, _amount(value)) for kind, value in values.items() if value]
        if [(line['kind'], line['amount']) for line in lines] != expected:
            raise ValueError('Retail item components have ambiguous source ownership')
        item_id = _digest(dict(version=VERSION, source_ref=ref))
        components = [dict(
            id=_digest(dict(version=VERSION, source_ref=ref, kind=kind)),
            kind=kind, amount=_amount(value),
            source_refs=[dict(ref, fields=_COMPONENT_FIELDS[kind][:])],
        ) for kind, value in values.items() if value]
        blockers = []
        if any(_optional(row.get(field)) is not None for field in _GIFT_FIELDS):
            blockers.append('gift_context_requires_review')
        if _optional(row.get('Purchase Order Number')) is not None:
            blockers.append('purchase_order_context_requires_review')
        items.append(dict(
            item_id=item_id, title=row['Product Name'],
            department=_optional(row.get('Department')), asin=_optional(row.get('ASIN')),
            marketplace=_optional(row.get('Website')), quantity=quantity,
            condition=_optional(row.get('Product Condition')),
            unit_price=_amount(unit_price), unit_tax=_amount(unit_tax), amount=_amount(total),
            components=components, source_refs=[deepcopy(ref)], blockers=blockers,
        ))

    if sum((Decimal(item['amount']) for item in items), Decimal('0')) != _money(record['amount']):
        raise ValueError('Projected item amounts do not reproduce the settlement')
    if sum(len(item['components']) for item in items) != len(record['lines']):
        raise ValueError('Every source component must belong to exactly one item')
    return dict(record_key=record['key'], order_id=record['order_id'], date=record['date'],
                currency=record['currency'], amount=record['amount'],
                source_record=deepcopy(record), items=items)


def project_record(record, tables):
    """Project one eligible retail record, rejecting altered or incomplete input.

    Tables are the complete tables of one selected immutable archive as returned
    by ``amazon_reconciliation._source_tables``. Matching status is caller-owned;
    this function revalidates normalization, not the bank relationship.
    """
    table_index, normalized_index = _index(tables)
    return _project(record, table_index, normalized_index)


def project_records(records, tables):
    """Project a set of settlements with one normalization and unique sources."""
    keys = [record.get('key') for record in records]
    if len(keys) != len(set(keys)):
        raise ValueError('A retail settlement may appear only once')
    table_index, normalized_index = _index(tables)
    projected = [_project(record, table_index, normalized_index) for record in records]
    item_ids = [item['item_id'] for settlement in projected for item in settlement['items']]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError('A source item appears in multiple settlements')
    return projected


def model_item(item):
    """Return only the explicit inference allowlist, never the raw source row.

    The department and condition can be null. Context blockers expose the need
    for review without revealing gift messages, recipients or purchase orders.
    A product identifier remains an application-side retrieval key.
    """
    result = {field: deepcopy(item[field]) for field in _MODEL_FIELDS}
    result['evidence_refs'] = [
        'product.' + field for field in ('title', 'department', 'quantity', 'condition')
        if result[field] is not None
    ]
    return result


def build_projection(session, archive_id):
    """Build current unique-candidate retail evidence without persisting anything.

    The caller owns the transaction and should use a stable read-only snapshot.
    Source readers validate immutable manifest/table digests. The report retains
    candidate and bank-input fingerprints for subsequent posting revalidation.
    No model or financial write is performed here.
    """
    from pacioli.functions.amazon_reconciliation import _source_tables, build_report

    with session.no_autoflush:
        report = build_report(session, archive_id)
        manifest, tables = _source_tables(session, archive_id)
        if report['archive_sha256'] != manifest['source_sha256']:
            raise ValueError('Archive changed while preparing item evidence')
        table_index, normalized_index = _index(tables)
        selected = [record for record in report['records']
                    if record['status'] == 'unique_candidate' and record['kind'] == 'retail_purchase']
        settlements = [_project(record, table_index, normalized_index) for record in selected]
    item_ids = [item['item_id'] for settlement in settlements for item in settlement['items']]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError('A source item appears in multiple candidate settlements')
    blockers = Counter(blocker for settlement in settlements for item in settlement['items']
                       for blocker in item['blockers'])
    return dict(
        version=VERSION, archive_id=archive_id, archive_digest=manifest['source_sha256'],
        reconciliation_version=report['version'], normalization_version=report['normalization_version'],
        bank_input_digest=report['bank_input_digest'], settlements=settlements,
        summary=dict(settlements=len(settlements), items=len(item_ids),
                     amount=_amount(sum((Decimal(s['amount']) for s in settlements), Decimal('0'))),
                     blocked_items=sum(bool(item['blockers']) for settlement in settlements
                                       for item in settlement['items']),
                     blockers=dict(sorted(blockers.items()))),
    )
