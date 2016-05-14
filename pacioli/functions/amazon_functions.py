import csv
import email
import imaplib
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql.elements import or_
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
import mechanize

from pacioli.models import (db, AmazonItems, Subaccounts, Mappings, JournalEntries, Connections, AmazonCategories, AmazonOrders)


def create_amazon_views():
    try:
        db.engine.execute('DROP VIEW amazon.amazon_transactions;')
    except ProgrammingError:
        pass
    db.engine.execute("""
    CREATE VIEW amazon.amazon_transactions AS SELECT amazon.items.*,
            bookkeeping.journal_entries.id AS journal_entry_id
        FROM amazon.items
        LEFT OUTER JOIN bookkeeping.journal_entries ON cast(amazon.items.id AS CHARACTER VARYING) = bookkeeping.journal_entries.transaction_id
          AND bookkeeping.journal_entries.transaction_source = 'amazon'
        ORDER BY amazon.items.shipment_date DESC;
    """)


def apply_all_mappings():
    for mapping in db.session.query(Mappings).all():
        matches = (db.session.query(AmazonItems)
                   .outerjoin(JournalEntries, JournalEntries.transaction_id == str(AmazonItems.id))
                   .filter(JournalEntries.transaction_id.is_(None))
                   .filter(or_(func.lower(AmazonItems.title).like('%' + mapping.keyword.lower() + '%'),
                               func.lower(AmazonItems.category_id).like('%' + mapping.keyword.lower() + '%')))
                   .order_by(AmazonItems.shipment_date.desc()).all())
        for match in matches:
            new_journal_entry = JournalEntries()
            new_journal_entry.transaction_id = match.id
            new_journal_entry.transaction_source = 'amazon'
            new_journal_entry.timestamp = match.shipment_date
            if match.amount > 0:
                try:
                    db.session.query(Subaccounts).filter(Subaccounts.name == mapping.positive_debit_subaccount_id).one()
                except NoResultFound:
                    new_subaccount = Subaccounts()
                    new_subaccount.name = mapping.positive_debit_subaccount_id
                    new_subaccount.parent = 'Discretionary Costs'
                    db.session.add(new_subaccount)
                    db.session.commit()
                new_journal_entry.debit_subaccount = mapping.positive_debit_subaccount_id
                new_journal_entry.credit_subaccount = mapping.positive_credit_subaccount_id
            else:
                raise Exception()
            new_journal_entry.functional_amount = match.item_total
            new_journal_entry.functional_currency = 'USD'
            new_journal_entry.source_amount = match.item_total
            new_journal_entry.source_currency = 'USD'
            db.session.add(new_journal_entry)
            db.session.commit()


def apply_single_amazon_mapping(mapping_id):
    mapping = db.session.query(Mappings).filter(Mappings.id == mapping_id).one()
    matches = (db.session.query(AmazonItems)
               .outerjoin(JournalEntries, JournalEntries.transaction_id == str(AmazonItems.id))
               .filter(JournalEntries.transaction_id.is_(None))
               .filter(or_(func.lower(AmazonItems.title).like('%' + mapping.keyword.lower() + '%'),
                           func.lower(AmazonItems.category_id).like('%' + mapping.keyword.lower() + '%')))
               .order_by(AmazonItems.shipment_date.desc()).all())
    for match in matches:
        new_journal_entry = JournalEntries()
        new_journal_entry.transaction_id = match.id
        new_journal_entry.mapping_id = mapping_id
        new_journal_entry.transaction_source = 'amazon'
        new_journal_entry.timestamp = match.shipment_date
        if match.item_total > 0:
            new_journal_entry.debit_subaccount = mapping.positive_debit_subaccount_id
            new_journal_entry.credit_subaccount = mapping.positive_credit_subaccount_id
        else:
            raise Exception()
        new_journal_entry.functional_amount = match.item_total
        new_journal_entry.functional_currency = 'USD'
        new_journal_entry.source_amount = match.item_total
        new_journal_entry.source_currency = 'USD'
        db.session.add(new_journal_entry)
        db.session.commit()


