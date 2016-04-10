from pacioli.extensions import admin
from pacioli.models import (db, Paystubs, PaystubItems)
from pacioli.views import PacioliModelView


admin.add_view(PacioliModelView(Paystubs, db.session, category='Payroll'))


class PaystubItemsModelView(PacioliModelView):
    form_choices = dict(description=[('Roth 401k Contribution', 'Roth 401k Contribution'),
                                     ('Bonus Earnings', 'Bonus Earnings'),
                                     ('Federal Income Tax', 'Federal Income Tax'),
                                     ('Long Term Disability Benefit', 'Long Term Disability Benefit'),
                                     ('Long Term Disability Company Portion', 'Long Term Disability Company Portion'),
                                     ('Health Insurance Premium', 'Health Insurance Premium'),
                                     ('Medicare Tax', 'Medicare Tax'),
                                     ('NY City Income Tax', 'NY City Income Tax'),
                                     ('NY State Income Tax', 'NY State Income Tax'),
                                     ('Post-Tax Transit', 'Post-Tax Transit'),
                                     ('Pre-Tax Transit', 'Pre-Tax Transit'),
                                     ('Regular Earnings', 'Regular Earnings'),
                                     ('Social Security Tax', 'Social Security Tax')])
admin.add_view(PaystubItemsModelView(PaystubItems, db.session, category='Payroll'))
