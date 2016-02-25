from pacioli.controllers import PacioliModelView
from pacioli.extensions import admin
from pacioli.models import (db, AmazonCategories, AmazonItems, AmazonOrders)


class AmazonItemView(PacioliModelView):
    column_list = ('id', 'order_status', 'title', 'quantity', 'purchase_price_per_unit',
                   'item_subtotal', 'item_subtotal_tax', 'item_total', 'currency',
                   'payment_instrument_type', 'category_id', 'shipment_date')

admin.add_view(AmazonItemView(AmazonItems, db.session, category='Amazon'))
admin.add_view(PacioliModelView(AmazonOrders, db.session, category='Amazon'))
admin.add_view(PacioliModelView(AmazonCategories, db.session, category='Amazon'))
