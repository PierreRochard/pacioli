"""Import Amazon order-history CSV reports and apply bookkeeping mappings."""

import csv
import email
from html import unescape
import imaplib
import io
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

import mechanize
from dateutil.parser import parse as parse_date
from sqlalchemy import and_, func, or_
from sqlalchemy.types import Date, Integer, Numeric

from pacioli.models import (db, AmazonItems, Mappings, JournalEntries,
                            Connections, AmazonCategories, AmazonOrders)
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.accounting_report import chart_accounts


REPORTS_URL = 'https://www.amazon.com/gp/b2b/reports'


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


def _amazon_credentials():
    connection = (db.session.query(Connections)
                  .filter(Connections.source == 'amazon').one())
    return connection.user, connection.password


def _browser():
    browser = mechanize.Browser()
    browser.set_handle_robots(False)
    return browser


def _sign_in(browser):
    browser.select_form(name='signIn')
    browser['email'], browser['password'] = _amazon_credentials()
    response = browser.submit()
    content = response.read()
    if b'The e-mail address and password you entered do not match' in content:
        raise RuntimeError('Amazon rejected the configured credentials')
    return content


def request_amazon_report():
    browser = _browser()
    browser.open(REPORTS_URL)
    _sign_in(browser)
    browser.open(REPORTS_URL)
    browser.select_form(nr=2)
    browser.form['type'] = ('ITEMS',)

    latest = (db.session.query(AmazonOrders.order_date)
              .order_by(AmazonOrders.order_date.desc()).first())
    latest_order_date = latest[0] if latest else None
    start_date = (latest_order_date - timedelta(days=5)
                  if latest_order_date else date(2007, 1, 1))
    end_date = date.today()
    for label, value in [('monthstart', start_date.month),
                         ('daystart', start_date.day),
                         ('yearstart', start_date.year),
                         ('monthend', end_date.month),
                         ('dayend', end_date.day),
                         ('yearend', end_date.year)]:
        browser.form[label] = (str(value),)
    browser.submit()


def _report_url(raw_email):
    message = email.message_from_bytes(raw_email)
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b''
        body = unescape(payload.decode(part.get_content_charset() or 'utf-8',
                                       errors='replace'))
        for candidate in re.findall(r"https?://[^\s<>\"']+", body):
            candidate = candidate.rstrip(').,')
            parsed = urlparse(candidate)
            if (parsed.hostname in ('amazon.com', 'www.amazon.com')
                    and parsed.path.startswith('/gp/b2b/reports/')):
                return parsed._replace(scheme='https').geturl()
    raise ValueError('No Amazon order-history report URL was found in the email')


def fetch_amazon_email_download():
    gmail = (db.session.query(Connections)
             .filter(Connections.source == 'gmail').one())
    imap_session = imaplib.IMAP4_SSL(gmail.url)
    try:
        imap_session.login(gmail.user, gmail.password)
        status, _ = imap_session.select('"[Gmail]/All Mail"')
        if status != 'OK':
            raise RuntimeError('Cannot open Gmail All Mail')
        status, data = imap_session.search(None,
                                           '(SUBJECT "Your order history report for")')
        if status != 'OK' or not data or not data[0].split():
            raise LookupError('No Amazon report email was found')
        status, data = imap_session.fetch(data[0].split()[-1], '(RFC822)')
        if status != 'OK':
            raise RuntimeError('Cannot fetch Amazon report email')
        report_url = _report_url(data[0][1])
    finally:
        imap_session.logout()

    browser = _browser()
    browser.open(report_url)
    content = _sign_in(browser)
    return import_amazon_csv(content.decode('utf-8-sig'))


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
            if db.session.query(AmazonCategories).get(category) is None:
                db.session.add(AmazonCategories(name=category))
                db.session.flush()
            if db.session.query(AmazonOrders).get(order_id) is None:
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
