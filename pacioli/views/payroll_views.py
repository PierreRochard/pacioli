from pacioli.extensions import admin
from pacioli.models import (db, Paystubs, PaystubItems)
from pacioli.views import PacioliModelView

admin.add_view(PacioliModelView(Paystubs, db.session, category='Payroll'))
admin.add_view(PacioliModelView(PaystubItems, db.session, category='Payroll'))
