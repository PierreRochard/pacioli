from flask import url_for, redirect, request, abort
from flask_security import current_user
from flask import Blueprint
from flask.ext.admin.contrib import sqla
from flask.ext.admin.contrib.sqla import ModelView

from pacioli.extensions import admin
from pacioli.models import db, User, Role

main = Blueprint('main', __name__)


class MyModelView(sqla.ModelView):
    def is_accessible(self):
        if not current_user.is_active or not current_user.is_authenticated:
            return False

        if current_user.has_role('superuser'):
            return True

        return False

    def _handle_view(self, name, **kwargs):
        """
        Override builtin _handle_view in order to redirect users when a view is not accessible.
        """
        if not self.is_accessible():
            if current_user.is_authenticated():
                # permission denied
                abort(403)
            else:
                # login
                return redirect(url_for('security.login', next=request.url))


admin.add_view(MyModelView(User, db.session))
admin.add_view(MyModelView(Role, db.session))

