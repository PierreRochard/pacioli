from datetime import datetime

from dateutil.tz import tzlocal
from flask import Blueprint
from flask import url_for, redirect
from flask.ext.admin import expose
from flask.ext.security.utils import encrypt_password
from wtforms import StringField

from pacioli.controllers import PacioliModelView
from pacioli.controllers.ofx_views import sync_ofx
from pacioli.extensions import admin
from pacioli.models import (db, User, Role, JournalEntries, Subaccounts,
                            Accounts, Classifications, Elements, Connections)


main = Blueprint('main', __name__)


admin.add_view(PacioliModelView(User, db.session, category='Admin'))
admin.add_view(PacioliModelView(Role, db.session, category='Admin'))


class ConnectionsModelView(PacioliModelView):
    list_template = 'connections.html'
    column_list = ('id', 'source', 'type', 'url', 'org', 'fid', 'routing_number',
                   'account_number', 'user', 'synced_at')
    form_choices = dict(type=[('Checking', 'Checking'), ('Savings', 'Savings'), ('Credit Card', 'Credit Card')],
                        source=[('ofx', 'ofx')])

    def create_model(self, form):
        # TODO: store the password in an encrypted form
        # form.password.data = encrypt_password(form.password.data)
        super(PacioliModelView, self).create_model(form)

    def after_model_change(self, form, model, is_created):
        if is_created:
            model.created_at = datetime.now(tzlocal())
            db.session.commit()

    @expose('/sync_connections/')
    def sync_connections(self):
        sync_ofx()
        return redirect(url_for('connections.index_view'))


admin.add_view(ConnectionsModelView(Connections, db.session, category='Admin'))


class TaxonomyModelView(PacioliModelView):
    form_extra_fields = dict(name=StringField('Name'))
    column_searchable_list = ['name']


admin.add_view(PacioliModelView(JournalEntries, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Subaccounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Accounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Classifications, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Elements, db.session, category='Bookkeeping'))
