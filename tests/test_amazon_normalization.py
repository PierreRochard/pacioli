"""Native evidence semantics, exact amounts and conservative candidate gates."""
from copy import deepcopy
from decimal import Decimal

import pytest

from pacioli.functions.amazon_normalization import normalize_tables


def table(filename, rows, identifier=1):
    headers = list(rows[0])
    return dict(evidence_id=identifier, filename=filename, headers=headers,
                rows=[[row.get(header, '') for header in headers] for row in rows])


def retail(**overrides):
    row = {
        'Order ID': '123-1234567-1234567', 'Order Status': 'Closed',
        'Shipment Status': 'Shipped', 'Currency': 'USD',
        'Ship Date': '2026-09-03T00:30:00Z', 'Order Date': '2026-09-01T12:00:00Z',
        'Carrier Name & Tracking Number': 'Carrier: synthetic-tracking-1',
        'Original Quantity': '1', 'Product Name': 'Household supplies',
        'Unit Price': '10.00', 'Unit Price Tax': '0.80', 'Shipping Charge': '0',
        'Total Discounts': '0', 'Total Amount': '10.80',
        'Shipment Item Subtotal': '10.00', 'Shipment Item Subtotal Tax': '0.80',
        'Payment Method Type': 'Visa - 1234',
    }
    row.update(overrides)
    return row


def digital(**overrides):
    row = {
        'Order ID': 'D01-SYNTHETIC', 'Order Status': 'SUCCESS',
        'Delivery Status': 'Delivery Complete', 'Fullfilment Status': 'Yes',
        'Base Currency': 'USD', 'FX Currency Code': 'Not Applicable',
        'FX Transaction Amount': 'Not Applicable',
        'Digital Order Item ID': 'item-1', 'Product Name': 'Example digital book',
        'Component Type': 'Price Amount', 'Transaction Amount': '10.00', 'Price': '10.00',
        'Order Date': '2026-09-01T12:00:00Z', 'Fulfilled Date': '2026-09-01T12:00:00Z',
        'Payment Information': 'Payment Instrument Details Not Available',
        'Does Order Depend on Another Order': 'No', 'Is Order Free Replacement': 'No',
    }
    row.update(overrides)
    return row


def digital_refund(**overrides):
    row = {
        'Order ID': 'D01-SYNTHETIC', 'Processing Successful': 'Yes',
        'Return Status': 'Customer Return Complete', 'Status': 'Amount Settled',
        'Return Date': '2026-09-04T12:00:00Z', 'Base Currency': 'USD',
        'FX Currency Code': 'Not Available', 'FX Transaction Amount': 'Not Available',
        'Digital Order Item ID': 'item-1', 'Product Name': 'Example digital book',
        'Monetary Component Type': 'Price Amount', 'Transaction Amount': '1.05',
        'Amount Refunded': '1.12', 'Refund to Gift Certificate': 'No',
        'Payment Information': 'Not Available',
    }
    row.update(overrides)
    return row


def retail_refund(**overrides):
    row = {
        'Order ID': '123-1234567-1234567', 'Payment Status': 'Completed',
        'Reversal Status': 'Completed', 'Reversal Amount State': 'Final',
        'Disbursement Type': 'Refund', 'Currency': 'USD',
        'Refund Date': '2026-09-04T12:00:00Z', 'Creation Date': '2026-09-04T11:00:00Z',
        'Refund Amount': '10.80', 'Direct Debit Refund Amount': '0',
    }
    row.update(overrides)
    return row


