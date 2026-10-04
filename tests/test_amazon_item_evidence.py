"""Item identity, exact component ownership and minimal model disclosure."""
from contextlib import nullcontext
from copy import deepcopy
from decimal import Decimal

import pytest

from pacioli.functions import amazon_item_evidence as projection
from pacioli.functions.amazon_normalization import normalize_tables


def retail(**overrides):
    row = {
        'Order ID': '123-1234567-1234567', 'Order Status': 'Closed',
        'Shipment Status': 'Shipped', 'Currency': 'USD',
        'Ship Date': '2026-09-03T00:30:00Z', 'Order Date': '2026-09-01T12:00:00Z',
        'Carrier Name & Tracking Number': 'Carrier: synthetic-tracking-1',
        'Original Quantity': '1', 'Product Name': 'Synthetic household item',
        'Unit Price': '10.00', 'Unit Price Tax': '0.80', 'Shipping Charge': '0',
        'Total Discounts': '0', 'Total Amount': '10.80',
        'Shipment Item Subtotal': '10.00', 'Shipment Item Subtotal Tax': '0.80',
        'Payment Method Type': 'Visa - 1234', 'ASIN': 'B012345678',
        'Department': 'Household', 'Website': 'Amazon.com', 'Product Condition': 'New',
        'Gift Message': 'Not Available', 'Gift Recipient Contact': 'Not Available',
        'Gift Sender Name': 'Not Available', 'Purchase Order Number': 'Not Available',
        'Billing Address': 'PRIVATE BILLING', 'Shipping Address': 'PRIVATE SHIPPING',
        'Item Serial Number': 'PRIVATE SERIAL',
    }
    row.update(overrides)
    return row


def source(rows, identifier=1):
    headers = list(rows[0])
    return dict(evidence_id=identifier, filename='Order History.csv', headers=headers,
                rows=[[row.get(header, '') for header in headers] for row in rows])


def record(tables):
    result, = normalize_tables(tables)['records']
    return result


def test_amazon_item_exact_quantity_tax_shipping_discount_and_input_immutability():
    tables = [source([retail(**{
        'Original Quantity': '3', 'Unit Price': '0.10', 'Unit Price Tax': '0.01',
        'Shipping Charge': '1.00', 'Total Discounts': "'-0.20'", 'Total Amount': '1.13',
        'Shipment Item Subtotal': '0.30', 'Shipment Item Subtotal Tax': '0.03',
    })])]
    normalized = record(tables)
    before = deepcopy((tables, normalized))
    result = projection.project_record(normalized, tables)
    item, = result['items']
    assert item['quantity'] == 3 and item['unit_price'] == '0.10'
    assert item['unit_tax'] == '0.01' and item['amount'] == result['amount'] == '1.13'
    assert [(c['kind'], c['amount']) for c in item['components']] == [
        ('item', '0.30'), ('tax', '0.03'), ('shipping', '1.00'), ('discount', '-0.20')]
    assert sum(Decimal(c['amount']) for c in item['components']) == Decimal('1.13')
    assert item['components'][0]['source_refs'][0]['fields'] == ['Unit Price', 'Original Quantity']
    assert (tables, normalized) == before
    assert projection.project_record(normalized, tables) == result


def test_amazon_item_identical_titles_and_asins_keep_distinct_occurrences():
    first = retail(**{'Shipment Item Subtotal': '15.00', 'Shipment Item Subtotal Tax': '1.20'})
    second = dict(first, **{'Unit Price': '5.00', 'Unit Price Tax': '0.40', 'Total Amount': '5.40'})
    tables = [source([first, second])]
    result = projection.project_record(record(tables), tables)
    assert len(result['items']) == 2
    assert len({item['item_id'] for item in result['items']}) == 2
    assert [item['amount'] for item in result['items']] == ['10.80', '5.40']
    assert [item['source_refs'][0]['row_number'] for item in result['items']] == [2, 3]
    component_ids = [c['id'] for item in result['items'] for c in item['components']]
    assert len(component_ids) == len(set(component_ids)) == 4


def test_amazon_item_zero_cost_sibling_is_retained_without_fake_component():
    zero = retail(**{'Unit Price': '0', 'Unit Price Tax': '0', 'Total Amount': '0'})
    tables = [source([retail(), zero])]
    result = projection.project_record(record(tables), tables)
    assert len(result['items']) == 2
    assert result['items'][1]['amount'] == '0.00' and result['items'][1]['components'] == []


@pytest.mark.parametrize('field,value', [
    ('amount', '999.00'), ('order_id', 'different-order'), ('date', '2026-09-04'),
    ('card_last4', '9999'), ('currency', 'EUR'), ('kind', 'retail_refund'),
    ('blockers', ['missing_shipment_identity']),
])
def test_amazon_item_rejects_changed_record_fields(field, value):
    tables = [source([retail()])]
    normalized = record(tables)
    normalized[field] = value
    with pytest.raises(ValueError):
        projection.project_record(normalized, tables)


def test_amazon_item_rejects_changed_source_money_and_component_ownership():
    tables = [source([retail()])]
    normalized = record(tables)
    damaged = deepcopy(tables)
    damaged[0]['rows'][0][damaged[0]['headers'].index('Total Amount')] = '11.00'
    with pytest.raises(ValueError):
        projection.project_record(normalized, damaged)
    normalized['lines'][1]['source_rows'] = [{'table_id': 1, 'row_number': 3}]
    with pytest.raises(ValueError):
        projection.project_record(normalized, tables)


