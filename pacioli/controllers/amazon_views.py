from pacioli.controllers import PacioliModelView
from pacioli.extensions import admin
from pacioli.models import (db, AmazonCategories, AmazonItems, AmazonOrders)


class AmazonItemView(PacioliModelView):
    can_edit = False
    can_create = False
    can_delete = False
    can_export = True
    column_display_actions = False

    column_list = ('id', 'order_status', 'title', 'quantity', 'purchase_price_per_unit',
                   'item_subtotal', 'item_subtotal_tax', 'item_total', 'currency',
                   'payment_instrument_type', 'category_id', 'shipment_date')
    column_filters = column_list
    column_searchable_list = ('title', )
    column_default_sort = {'field': 'id', 'sort_desc': True, 'absolute_value': False}
    column_labels = dict(id='ID', order_status='Status', quantity='#', purchase_price_per_unit='Price',
                         item_subtotal='Subtotal',
                         item_subtotal_tax='Tax', item_total='Total', payment_instrument_type='Payment',
                         category_id='Category', shipment_date='Shipped')

admin.add_view(AmazonItemView(AmazonItems, db.session, category='Amazon'))


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
