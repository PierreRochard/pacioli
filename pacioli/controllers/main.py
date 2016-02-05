from flask import Blueprint
from flask import url_for, redirect, request, abort
from flask.ext.admin.contrib import sqla
from flask_security import current_user

from pacioli.extensions import admin
from pacioli.models import db, User, Role, JournalEntries, Subaccounts, Accounts, Classifications, Elements

main = Blueprint('main', __name__)


def redirect_url(default='index'):
    return request.args.get('next') or request.referrer or url_for(default)


class PacioliModelView(sqla.ModelView):
    def is_accessible(self):
        if not current_user.is_active or not current_user.is_authenticated:
            return False

        if current_user.has_role('superuser'):
            return True

        return False

    def _handle_view(self, name, **kwargs):
        if not self.is_accessible():
            if current_user.is_authenticated:
                abort(403)
            else:
                return redirect(url_for('security.login', next=request.url))

    can_view_details = True
    column_display_pk = True
    column_display_all_relations = False



admin.add_view(PacioliModelView(User, db.session, category='Admin'))
admin.add_view(PacioliModelView(Role, db.session, category='Admin'))

admin.add_view(PacioliModelView(JournalEntries, db.session, category='Bookkeeping'))
admin.add_view(PacioliModelView(Subaccounts, db.session, category='Bookkeeping'))
admin.add_view(PacioliModelView(Accounts, db.session, category='Bookkeeping'))
admin.add_view(PacioliModelView(Classifications, db.session, category='Bookkeeping'))
admin.add_view(PacioliModelView(Elements, db.session, category='Bookkeeping'))
