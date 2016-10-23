import logging
import sys

from pacioli import create_app

logging.basicConfig(stream=sys.stderr)

app = create_app('pacioli.settings.ProdConfig', env='prod')


from pacioli.models import register_models
with app.app_context():
    register_models()

import pacioli.views.admin_views
import pacioli.views.bookkeeping_views
import pacioli.views.accounting_views
import pacioli.views.ofx_views
import pacioli.views.amazon_views
import pacioli.views.payroll_views