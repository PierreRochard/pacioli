from flask import request, redirect, url_for, current_app
from flask.ext.admin import expose
from flask.ext.admin.contrib.sqla.ajax import QueryAjaxModelLoader
from flask.ext.admin.model.fields import AjaxSelectField
from pacioli.views import PacioliModelView
from pacioli.extensions import admin
from pacioli.models import (db, AmazonCategories, AmazonItems, AmazonOrders, Subaccounts, Mappings, JournalEntries)
from sqlalchemy import func, PrimaryKeyConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql.elements import or_
from wtforms import Form, HiddenField


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


def register_amazon():
    db.metadata.reflect(bind=db.engine, schema='amazon', views=True, only=current_app.config['AMAZON_MODEL_MAP'].keys())
    db.metadata.tables['amazon.amazon_transactions'].append_constraint(PrimaryKeyConstraint('id', name='amazon_transactions_pk'))
    Base = automap_base(metadata=db.metadata)
    Base.prepare()
    for cls in Base.classes:
        if cls.__table__.name in current_app.config['AMAZON_MODEL_MAP']:
            globals()[current_app.config['AMAZON_MODEL_MAP'][cls.__table__.name]] = cls

    class AmazonItemView(PacioliModelView):
        list_template = 'amazon_items.html'

        can_edit = False
        can_create = False
        can_delete = False
        can_export = True
        column_display_actions = False

        column_list = ('id', 'journal_entry_id', 'order_status', 'title', 'quantity', 'purchase_price_per_unit',
                       'item_subtotal', 'item_subtotal_tax', 'item_total', 'currency',
                       'payment_instrument_type', 'category_id', 'shipment_date')
        column_filters = column_list
        column_searchable_list = ('title', 'category_id')
        column_default_sort = {'field': 'id', 'sort_desc': True, 'absolute_value': False}
        column_labels = dict(id='ID', journal_entry_id='JE', order_status='Status', quantity='#',
                             purchase_price_per_unit='Price', item_subtotal='Subtotal',
                             item_subtotal_tax='Tax', item_total='Total', payment_instrument_type='Payment',
                             category_id='Category', shipment_date='Shipped')

        ajax_subaccount_loader = QueryAjaxModelLoader('subaccounts', db.session, Subaccounts, fields=['name'],
                                                      page_size=10, placeholder='Expense Subaccount')
        form_ajax_refs = {'subaccounts': ajax_subaccount_loader}

        @expose('/', methods=('GET', 'POST'))
        def index_view(self):
            if request.method == 'POST':
                form = request.form.copy().to_dict()
                new_mapping = Mappings()
                new_mapping.source = 'amazon'
                new_mapping.keyword = form['keyword']
                new_mapping.positive_debit_subaccount_id = form['subaccount']
                new_mapping.positive_credit_subaccount_id = 'Amazon Suspense Account'
                new_mapping.negative_debit_subaccount_id = 'Amazon Suspense Account'
                new_mapping.negative_credit_subaccount_id = form['subaccount']

                try:
                    db.session.add(new_mapping)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                mapping_id, = (db.session.query(Mappings.id).filter(Mappings.source == 'amazon')
                               .filter(Mappings.keyword == form['keyword']).one())
                apply_single_amazon_mapping(mapping_id)
                return redirect(url_for('amazonitems.index_view'))

            class NewTransactionMapping(Form):
                keyword = HiddenField()
                subaccount = AjaxSelectField(loader=self.ajax_subaccount_loader, allow_blank=False)

            new_mapping_form = NewTransactionMapping()

            self._template_args['new_mapping_form'] = new_mapping_form
            return super(AmazonItemView, self).index_view()

        @expose('/apply-all-mappings/')
        def apply_all_mappings_view(self):
            apply_all_mappings()
            return redirect(url_for('amazonitems.index_view'))
    admin.add_view(AmazonItemView(AmazonTransactions, db.session, endpoint='amazonitems', name='Amazon Items', category='Amazon'))

    class AmazonOrdersView(PacioliModelView):
        can_edit = False
        can_create = False
        can_delete = False
        can_export = True
        column_display_actions = False

        column_list = ('id', 'order_date')
        column_filters = column_list
        column_searchable_list = column_list
        column_default_sort = {'field': 'order_date', 'sort_desc': True, 'absolute_value': False}
        column_labels = dict(id='ID')
    admin.add_view(AmazonOrdersView(AmazonOrders, db.session, category='Amazon'))

    class AmazonCategoriesView(PacioliModelView):
        can_edit = False
        can_create = False
        can_delete = False
        can_export = True
        column_display_actions = False
        column_default_sort = {'field': 'name', 'sort_desc': False, 'absolute_value': False}
    admin.add_view(AmazonCategoriesView(AmazonCategories, db.session, category='Amazon'))
