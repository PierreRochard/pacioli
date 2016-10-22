from flask import request, redirect, url_for
from flask_admin import expose
from flask_admin.contrib.sqla.ajax import QueryAjaxModelLoader
from flask_admin.model.fields import AjaxSelectField
from pacioli.functions.amazon_functions import apply_single_amazon_mapping, apply_all_mappings
from sqlalchemy.exc import IntegrityError
from wtforms import Form, HiddenField

from pacioli.extensions import admin
from pacioli.models import (db, AmazonCategories, AmazonOrders, AmazonTransactions, Subaccounts, Mappings)
from pacioli.views import PrivateModelView


class AmazonItemView(PrivateModelView):
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


class AmazonOrdersView(PrivateModelView):
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


class AmazonCategoriesView(PrivateModelView):
    can_edit = False
    can_create = False
    can_delete = False
    can_export = True
    column_display_actions = False
    column_default_sort = {'field': 'name', 'sort_desc': False, 'absolute_value': False}
admin.add_view(AmazonCategoriesView(AmazonCategories, db.session, category='Amazon'))
