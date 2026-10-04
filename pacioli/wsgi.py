import logging
import sys

from raven.contrib.flask import Sentry

from pacioli import create_app

logging.basicConfig(stream=sys.stderr)

app = create_app('pacioli.settings.ProdConfig', env='Prod')

# Raven reads client options from SENTRY_CONFIG when init_app creates its client.
# Never attach submitted credentials or stack locals to an error report.
app.config['SENTRY_CONFIG'] = {
    'capture_locals': False,
    'processors': (
        'raven.processors.RemovePostDataProcessor',
        'raven.processors.RemoveStackLocalsProcessor',
        'raven.processors.SanitizePasswordsProcessor',
    ),
}
sentry = Sentry()
sentry.init_app(app)

from pacioli.models import register_views
with app.app_context():
    register_views()

import pacioli.views.admin_views
import pacioli.views.bookkeeping_views
import pacioli.views.accounting_views
import pacioli.views.ofx_views
import pacioli.views.amazon_views
import pacioli.views.payroll_views
import pacioli.views.tax_views
