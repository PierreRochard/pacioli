from flask_admin import Admin
from flask_admin.theme import Bootstrap4Theme
from pathlib import Path

from flask_mail import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from pacioli.views.dashboard_views import HomeView

admin = Admin(url='/',
              name='pacio.li',
              theme=Bootstrap4Theme(base_template='base_master.html'),
              index_view=HomeView(url='/'))

mail = Mail()

db = SQLAlchemy()

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations'
migrate = Migrate(directory=str(MIGRATIONS))


def run_sql(statement, parameters=None):
    """Run one statement in its own committed transaction, as Engine.execute did
    before SQLAlchemy 2. Raw strings go to the driver without bind parsing."""
    with db.engine.begin() as connection:
        if isinstance(statement, str):
            result = connection.exec_driver_sql(statement, parameters)
        else:
            result = connection.execute(statement, parameters)
        # Buffer rows so callers can read them after the connection is returned.
        return result.freeze()() if result.returns_rows else result
