from flask.ext.admin import Admin
from flask.ext.cache import Cache
from flask.ext.debugtoolbar import DebugToolbarExtension
from flask.ext.mail import Mail
from flask_assets import Environment

cache = Cache()

assets_env = Environment()

debug_toolbar = DebugToolbarExtension()

admin = Admin(url='/',
              base_template='base_master.html',
              name='pacio.li',
              template_mode='bootstrap3')

mail = Mail()