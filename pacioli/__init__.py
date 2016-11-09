from flask import Flask, url_for
from flask_security import Security
from flask_admin import helpers as admin_helpers

from pacioli import settings
from pacioli.extensions import admin, mail, db
from pacioli.models import Users, Roles, user_datastore


def create_app(config_object, env="prod"):
    app = Flask(__name__)

    app.config.from_object(config_object)
    app.config['ENV'] = env
    db.init_app(app)
    mail.init_app(app)
    security = Security(app, user_datastore)

    @security.context_processor
    def security_context_processor():
        return dict(admin_base_template=admin.base_template,
                    admin_view=admin.index_view,
                    h=admin_helpers,
                    get_url=url_for)

    admin.init_app(app)

    return app
