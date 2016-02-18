from datetime import datetime

from dateutil.tz import tzlocal
from flask import Blueprint
from flask import url_for, redirect
from flask.ext.admin import expose
from flask.ext.security.utils import encrypt_password
from pacioli.controllers.utilities import date_formatter
from wtforms import StringField

from pacioli.controllers import PacioliModelView
from pacioli.controllers.ofx_views import sync_ofx
from pacioli.extensions import admin
from pacioli.models import (db, User, Role, JournalEntries, Subaccounts,
                            Accounts, Classifications, Elements, Connections, Mappings)


main = Blueprint('main', __name__)


class UserModelView(PacioliModelView):
    column_list = ('id', 'email', 'active', 'confirmed_at', 'current_login_at',
                   'last_login_ip', 'current_login_ip', 'login_count')
    column_formatters = dict(confirmed_at=date_formatter, current_login_at=date_formatter)

admin.add_view(UserModelView(User, db.session, category='Admin'))
admin.add_view(PacioliModelView(Role, db.session, category='Admin'))


class ConnectionsModelView(PacioliModelView):
    list_template = 'connections.html'
    column_list = ('id', 'source', 'type', 'url', 'org', 'fid', 'routing_number',
                   'account_number', 'user', 'synced_at')
    form_choices = dict(type=[('Checking', 'Checking'), ('Savings', 'Savings'), ('Credit Card', 'Credit Card')],
                        source=[('ofx', 'ofx')])
    column_editable_list = column_list[1:]
    column_labels = dict(id='ID', url='URL', fid='FID')
    column_formatters = dict(synced_at=date_formatter)

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


class MappingsModelView(PacioliModelView):
    form_choices = dict(source=[('ofx', 'ofx')])
    column_display_all_relations = True
    column_list = ('id', 'source', 'keyword', 'positive_debit_subaccount',
                   'positive_credit_subaccount', 'negative_debit_subaccount', 'negative_credit_subaccount')
    form_columns = column_list
    column_sortable_list = column_list
    subaccount_loader = dict(fields=('name',), page_size=10, placeholder='-')
    form_ajax_refs = dict(positive_debit_subaccount=subaccount_loader, positive_credit_subaccount=subaccount_loader,
                          negative_debit_subaccount=subaccount_loader, negative_credit_subaccount=subaccount_loader)

    column_editable_list = ('keyword',)

admin.add_view(ConnectionsModelView(Connections, db.session, category='Admin'))
admin.add_view(MappingsModelView(Mappings, db.session, category='Admin'))


class TaxonomyModelView(PacioliModelView):
    form_extra_fields = dict(name=StringField('Name'))
    column_searchable_list = ['name']


admin.add_view(PacioliModelView(JournalEntries, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Subaccounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Accounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Classifications, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Elements, db.session, category='Bookkeeping'))
