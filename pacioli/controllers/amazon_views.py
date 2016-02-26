from pacioli.controllers import PacioliModelView
from pacioli.extensions import admin
from pacioli.models import (db, AmazonCategories, AmazonItems, AmazonOrders)


class AmazonItemView(PacioliModelView):
    column_list = ('order_status', 'title', 'quantity', 'purchase_price_per_unit',
                   'item_subtotal', 'item_subtotal_tax', 'item_total', 'currency',
                   'payment_instrument_type', 'category_id', 'shipment_date')
    column_filters = column_list
    column_searchable_list = ('title', )
    column_default_sort = ('id', True)
    column_labels = dict(order_status='Status', quantity='#', purchase_price_per_unit='Price', item_subtotal='Subtotal',
                         item_subtotal_tax='Tax', item_total='Total', payment_instrument_type='Payment',
                         category_id='Category', shipment_date='Shipped')

admin.add_view(AmazonItemView(AmazonItems, db.session, category='Amazon'))
admin.add_view(PacioliModelView(AmazonOrders, db.session, category='Amazon'))
admin.add_view(PacioliModelView(AmazonCategories, db.session, category='Amazon'))
