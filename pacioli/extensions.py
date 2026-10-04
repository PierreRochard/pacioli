from flask_admin import Admin
from flask_mail import Mail
from flask_sqlalchemy import SQLAlchemy

from pacioli.views.dashboard_views import HomeView

admin = Admin(url='/',
              base_template='base_master.html',
              name='pacio.li',
              template_mode='bootstrap3',
              index_view=HomeView(url='/'))

mail = Mail()

db = SQLAlchemy()
