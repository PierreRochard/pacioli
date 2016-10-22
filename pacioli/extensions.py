from flask_admin import Admin
from flask_mail import Mail
from flask_sqlalchemy import SQLAlchemy

admin = Admin(url='/',
              base_template='base_master.html',
              name='pacio.li',
              template_mode='bootstrap3')

mail = Mail()

db = SQLAlchemy()
