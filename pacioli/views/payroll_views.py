from pacioli.extensions import admin
from pacioli.models import (db, Paystubs, PaystubItems)
from pacioli.views import PacioliModelView

admin.add_view(PacioliModelView(Paystubs, db.session, category='Payroll'))


class PaystubItemsModelView(PacioliModelView):
    form_choices = dict(description=[('Bonus Earnings', 'Bonus Earnings'),
                                     ('Federal Income Tax', 'Federal Income Tax'),
                                     ('Health Insurance Premium', 'Health Insurance Premium'),
                                     ('Long Term Disability Benefit', 'Long Term Disability Benefit'),
                                     ('Long Term Disability Company Portion', 'Long Term Disability Company Portion'),
                                     ('Medicare Tax', 'Medicare Tax'),
                                     ('NY City Income Tax', 'NY City Income Tax'),
                                     ('NY State Income Tax', 'NY State Income Tax'),
                                     ('Post-Tax Transit', 'Post-Tax Transit'),
                                     ('Pre-Tax Transit', 'Pre-Tax Transit'),
                                     ('Regular Earnings', 'Regular Earnings'),
                                     ('Roth 401k Contribution', 'Roth 401k Contribution'),
                                     ('Social Security Tax', 'Social Security Tax')])
    form_columns = ('description', 'this_period', 'year_to_date', 'statutory', 'paystub', 'rate', 'hours')

admin.add_view(PaystubItemsModelView(PaystubItems, db.session, category='Payroll'))
