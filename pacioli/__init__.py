from flask import Flask, url_for
from flask_security import Security
from flask_admin import helpers as admin_helpers
from flask_wtf.csrf import generate_csrf

from pacioli import settings
from pacioli.extensions import admin, mail, db
from pacioli.models import Users, Roles, user_datastore


def create_app(config_object, env="prod"):
    app = Flask(__name__)

    app.config.from_object(config_object)
    app.config['ENV'] = env
    app.jinja_env.globals['action_csrf_token'] = generate_csrf
    db.init_app(app)
    mail.init_app(app)
    security = Security(app, user_datastore)

    @security.context_processor
    def security_context_processor():
        return dict(admin_base_template=admin.theme.base_template,
                    theme=admin.theme,
                    admin_view=admin.index_view,
                    h=admin_helpers,
                    get_url=url_for)

    admin.init_app(app, url='/')

    from pacioli.admin_setup import register_admin_setup
    register_admin_setup(app)

    return app
