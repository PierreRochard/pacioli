"""Pure, conservative interpretation of Amazon's native order-data CSVs.

This module creates settlement *candidates*, never journal entries. A shipment
subtotal is repeated on item rows; a digital price is repeated on monetary
component rows. Neither may be summed as though every row were a new charge.
All original rows, including duplicates and exclusions, retain provenance.
Dates are UTC calendar dates; settlement matching must allow posting delays.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import PurePosixPath
import re


VERSION = 'amazon-native-v1'
ZERO = Decimal('0')
CENT = Decimal('0.01')
MISSING = {'', 'not available', 'not applicable', 'payment instrument details not available'}
KINDS = ('retail_purchase', 'digital_purchase', 'retail_refund', 'digital_refund')


class Uninterpretable(ValueError):
    pass


def _missing(value):
    return str(value).strip().casefold() in MISSING


def _money(value):
    """Accept decimal text only, including Amazon's quoted negative discounts.

    More than two decimal places are accepted only when they are trailing zeroes;
    binary-float artifacts in companion returns tables must not be rounded away.
    """
    if not isinstance(value, str):
        raise Uninterpretable('invalid_amount')
    value = value.strip()
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        value = value[1:-1]
    if not re.fullmatch(r'-?\d{1,12}(?:\.\d{1,12})?', value):
        raise Uninterpretable('invalid_amount')
    try:
        amount = Decimal(value)
        if amount != amount.quantize(CENT):
            raise Uninterpretable('fractional_cent_amount')
        return amount.quantize(CENT)
    except InvalidOperation as exc:
        raise Uninterpretable('invalid_amount') from exc


def _quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9]\d{0,5}', value.strip()):
        raise Uninterpretable('invalid_quantity')
    return Decimal(value)


def _date(value):
    """Use explicit timezones and fixed, documented bounds, not wall-clock state."""
    if not isinstance(value, str):
        raise Uninterpretable('invalid_date')
    try:
        value = value.strip()
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        parsed = parsed.astimezone(timezone.utc)
        # Amazon order history cannot predate 1994. A fixed upper bound keeps
        # this transformation deterministic; callers apply their as-of window.
        if not 1994 <= parsed.year < 2100:
            raise ValueError()
        return parsed
    except (ValueError, OverflowError) as exc:
        raise Uninterpretable('invalid_date') from exc


def _amount(value):
    return '0.00' if value == ZERO else format(value.quantize(CENT), '.2f')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def _refs(rows):
    return [row['ref'] for row in rows]


def _tender(value):
    if _missing(value):
        return None, ['unknown_tender']
    value = value.strip()
    # Single explicit card only. Do not assign a composite/gift-card total to
    # the card just because its last four digits happen to occur in the text.
    match = re.fullmatch(r'(?:Visa|Mastercard|MasterCard|American Express|Amex|Discover|AmazonPLCC) - (\d{4})', value)
    if match:
        return match.group(1), []
    if 'gift' in value.casefold():
        return None, ['gift_or_mixed_tender']
    return None, ['unrecognized_tender']


def _line(title, value, kind, row):
    return dict(title=title, amount=_amount(value), kind=kind, source_rows=[row['ref']])


def _record(kind, order_id, currency, date, amount, rows, lines, blockers, card=None):
    refs = _refs(rows)
    return dict(key=_digest(dict(version=VERSION, kind=kind, source_rows=refs)),
                kind=kind, order_id=order_id, currency=currency, date=date,
                amount=_amount(amount), card_last4=card, lines=lines,
                source_rows=refs, blockers=sorted(set(blockers)))


def _exclude(rows, reason, kind=None):
    refs = _refs(rows)
    return dict(key=_digest(dict(version=VERSION, exclusion=reason, source_rows=refs)),
                kind=kind, order_id=rows[0]['data'].get('Order ID') or None,
                source_rows=refs, reason=reason, blockers=[reason])


def _duplicates(rows):
    counts = Counter(_digest(row['data']) for row in rows)
    return ['duplicate_source_rows'] if any(value > 1 for value in counts.values()) else []


def _retail(rows, records, exclusions):
    groups = defaultdict(list)
    incomplete_orders = set()
    for row in rows:
        data = row['data']
        order_id = data.get('Order ID', '').strip()
        try:
            if not order_id:
                raise Uninterpretable('missing_order_id')
            if data.get('Order Status', '').casefold() == 'cancelled':
                raise Uninterpretable('cancelled')
            if data.get('Order Status') != 'Closed':
                raise Uninterpretable('unsettled_order')
            if any(' and ' in data.get(field, '') for field in
                   ('Ship Date', 'Shipment Status', 'Carrier Name & Tracking Number')):
                raise Uninterpretable('composite_shipment')
            if data.get('Shipment Status') != 'Shipped':
                raise Uninterpretable('unshipped')
            if data.get('Currency') != 'USD':
                raise Uninterpretable('unsupported_currency')
            shipped = _date(data.get('Ship Date'))
            ordered = _date(data.get('Order Date'))
            if shipped < ordered:
                raise Uninterpretable('shipment_precedes_order')
            row['date'] = shipped
            row['quantity'] = _quantity(data.get('Original Quantity'))
            row['values'] = {field: _money(data.get(field)) for field in (
                'Unit Price', 'Unit Price Tax', 'Shipping Charge', 'Total Discounts',
                'Total Amount', 'Shipment Item Subtotal', 'Shipment Item Subtotal Tax')}
            if any(row['values'][field] < ZERO for field in (
                    'Unit Price', 'Unit Price Tax', 'Shipping Charge', 'Total Amount',
                    'Shipment Item Subtotal', 'Shipment Item Subtotal Tax')):
                raise Uninterpretable('negative_purchase_component')
            if row['values']['Total Discounts'] > ZERO:
                raise Uninterpretable('positive_discount')
            if not data.get('Product Name', '').strip():
                raise Uninterpretable('missing_product_name')
            carrier = data.get('Carrier Name & Tracking Number', '').strip()
            # Same order, precise shipment instant and carrier/tracking is a
            # provider-supported grouping. Never search arbitrary item subsets.
            group_key = (order_id, shipped.isoformat(), carrier)
            groups[group_key].append(row)
        except Uninterpretable as exc:
            reason = str(exc)
            exclusions.append(_exclude([row], reason, 'retail_purchase'))
            if reason not in ('cancelled', 'unshipped', 'unsettled_order'):
                incomplete_orders.add(order_id)
    for (order_id, _, carrier), group in groups.items():
        blockers = _duplicates(group)
        if order_id in incomplete_orders:
            blockers.append('incomplete_order_evidence')
        if _missing(carrier):
            blockers.append('missing_shipment_identity')
        tender_values = {row['data'].get('Payment Method Type', '') for row in group}
        if len(tender_values) == 1:
            card, tender_blockers = _tender(next(iter(tender_values)))
            blockers.extend(tender_blockers)
        else:
            card = None
            blockers.append('conflicting_tender')
        lines = []
        for row in group:
            values, qty = row['values'], row['quantity']
            title = row['data']['Product Name']
            item = values['Unit Price'] * qty
            tax = values['Unit Price Tax'] * qty
            shipping = values['Shipping Charge']
            discount = values['Total Discounts']
            calculated = item + tax + shipping + discount
            if calculated != values['Total Amount']:
                blockers.append('item_total_mismatch')
                # Preserve the provider total without pretending to know the
                # missing allocation. The blocker prevents an eligible match.
                lines.append(_line(title, values['Total Amount'], 'unresolved_item_total', row))
            else:
                for amount, kind, label in ((item, 'item', title), (tax, 'tax', title),
                                            (shipping, 'shipping', title),
                                            (discount, 'discount', title)):
                    if amount:
                        lines.append(_line(label, amount, kind, row))
        for provider_field, unit_field, mismatch in (
                ('Shipment Item Subtotal', 'Unit Price', 'shipment_subtotal_mismatch'),
                ('Shipment Item Subtotal Tax', 'Unit Price Tax', 'shipment_tax_mismatch')):
            declared = {row['values'][provider_field] for row in group}
            computed = sum((row['values'][unit_field] * row['quantity'] for row in group), ZERO)
            if len(declared) != 1 or computed != next(iter(declared)):
                blockers.append(mismatch)
        amount = sum((row['values']['Total Amount'] for row in group), ZERO)
        if not amount:
            exclusions.append(_exclude(group, 'zero_cash_movement', 'retail_purchase'))
            continue
        records.append(_record('retail_purchase', order_id, 'USD',
                               group[0]['date'].date().isoformat(), amount,
                               group, lines, blockers, card))


def _digital(rows, records, exclusions, refund=False):
    kind = 'digital_refund' if refund else 'digital_purchase'
    groups = defaultdict(list)
    item_dates = defaultdict(set)
    incomplete_orders = set()
    component_field = 'Monetary Component Type' if refund else 'Component Type'
    for row in rows:
        data = row['data']
        order_id = data.get('Order ID', '').strip()
        try:
            if not order_id:
                raise Uninterpretable('missing_order_id')
            if refund:
                if (data.get('Processing Successful') != 'Yes' or
                        data.get('Return Status') != 'Customer Return Complete' or
                        data.get('Status') != 'Amount Settled'):
                    raise Uninterpretable('unsettled_refund')
                when = _date(data.get('Return Date'))
            else:
                if 'CANCEL' in data.get('Order Status', '').upper():
                    raise Uninterpretable('cancelled')
                if (data.get('Order Status') != 'SUCCESS' or
                        data.get('Delivery Status') != 'Delivery Complete' or
                        data.get('Fullfilment Status') != 'Yes'):
                    raise Uninterpretable('unfulfilled_digital_order')
                when = _date(data.get('Fulfilled Date'))
                if when < _date(data.get('Order Date')):
                    raise Uninterpretable('fulfillment_precedes_order')
            if data.get('Base Currency') != 'USD':
                raise Uninterpretable('unsupported_currency')
            if not _missing(data.get('FX Currency Code', '')) or not _missing(data.get('FX Transaction Amount', '')):
                raise Uninterpretable('foreign_exchange')
            if not data.get('Digital Order Item ID', '').strip():
                raise Uninterpretable('missing_digital_item_id')
            if not data.get('Product Name', '').strip():
                raise Uninterpretable('missing_product_name')
            if data.get(component_field) not in ('Price Amount', 'Tax'):
                raise Uninterpretable('unknown_digital_component')
            value = _money(data.get('Transaction Amount'))
            row['value'] = -abs(value) if refund else value
            row['source_value'] = value
            row['date'] = when
            if refund:
                row['declared_refund'] = abs(_money(data.get('Amount Refunded')))
            groups[(order_id, when.isoformat())].append(row)
            item_dates[(order_id, data['Digital Order Item ID'])].add(when.isoformat())
        except Uninterpretable as exc:
            reason = str(exc)
            exclusions.append(_exclude([row], reason, kind))
            if reason not in ('cancelled', 'unfulfilled_digital_order', 'unsettled_refund'):
                incomplete_orders.add(order_id)
    for (order_id, _), group in groups.items():
        blockers = _duplicates(group)
        if order_id in incomplete_orders:
            blockers.append('incomplete_order_evidence')
        payment_values = {row['data'].get('Payment Information', '') for row in group}
        if len(payment_values) == 1:
            card, payment_blockers = _tender(next(iter(payment_values)))
            blockers.extend(payment_blockers)
        else:
            card = None
            blockers.append('conflicting_tender')
        items = defaultdict(list)
        for row in group:
            items[row['data']['Digital Order Item ID']].append(row)
            data = row['data']
            if not refund and len(item_dates[(order_id, data['Digital Order Item ID'])]) > 1:
                blockers.append('inconsistent_digital_component_dates')
            if refund:
                if data.get('Refund to Gift Certificate') != 'No':
                    blockers.append('gift_or_unknown_refund_destination')
            else:
                offer = data.get('Offer Type Code', '')
                if row['value'] < ZERO and offer not in ('Coupon', 'Promotion'):
                    # Preserve the cash arithmetic even when the meaning of a
                    # negative component is unknown. Never inflate a candidate
                    # by silently dropping a negative sibling.
                    blockers.append('unrecognized_negative_component')
                elif not _missing(offer) and offer not in ('Coupon', 'Promotion'):
                    blockers.append('unrecognized_offer_type')
                if (data.get('Does Order Depend on Another Order', 'No') != 'No' or
                        data.get('Is Order Free Replacement', 'No') != 'No' or
                        any(not _missing(data.get(field, '')) for field in (
                            'Alternative Order Providing Payment', 'PreviouslyPaidOrderId',
                            'Previously Paid Digital Order Item ID', 'Related Physical Order ID',
                            'Paid by Other Customer', 'Item Merged from Another Order'))):
                    blockers.append('linked_or_alternative_payment')
        for item_rows in items.values():
            # Coupon/Promotion components are separate adjustments, not a
            # second charge for the same repeated Price field. Claim codes
            # distinguish separate provider offers without exposing the code.
            identities = Counter((row['data'][component_field],
                '' if _missing(row['data'].get('Offer Type Code', '')) else row['data']['Offer Type Code'],
                '' if _missing(row['data'].get('Claim Code', '')) else row['data']['Claim Code'])
                for row in item_rows)
            if any(count > 1 for count in identities.values()):
                blockers.append('repeated_digital_component')
            if not any(row['data'][component_field] == 'Price Amount' for row in item_rows):
                blockers.append('missing_price_component')
            if refund:
                if (any(row['source_value'] < ZERO for row in item_rows) and
                        any(row['source_value'] > ZERO for row in item_rows)):
                    blockers.append('mixed_refund_component_signs')
                declared = {row['declared_refund'] for row in item_rows}
                component_total = -sum((row['value'] for row in item_rows), ZERO)
                if len(declared) != 1 or component_total != next(iter(declared)):
                    blockers.append('refund_component_total_mismatch')
        amount = sum((row['value'] for row in group), ZERO)
        if not amount:
            exclusions.append(_exclude(group, 'zero_cash_movement', kind))
            continue
        if not refund and amount < ZERO:
            exclusions.append(_exclude(group, 'negative_purchase_total', kind))
            continue
        lines = [_line(row['data']['Product Name'], row['value'],
                       ('discount' if not refund and row['value'] < ZERO else
                        'tax' if row['data'][component_field] == 'Tax' else 'item'), row)
                 for row in group]
        records.append(_record(kind, order_id, 'USD', group[0]['date'].date().isoformat(),
                               amount, group, lines, blockers, card))


def _retail_refunds(rows, records, exclusions):
    # Refund Details contains completed disbursements. Return requests/statuses
    # contain intentions, duplicate corroboration and non-decimal artifacts.
    # Each completed disbursement remains separate; no order-wide summation.
    duplicates = Counter(_digest(row['data']) for row in rows)
    for row in rows:
        data = row['data']
        try:
            if not data.get('Order ID', '').strip():
                raise Uninterpretable('missing_order_id')
            if (data.get('Payment Status') != 'Completed' or
                    data.get('Reversal Status') != 'Completed' or
                    data.get('Reversal Amount State') != 'Final' or
                    data.get('Disbursement Type') != 'Refund'):
                raise Uninterpretable('unsettled_refund')
            if data.get('Currency') != 'USD':
                raise Uninterpretable('unsupported_currency')
            when = _date(data.get('Refund Date'))
            if when < _date(data.get('Creation Date')):
                raise Uninterpretable('refund_precedes_creation')
            amount = -abs(_money(data.get('Refund Amount')))
            if not amount:
                raise Uninterpretable('zero_cash_movement')
            blockers = ['unknown_tender']
            if _money(data.get('Direct Debit Refund Amount')) != ZERO:
                blockers.append('additional_refund_amount')
            if duplicates[_digest(data)] > 1:
                blockers.append('duplicate_source_rows')
            records.append(_record('retail_refund', data['Order ID'].strip(), 'USD',
                when.date().isoformat(), amount, [row],
                [_line('Amazon order refund', amount, 'refund', row)], blockers))
        except Uninterpretable as exc:
            exclusions.append(_exclude([row], str(exc), 'retail_refund'))


def normalize_tables(tables):
    """Return immutable-source candidates and explicit exclusions, without I/O.

    Input tables have ``evidence_id``, ``filename``, ``headers`` and ``rows``.
    Rows are lists of original string cells; row_number 2 is the first data row.
    A record with any blocker is not eligible for automatic bank matching.
    Caller must choose a single archive snapshot: tables from different exports
    must not be combined as additional purchases.
    """
    records, exclusions = [], []
    total_rows = 0
    seen_tables = set()
    handlers = {
        'order history.csv': _retail,
        'digital content orders.csv': _digital,
        'digital returns.csv': lambda rows, records, exclusions: _digital(rows, records, exclusions, refund=True),
        'refund details.csv': _retail_refunds,
    }
    for table in tables:
        table_id = table.get('evidence_id')
        if isinstance(table_id, bool) or not isinstance(table_id, int) or table_id <= 0 or table_id in seen_tables:
            raise ValueError('Every source table must have a distinct positive evidence_id')
        seen_tables.add(table_id)
        headers = table.get('headers')
        if (not isinstance(headers, list) or not headers or
                any(not isinstance(header, str) or not header for header in headers) or
                len(headers) != len(set(headers))):
            raise ValueError('Source table headers must be distinct nonempty strings')
        rows = []
        malformed = False
        for row_number, cells in enumerate(table.get('rows', []), 2):
            total_rows += 1
            ref = dict(table_id=table_id, row_number=row_number)
            if not isinstance(cells, list) or len(cells) != len(headers) or any(not isinstance(cell, str) for cell in cells):
                exclusions.append(_exclude([dict(ref=ref, data={})], 'malformed_row'))
                malformed = True
                continue
            rows.append(dict(ref=ref, data=dict(zip(headers, cells))))
        filename = PurePosixPath(table.get('filename', '')).name.casefold()
        handler = handlers.get(filename)
        if handler:
            first_record = len(records)
            first_exclusion = len(exclusions)
            handler(rows, records, exclusions)
            if malformed or any(row['reason'] == 'missing_order_id' for row in exclusions[first_exclusion:]):
                # Without a valid identity we cannot determine which shipment
                # lost a sibling, so every candidate from that table is held.
                for record in records[first_record:]:
                    record['blockers'] = sorted(set(record['blockers'] + ['incomplete_source_table']))
        else:
            exclusions.extend(_exclude([row], 'companion_or_unsupported_table') for row in rows)
    records.sort(key=lambda record: (record['date'], record['kind'], record['order_id'], record['key']))
    exclusions.sort(key=lambda exclusion: (exclusion['source_rows'][0]['table_id'],
                                           exclusion['source_rows'][0]['row_number'], exclusion['reason']))
    summary = dict(input_rows=total_rows, records=len(records),
        eligible_records=sum(not record['blockers'] for record in records),
        blocked_records=sum(bool(record['blockers']) for record in records),
        normalized_rows=sum(len(record['source_rows']) for record in records),
        excluded_rows=sum(len(exclusion['source_rows']) for exclusion in exclusions),
        exclusions_by_reason=dict(sorted(Counter(exclusion['reason'] for exclusion in exclusions).items())),
        blockers_by_reason=dict(sorted(Counter(reason for record in records for reason in record['blockers']).items())),
        by_kind={kind: dict(records=sum(record['kind'] == kind for record in records),
            amount=_amount(sum((Decimal(record['amount']) for record in records if record['kind'] == kind), ZERO)))
            for kind in KINDS})
    return dict(version=VERSION, records=records, exclusions=exclusions, summary=summary)