def test_amazon_item_duplicate_or_partial_source_cannot_be_promoted():
    tables = [source([retail(), retail()])]
    with pytest.raises(ValueError):
        projection.project_record(record(tables), tables)
    tables = [source([retail()])]
    normalized = record(tables)
    normalized['source_rows'].append(deepcopy(normalized['source_rows'][0]))
    with pytest.raises(ValueError):
        projection.project_record(normalized, tables)


@pytest.mark.parametrize('value', ['', 'Not Available', 'not applicable', '  NOT AVAILABLE  '])
def test_amazon_item_missing_gift_and_purchase_order_sentinels_do_not_block(value):
    tables = [source([retail(**{field: value for field in (
        'Gift Message', 'Gift Recipient Contact', 'Gift Sender Name', 'Purchase Order Number')})])]
    item, = projection.project_record(record(tables), tables)['items']
    assert item['blockers'] == []


def test_amazon_item_gift_and_purchase_order_context_hold_without_private_text():
    tables = [source([retail(**{'Gift Message': 'PRIVATE GIFT',
                              'Gift Recipient Contact': 'PRIVATE RECIPIENT',
                              'Purchase Order Number': 'PRIVATE PO'})])]
    item, = projection.project_record(record(tables), tables)['items']
    assert item['blockers'] == ['gift_context_requires_review', 'purchase_order_context_requires_review']
    assert 'PRIVATE' not in repr(item)


def test_amazon_item_model_view_is_explicit_allowlist_and_detached():
    tables = [source([retail()])]
    item, = projection.project_record(record(tables), tables)['items']
    item['extra_secret'] = 'PRIVATE'
    view = projection.model_item(item)
    assert set(view) == {'item_id', 'title', 'department', 'quantity', 'condition', 'blockers', 'evidence_refs'}
    assert view['evidence_refs'] == ['product.title', 'product.department', 'product.quantity', 'product.condition']
    assert 'PRIVATE' not in repr(view) and 'B012345678' not in repr(view)
    assert 'amount' not in view and 'source_refs' not in view
    view['blockers'].append('changed')
    assert item['blockers'] == []


def test_amazon_item_source_observation_identity_changes_across_archives():
    first = [source([retail()], identifier=1)]
    second = [source([retail()], identifier=2)]
    one = projection.project_record(record(first), first)['items'][0]
    two = projection.project_record(record(second), second)['items'][0]
    assert one['item_id'] != two['item_id']
    assert one['asin'] == two['asin'] and one['amount'] == two['amount']


def test_amazon_items_batch_projects_distinct_settlements_with_one_normalization(monkeypatch):
    second = retail(**{'Order ID': '123-7654321-7654321', 'Product Name': 'Another item'})
    tables = [source([retail(), second])]
    records = normalize_tables(tables)['records']
    calls = []
    original = projection.normalize_tables
    monkeypatch.setattr(projection, 'normalize_tables',
                        lambda inputs: calls.append(inputs) or original(inputs))
    results = projection.project_records(records, tables)
    assert len(results) == 2 and len(calls) == 1
    assert {result['record_key'] for result in results} == {record['key'] for record in records}
    with pytest.raises(ValueError, match='only once'):
        projection.project_records([records[0], records[0]], tables)


def test_amazon_item_build_projection_selects_fresh_unique_retail_and_preserves_metadata(monkeypatch):
    from pacioli.functions import amazon_reconciliation
    tables = [source([retail()])]
    normalized = record(tables)
    normalized.update(status='unique_candidate', candidates=[{'id': 'bank-source', 'amount': '-10.80'}])
    report = dict(records=[normalized, dict(normalized, status='already_booked'),
                           dict(normalized, kind='digital_purchase')],
                  archive_sha256='archive-digest', version='review-version',
                  normalization_version='normal-version', bank_input_digest='bank-digest')
    calls = []
    monkeypatch.setattr(amazon_reconciliation, 'build_report',
                        lambda session, identifier: calls.append(identifier) or report)
    monkeypatch.setattr(amazon_reconciliation, '_source_tables',
                        lambda session, identifier: ({'source_sha256': 'archive-digest'}, tables))

    class ReadSession:
        no_autoflush = nullcontext()

    result = projection.build_projection(ReadSession(), 297)
    assert calls == [297]
    assert result['summary'] == dict(settlements=1, items=1, amount='10.80', blocked_items=0, blockers={})
    assert result['bank_input_digest'] == 'bank-digest'
    assert result['settlements'][0]['source_record']['candidates'][0]['id'] == 'bank-source'
    assert result['archive_id'] == 297 and result['archive_digest'] == 'archive-digest'
    result['settlements'][0]['source_record']['candidates'][0]['id'] = 'changed'
    assert normalized['candidates'][0]['id'] == 'bank-source'


def test_amazon_item_build_projection_rejects_archive_drift(monkeypatch):
    from pacioli.functions import amazon_reconciliation
    monkeypatch.setattr(amazon_reconciliation, 'build_report',
                        lambda session, identifier: {'archive_sha256': 'first'})
    monkeypatch.setattr(amazon_reconciliation, '_source_tables',
                        lambda session, identifier: ({'source_sha256': 'second'}, []))

    class ReadSession:
        no_autoflush = nullcontext()

    with pytest.raises(ValueError, match='Archive changed'):
        projection.build_projection(ReadSession(), 297)
