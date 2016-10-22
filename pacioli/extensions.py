from flask.ext.admin import Admin
from flask.ext.mail import Mail
from flask.ext.sqlalchemy import SQLAlchemy

admin = Admin(url='/',
              base_template='base_master.html',
              name='pacio.li',
              template_mode='bootstrap3')

mail = Mail()

db = SQLAlchemy()