def request_amazon_report():
    br = mechanize.Browser()
    br.set_handle_robots(False)
    br.addheaders = [('User-agent',
                      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_5) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/48.0.2564.109 Safari/537.36')]
    br.open('https://www.amazon.com/gp/b2b/reports')
    br.select_form(name='signIn')
    br['email'], br['password'] = (db.session.query(Connections.user, Connections.password)
                                   .filter(Connections.source == 'amazon').one())
    logged_in = br.submit()

    error_str = 'The e-mail address and password you entered do not match any accounts on record.'
    if error_str in logged_in.read():
        print(error_str)

    url = 'https://www.amazon.com/gp/b2b/reports'
    page = br.open(url)
    br.select_form(nr=2)
    # print(br.form)
    br.form['type'] = ('ITEMS',)
    latest_order_date, = (db.session.query(AmazonOrders.order_date)
                          .order_by(AmazonOrders.order_date.desc()).first())
    if not latest_order_date:
        start_date = datetime(2007, 1, 1)
    else:
        start_date = (latest_order_date - timedelta(days=5)).date()

    end_date = datetime.today().date()
    br.form['monthstart'] = (str(start_date.month),)
    br.form['daystart'] = (str(start_date.day),)
    br.form['yearstart'] = (str(start_date.year),)
    br.form['monthend'] = (str(end_date.month),)
    br.form['dayend'] = (str(end_date.day),)
    br.form['yearend'] = (str(end_date.year),)
    br.submit()


def fetch_amazon_email_download():
    imap_server, imap_user, imap_password = (db.session.query(Connections.url, Connections.user,
                                                              Connections.password)
                                             .filter(Connections.source == 'gmail').first())
    imap_session = imaplib.IMAP4_SSL(imap_server)
    imap_session.login(imap_user, imap_password)
    imap_session.list()
    imap_session.select('"[Gmail]/All Mail"')
    result, data = imap_session.search(None, '(SUBJECT "Your order history report for")')
    ids = data[0]
    email_id_list = ids.split()
    email_id = email_id_list[-1]
    result, data = imap_session.fetch(email_id, '(RFC822)')
    raw_email = data[0][1]
    email_message = email.message_from_string(raw_email)
    report_url = None
    for fragment in str(email_message).split():
        if fragment.startswith('http://www.amazon.com/gp/b2b/reports/?ie=UTF8&'):
            report_url = fragment[:-1]
    assert report_url
    imap_session.close()
    imap_session.logout()

    br = mechanize.Browser()
    br.set_handle_robots(False)
    br.addheaders = [('User-agent',
                      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_5) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/48.0.2564.109 Safari/537.36')]
    br.open(report_url)
    br.select_form(name='signIn')
    br['email'], br['password'] = (db.session.query(Connections.user, Connections.password)
                                   .filter(Connections.source == 'amazon').first())
    csv_file = br.submit()
    reader = csv.DictReader(csv_file)
    for row in reader:
        new_amazon_item = AmazonItems()
        for key in row:
            row[key.lower().replace('/', '_').replace(' ', '_').replace('&', 'and')] = row.pop(key)
        if not row['category']:
            row['category'] = 'Other'
        for column in inspect(AmazonItems).attrs:
            if column.key == 'category_id':
                new_amazon_category = AmazonCategories()
                new_amazon_category.name = row['category']
                try:
                    db.session.add(new_amazon_category)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                setattr(new_amazon_item, column.key, row['category'])
            elif column.key == 'order_id':
                new_amazon_order = AmazonOrders()
                new_amazon_order.id = row['order_id']
                new_amazon_order.order_date = row['order_date']
                try:
                    db.session.add(new_amazon_order)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                setattr(new_amazon_item, column.key, row[column.key])
            elif column.key in ['id', 'order', 'category']:
                continue
            else:
                assert len(column.columns) == 1
                if str(column.columns[0].type) == 'NUMERIC':
                    if row[column.key].startswith('$'):
                        row[column.key] = row[column.key].replace('$', '').replace(',', '')
                if not row[column.key]:
                    row[column.key] = None
                setattr(new_amazon_item, column.key, row[column.key])
        try:
            db.session.add(new_amazon_item)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
