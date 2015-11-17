import os

from flask import Flask
from flask.ext.security import Security
from webassets.loaders import PythonLoader as PythonAssetsLoader
from flask_admin import helpers as admin_helpers
from flask_mail import Mail

from pacioli import assets
from pacioli.models import db, User, Role, user_datastore
from pacioli.controllers.main import main

from pacioli.extensions import (
    cache,
    assets_env,
    debug_toolbar,
    admin
)

environment = os.environ.get('pacioli_ENV', 'prod')


def create_app(object_name='pacioli.settings.%sConfig' % environment.capitalize(), env="prod"):
    """
    An flask application factory, as explained here:
    http://flask.pocoo.org/docs/patterns/appfactories/

    Arguments:
        object_name: the python path of the config object,
                     e.g. pacioli.settings.ProdConfig

        env: The name of the current environment, e.g. prod or dev
    """

    app = Flask(__name__)

    app.config.from_object(object_name)
    app.config['ENV'] = env

    # initialize the cache
    cache.init_app(app)

    # initialize the debug tool bar
    debug_toolbar.init_app(app)

    # initialize SQLAlchemy
    db.init_app(app)

    mail = Mail(app)

    security = Security(app, user_datastore)

    @security.context_processor
    def security_context_processor():
        return dict(
            admin_base_template=admin.base_template,
            admin_view=admin.index_view,
            h=admin_helpers,
        )

    admin.init_app(app)

    # Import and register the different asset bundles
    assets_env.init_app(app)
    assets_loader = PythonAssetsLoader(assets)
    for name, bundle in assets_loader.load_bundles().items():
        assets_env.register(name, bundle)

    # register our blueprints
    app.register_blueprint(main)

    return app
