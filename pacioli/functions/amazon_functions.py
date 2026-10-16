"""Import Amazon order-history CSV reports and apply bookkeeping mappings."""

import csv
import io
import json
from datetime import datetime, time, timezone
from decimal import Decimal

from dateutil.parser import parse as parse_date
from sqlalchemy import and_, func, or_
from sqlalchemy.types import Date, Integer, Numeric

from pacioli.models import (db, AmazonItems, Mappings, JournalEntries,
                            AmazonCategories, AmazonOrders)
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.accounting_report import chart_accounts


def apply_all_mappings():
    mapping_ids = (db.session.query(Mappings.id)
                   .filter(Mappings.source == 'amazon')
                   .order_by(Mappings.id).all())
    for mapping_id, in mapping_ids:
        apply_single_amazon_mapping(mapping_id)


def apply_single_amazon_mapping(mapping_id):
    from pacioli.models import AmazonEvidenceBundles
    from pacioli.functions.reconciliation import lock_ledger
    lock_ledger('legacy Amazon mapping', 'Check legacy items against reconciled bank settlements')
    mapping = db.session.query(Mappings).filter(Mappings.id == mapping_id).one()
    if mapping.source != 'amazon' or not mapping.keyword:
        raise ValueError('An Amazon mapping with a keyword is required')
    accounts = chart_accounts(db.session)
    for name in (mapping.positive_debit_subaccount_id,
                 mapping.negative_credit_subaccount_id):
        if name and accounts.get(name, (None, False))[0] not in (
                'Assets', 'Expenses', 'Losses'):
            raise ValueError('Amazon purchase account must be an asset or expense: '
                             + name)
    for name in (mapping.positive_credit_subaccount_id,
                 mapping.negative_debit_subaccount_id):
        if name and not (accounts.get(name, (None, False))[0] == 'Liabilities'
                         or accounts.get(name, (None, False))[1]):
            raise ValueError('Amazon settlement account must be a liability or cash: '
                             + name)

    matches = (db.session.query(AmazonItems)
               .outerjoin(JournalEntries, and_(
                   JournalEntries.transaction_id == db.cast(AmazonItems.id, db.String),
                   JournalEntries.transaction_source == 'amazon'))
               .filter(JournalEntries.id.is_(None))
               .filter(or_(func.lower(AmazonItems.title).like('%' + mapping.keyword.lower() + '%'),
                           func.lower(AmazonItems.category_id).like('%' + mapping.keyword.lower() + '%')))
               .order_by(AmazonItems.shipment_date.desc()).all())

    try:
        settled_orders={line['order_id'] for bundle in db.session.query(AmazonEvidenceBundles)
                        .filter(AmazonEvidenceBundles.transaction_id.isnot(None))
                        for line in json.loads(bundle.details)['lines']}
        for item in matches:
            if item.order_id in settled_orders:
                raise ValueError('Order already has reconciled bank allocations; legacy posting would duplicate it')
            if item.shipment_date is None or item.item_total is None or item.item_total == 0:
                continue
            if item.currency != 'USD':
                raise ValueError('Cannot book Amazon item {} without a USD amount'.format(item.id))

            if item.item_total > 0:
                debit = mapping.positive_debit_subaccount_id
                credit = mapping.positive_credit_subaccount_id
            else:
                debit = mapping.negative_debit_subaccount_id
                credit = mapping.negative_credit_subaccount_id
            if not debit or not credit:
                raise ValueError('Amazon mapping {} has incomplete accounts'.format(mapping_id))

            amount = abs(item.item_total)
            post_pair(transaction_id=str(item.id), transaction_source='amazon',
                      mapping_id=mapping_id,
                      timestamp=datetime.combine(item.shipment_date, time.min, tzinfo=timezone.utc),
                      debit=debit, credit=credit, functional_amount=amount)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _normalized_row(row):
    return {key.lstrip('\ufeff').lower().replace('/', '_').replace(' ', '_')
            .replace('&', 'and'): value.strip() if value else None
            for key, value in row.items() if key is not None}


def _money(value):
    if value is None:
        return None
    cleaned = value.replace('$', '').replace(',', '').strip()
    if cleaned.startswith('(') and cleaned.endswith(')'):
        cleaned = '-' + cleaned[1:-1]
    return Decimal(cleaned)


def import_amazon_csv(csv_text):
    """Import a downloaded item report once; return the number of new items."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError('Amazon CSV has no header')

    added = 0
    try:
        for source_row in reader:
            row = _normalized_row(source_row)
            order_id, title = row.get('order_id'), row.get('title')
            if not order_id or not title:
                raise ValueError('Amazon CSV rows require Order ID and Title')
            if (db.session.query(AmazonItems.id)
                    .filter_by(order_id=order_id, title=title).first()):
                continue

            category = row.get('category') or 'Other'
            if db.session.get(AmazonCategories, category) is None:
                db.session.add(AmazonCategories(name=category))
                db.session.flush()
            if db.session.get(AmazonOrders, order_id) is None:
                order_date = row.get('order_date')
                db.session.add(AmazonOrders(
                    id=order_id,
                    order_date=parse_date(order_date).date() if order_date else None))
                db.session.flush()

            item = AmazonItems(order_id=order_id, category_id=category, title=title)
            for column in AmazonItems.__table__.columns:
                name = column.name
                if name in ('id', 'order_id', 'category_id', 'title'):
                    continue
                value = row.get(name)
                if value is None:
                    continue
                if isinstance(column.type, Date):
                    value = parse_date(value).date()
                elif isinstance(column.type, Numeric):
                    value = _money(value)
                elif isinstance(column.type, Integer):
                    value = int(value)
                setattr(item, name, value)
            db.session.add(item)
            db.session.flush()
            added += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return added