def test_retail_repeated_shipment_subtotals_are_not_double_counted():
    first = retail(**{'Unit Price': '10', 'Unit Price Tax': '0.80', 'Total Amount': '10.80',
                      'Shipment Item Subtotal': '15', 'Shipment Item Subtotal Tax': '1.20'})
    second = dict(first, **{'Unit Price': '5', 'Unit Price Tax': '0.40',
                           'Total Amount': '5.40', 'Product Name': 'A different item'})
    source = table('Your Amazon Orders/Order History.csv', [first, second], 55)
    before = deepcopy(source)
    result = normalize_tables([source])
    assert source == before
    assert result['exclusions'] == []
    assert result['summary']['eligible_records'] == 1
    record, = result['records']
    assert record['amount'] == '16.20'
    assert record['card_last4'] == '1234'
    assert record['blockers'] == []
    assert record['source_rows'] == [{'table_id': 55, 'row_number': 2}, {'table_id': 55, 'row_number': 3}]
    assert sum(Decimal(line['amount']) for line in record['lines']) == Decimal('16.20')
    assert normalize_tables([source]) == result


def test_retail_split_shipments_are_separate_and_no_order_subset_is_invented():
    first = retail()
    second = retail(**{'Ship Date': '2026-09-05T12:00:00Z',
                        'Carrier Name & Tracking Number': 'Carrier: synthetic-tracking-2',
                        'Unit Price': '20', 'Unit Price Tax': '1.60', 'Total Amount': '21.60',
                        'Shipment Item Subtotal': '20', 'Shipment Item Subtotal Tax': '1.60'})
    result = normalize_tables([table('Order History.csv', [first, second])])
    assert [(r['date'], r['amount']) for r in result['records']] == [
        ('2026-09-03', '10.80'), ('2026-09-05', '21.60')]
    assert all(not record['blockers'] for record in result['records'])
    assert len({r['key'] for r in result['records']}) == 2


def test_retail_quantity_discount_shipping_and_tax_are_exact():
    row = retail(**{'Original Quantity': '3', 'Unit Price': '0.10', 'Unit Price Tax': '0.01',
                    'Shipping Charge': '1.00', 'Total Discounts': "'-0.20'", 'Total Amount': '1.13',
                    'Shipment Item Subtotal': '0.30', 'Shipment Item Subtotal Tax': '0.03'})
    record, = normalize_tables([table('Order History.csv', [row])])['records']
    assert not record['blockers']
    assert record['amount'] == '1.13'
    assert [(line['kind'], line['amount']) for line in record['lines']] == [
        ('item', '0.30'), ('tax', '0.03'), ('shipping', '1.00'), ('discount', '-0.20')]


def test_retail_duplicate_rows_are_preserved_but_cannot_be_promoted():
    row = retail()
    result = normalize_tables([table('Order History.csv', [row, row])])
    record, = result['records']
    assert record['amount'] == '21.60'
    assert len(record['source_rows']) == 2
    assert [line['amount'] for line in record['lines']].count('10.00') == 2
    assert 'duplicate_source_rows' in record['blockers']
    assert 'shipment_subtotal_mismatch' in record['blockers']
    assert result['summary']['eligible_records'] == 0


@pytest.mark.parametrize('change,reason', [
    ({'Order Status': 'Cancelled'}, 'cancelled'),
    ({'Shipment Status': 'Not Available'}, 'unshipped'),
    ({'Currency': 'EUR'}, 'unsupported_currency'),
    ({'Ship Date': ''}, 'invalid_date'),
    ({'Ship Date': '1900-01-01T00:00:00Z'}, 'invalid_date'),
    ({'Ship Date': '2100-01-01T00:00:00Z'}, 'invalid_date'),
    ({'Ship Date': '2026-09-03'}, 'invalid_date'),
    ({'Ship Date': '2026-09-03T00:00:00'}, 'invalid_date'),
    ({'Ship Date': '2026-08-30T00:00:00Z'}, 'shipment_precedes_order'),
    ({'Total Amount': ''}, 'invalid_amount'),
    ({'Total Amount': '1e2'}, 'invalid_amount'),
    ({'Total Amount': 'NaN'}, 'invalid_amount'),
    ({'Total Amount': '10.801'}, 'fractional_cent_amount'),
    ({'Total Amount': '-10.80'}, 'negative_purchase_component'),
    ({'Original Quantity': '0'}, 'invalid_quantity'),
    ({'Product Name': ''}, 'missing_product_name'),
    ({'Shipment Status': 'Shipped and Shipped'}, 'composite_shipment'),
    ({'Carrier Name & Tracking Number': 'Carrier: a and Carrier: b'}, 'composite_shipment'),
])
def test_unusable_retail_evidence_is_excluded_explicitly(change, reason):
    result = normalize_tables([table('Order History.csv', [retail(**change)], 12)])
    assert result['records'] == []
    assert result['summary']['excluded_rows'] == 1
    exclusion, = result['exclusions']
    assert exclusion['reason'] == reason
    assert exclusion['source_rows'] == [{'table_id': 12, 'row_number': 2}]


def test_malformed_sibling_blocks_surviving_partial_shipment():
    result = normalize_tables([table('Order History.csv', [retail(), retail(**{'Total Amount': ''})])])
    record, = result['records']
    assert record['amount'] == '10.80'
    assert 'incomplete_order_evidence' in record['blockers']
    source = table('Order History.csv', [retail()])
    source['rows'].append(['broken width'])
    result = normalize_tables([source])
    assert 'incomplete_source_table' in result['records'][0]['blockers']
    assert result['exclusions'][0]['reason'] == 'malformed_row'
    result = normalize_tables([table('Order History.csv', [retail(), retail(**{'Order ID': ''})])])
    assert result['exclusions'][0]['reason'] == 'missing_order_id'
    assert 'incomplete_source_table' in result['records'][0]['blockers']


def test_utc_dates_and_provenance_keys_are_deterministic():
    source = table('Order History.csv', [retail(**{'Ship Date': '2026-09-02T20:30:00-05:00'})], 1)
    first = normalize_tables([source])['records'][0]
    assert first['date'] == '2026-09-03'
    source['evidence_id'] = 2
    second = normalize_tables([source])['records'][0]
    assert second['amount'] == first['amount']
    assert second['key'] != first['key']


@pytest.mark.parametrize('tender,blocker', [
    ('Not Available', 'unknown_tender'),
    ('Gift Certificate/Card', 'gift_or_mixed_tender'),
    ('Gift Certificate/Card and Visa - 1234', 'gift_or_mixed_tender'),
    ('Visa - 1234 and Visa - 5678', 'unrecognized_tender'),
])
def test_tender_is_never_guessed(tender, blocker):
    record, = normalize_tables([table('Order History.csv', [retail(**{'Payment Method Type': tender})])])['records']
    assert record['card_last4'] is None
    assert blocker in record['blockers']


def test_digital_transaction_components_not_repeated_price_are_summed():
    source = table('Digital Content Orders.csv', [digital(),
        digital(**{'Component Type': 'Tax', 'Transaction Amount': '0.80'})])
    result = normalize_tables([source])
    record, = result['records']
    assert record['amount'] == '10.80'
    assert record['kind'] == 'digital_purchase'
    assert record['blockers'] == ['unknown_tender']
    assert [line['amount'] for line in record['lines']] == ['10.00', '0.80']
    assert len(record['source_rows']) == 2


def test_digital_duplicate_component_is_preserved_and_blocks_promotion():
    record, = normalize_tables([table('Digital Content Orders.csv', [digital(), digital()])])['records']
    assert record['amount'] == '20.00'
    assert len(record['source_rows']) == 2
    assert set(record['blockers']) == {'unknown_tender', 'duplicate_source_rows', 'repeated_digital_component'}


def test_digital_coupon_price_and_tax_components_reduce_cash_total_exactly():
    source = table('Digital Content Orders.csv', [
        digital(**{'Price': '14.95', 'Transaction Amount': '14.95',
                   'Offer Type Code': 'Not Applicable', 'Claim Code': 'Not Applicable'}),
        digital(**{'Price': '14.95', 'Component Type': 'Tax', 'Transaction Amount': '0.08',
                   'Offer Type Code': 'Not Applicable', 'Claim Code': 'Not Applicable'}),
        digital(**{'Price': '14.95', 'Transaction Amount': '-13.96',
                   'Offer Type Code': 'Coupon', 'Claim Code': 'synthetic-coupon'}),
        digital(**{'Price': '14.95', 'Component Type': 'Tax', 'Transaction Amount': '0.0',
                   'Offer Type Code': 'Coupon', 'Claim Code': 'synthetic-coupon'}),
    ])
    record, = normalize_tables([source])['records']
    assert record['amount'] == '1.07'
    assert record['blockers'] == ['unknown_tender']
    assert [line['amount'] for line in record['lines']] == ['14.95', '0.08', '-13.96', '0.00']
    assert record['lines'][2]['kind'] == 'discount'
    assert len(record['source_rows']) == 4


def test_unknown_negative_digital_component_is_retained_with_blocker():
    source = table('Digital Content Orders.csv', [
        digital(**{'Offer Type Code': 'Not Applicable'}),
        digital(**{'Transaction Amount': '-2.00', 'Offer Type Code': 'Unknown adjustment'}),
    ])
    record, = normalize_tables([source])['records']
    assert record['amount'] == '8.00'
    assert 'unrecognized_negative_component' in record['blockers']
    assert len(record['source_rows']) == 2


def test_zero_net_digital_promotion_is_not_a_bank_charge():
    source = table('Digital Content Orders.csv', [
        digital(**{'Offer Type Code': 'Not Applicable'}),
        digital(**{'Transaction Amount': '-10.00', 'Offer Type Code': 'Promotion'}),
    ])
    result = normalize_tables([source])
    assert not result['records']
    assert result['exclusions'][0]['reason'] == 'zero_cash_movement'
    assert len(result['exclusions'][0]['source_rows']) == 2


def test_digital_multiple_items_and_cancelled_components():
    source = table('Digital Content Orders.csv', [digital(),
        digital(**{'Digital Order Item ID': 'item-2', 'Product Name': 'Another book', 'Transaction Amount': '5.00'}),
        digital(**{'Order ID': 'cancelled-order', 'Order Status': 'CANCELLED_BY_AMAZON'})])
    result = normalize_tables([source])
    assert result['records'][0]['amount'] == '15.00'
    assert result['records'][0]['blockers'] == ['unknown_tender']
    assert result['exclusions'][0]['reason'] == 'cancelled'


def test_digital_price_cannot_become_eligible_when_sibling_tax_has_different_date():
    result = normalize_tables([table('Digital Content Orders.csv', [
        digital(**{'Payment Information': 'Visa - 1234'}),
        digital(**{'Component Type': 'Tax', 'Transaction Amount': '0.80',
                   'Fulfilled Date': '2026-09-02T12:00:00Z', 'Payment Information': 'Visa - 1234'}),
    ])])
    assert len(result['records']) == 2
    assert all('inconsistent_digital_component_dates' in record['blockers'] for record in result['records'])
    assert result['summary']['eligible_records'] == 0


@pytest.mark.parametrize('change,reason', [
    ({'FX Currency Code': 'EUR', 'FX Transaction Amount': '9.00'}, 'foreign_exchange'),
    ({'Base Currency': 'EUR'}, 'unsupported_currency'),
    ({'Transaction Amount': ''}, 'invalid_amount'),
    ({'Component Type': 'Credit'}, 'unknown_digital_component'),
    ({'Fullfilment Status': 'No'}, 'unfulfilled_digital_order'),
])
def test_unusable_digital_components_excluded_without_reconstructing_price(change, reason):
    result = normalize_tables([table('Digital Content Orders.csv', [digital(**change)])])
    assert not result['records']
    assert result['exclusions'][0]['reason'] == reason


def test_digital_refund_repeated_declared_total_is_not_doubled():
    result = normalize_tables([table('Digital Returns.csv', [digital_refund(),
        digital_refund(**{'Monetary Component Type': 'Tax', 'Transaction Amount': '0.07'})])])
    record, = result['records']
    assert record['kind'] == 'digital_refund'
    assert record['amount'] == '-1.12'
    assert record['blockers'] == ['unknown_tender']
    assert [line['amount'] for line in record['lines']] == ['-1.05', '-0.07']
    assert result['summary']['by_kind']['digital_refund']['amount'] == '-1.12'


def test_digital_refund_mismatch_and_gift_destination_remain_blocked():
    record, = normalize_tables([table('Digital Returns.csv', [digital_refund(**{
        'Amount Refunded': '2.00', 'Refund to Gift Certificate': 'Yes'})])])['records']
    assert record['amount'] == '-1.05'
    assert {'refund_component_total_mismatch', 'gift_or_unknown_refund_destination'} <= set(record['blockers'])


@pytest.mark.parametrize('refund_amount', ['10.80', '-10.80', '10.800'])
def test_completed_retail_refunds_use_negative_cash_sign_and_stay_separate(refund_amount):
    result = normalize_tables([table('Refund Details.csv', [retail_refund(**{'Refund Amount': refund_amount}),
        retail_refund(**{'Refund Date': '2026-09-06T12:00:00Z', 'Refund Amount': '5.00'})])])
    assert [record['amount'] for record in result['records']] == ['-10.80', '-5.00']
    assert all(record['blockers'] == ['unknown_tender'] for record in result['records'])


def test_refund_requests_are_not_new_refunds_and_unsettled_refunds_are_excluded():
    result = normalize_tables([
        table('Refund Details.csv', [retail_refund(), retail_refund(**{'Payment Status': 'Pending'})]),
        table('Additional Data/Your Orders.Returns.1.csv', [
            {'Order ID': '123-1234567-1234567', 'Refund Amount': '10.80', 'Refund Status': 'RefundInitiated'}], 2),
        table('Returns Status.csv', [{'Order ID': '123-1234567-1234567', 'Return Amount': '10.80'}], 3),
    ])
    assert len(result['records']) == 1
    assert result['records'][0]['amount'] == '-10.80'
    assert result['summary']['exclusions_by_reason'] == {'companion_or_unsupported_table': 2, 'unsettled_refund': 1}


def test_every_row_has_exactly_one_record_or_exclusion_even_duplicates_and_companions():
    sources = [table('Order History.csv', [retail(), retail(), retail(**{'Currency': 'EUR'})], 8),
               table('Cart History.csv', [{'ASIN': 'synthetic'}], 9),
               table('Digital Returns.csv', [digital_refund(), digital_refund(**{
                   'Monetary Component Type': 'Tax', 'Transaction Amount': '0.07'})], 10)]
    result = normalize_tables(sources)
    refs = [(ref['table_id'], ref['row_number']) for record in result['records'] + result['exclusions']
            for ref in record['source_rows']]
    assert sorted(refs) == [(8, 2), (8, 3), (8, 4), (9, 2), (10, 2), (10, 3)]
    assert result['summary']['input_rows'] == result['summary']['normalized_rows'] + result['summary']['excluded_rows'] == 6
    assert normalize_tables(list(reversed(sources))) == result


def test_missing_or_repeated_table_identity_fails_closed():
    source = table('Order History.csv', [retail()])
    with pytest.raises(ValueError, match='evidence_id'):
        normalize_tables([source, source])
    source['evidence_id'] = None
    with pytest.raises(ValueError, match='evidence_id'):
        normalize_tables([source])


def test_money_float_cells_are_malformed_instead_of_casting_float_to_decimal():
    source = table('Order History.csv', [retail(**{'Total Amount': 10.8})])
    result = normalize_tables([source])
    assert not result['records']
    assert result['exclusions'][0]['reason'] == 'malformed_row'
