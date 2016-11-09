from datetime import datetime

from dateutil.tz import tzlocal
from flask import url_for, redirect
from flask_admin import expose
from pacioli.extensions import admin
from pacioli.functions.ofx_functions import sync_ofx
from pacioli.models import (db, Users, Roles, Connections, Mappings, ConnectionResponses, MappingOverlaps)
from pacioli.views import PrivateModelView
from pacioli.views.utilities import date_formatter, link_mapping_formatter, link_transaction_search_formatter


class UserModelView(PrivateModelView):
    column_list = ('id', 'email', 'active', 'confirmed_at', 'current_login_at',
                   'last_login_ip', 'current_login_ip', 'login_count')
    column_formatters = dict(confirmed_at=date_formatter, current_login_at=date_formatter)


admin.add_view(UserModelView(Users, db.session, category='Admin'))
admin.add_view(PrivateModelView(Roles, db.session, category='Admin'))


class ConnectionsModelView(PrivateModelView):
    list_template = 'connections.html'
    column_list = ('id', 'source', 'type', 'url', 'org', 'fid', 'routing_number',
                   'account_number', 'user', 'synced_at')
    form_choices = dict(type=[('Checking', 'Checking'), ('Savings', 'Savings'), ('Credit Card', 'Credit Card')],
                        source=[('ofx', 'ofx'), ('amazon', 'amazon'), ('gmail', 'gmail')])
    column_editable_list = column_list[1:]
    column_labels = dict(id='ID', url='URL', fid='FID')
    column_formatters = dict(synced_at=date_formatter)

    def create_model(self, form):
        # TODO: store the password in an encrypted form
        # form.password.data = encrypt_password(form.password.data)
        super(PrivateModelView, self).create_model(form)

    def after_model_change(self, form, model, is_created):
        if is_created:
            model.created_at = datetime.now(tzlocal())
            db.session.commit()

    @expose('/sync_connections/')
    def sync_connections(self):
        sync_ofx()
        return redirect(url_for('connections.index_view'))
admin.add_view(ConnectionsModelView(Connections, db.session, category='Admin'))


class ConnectionResponsesView(PrivateModelView):
    can_create = False
    can_delete = False
    can_edit = False
    column_default_sort = {'field': 'connected_at', 'sort_desc': True, 'absolute_value': False}
    column_list = ('id', 'connection', 'connected_at', 'response')
    column_sortable_list = column_list
    column_filters = column_list
    column_labels = dict(id='ID')
    column_display_actions = False
    column_formatters = dict(connected_at=date_formatter)

admin.add_view(ConnectionResponsesView(ConnectionResponses, db.session, category='Admin', endpoint='connection-responses'))


class MappingsModelView(PrivateModelView):
    column_labels = dict(id='ID')
    form_choices = dict(source=[('ofx', 'ofx')])
    column_display_all_relations = True
    column_list = ('id', 'source', 'keyword', 'positive_debit_subaccount',
                   'positive_credit_subaccount', 'negative_debit_subaccount', 'negative_credit_subaccount')
    form_columns = column_list
    column_sortable_list = column_list
    column_filters = ('id', 'source', 'keyword')
    column_searchable_list = ('keyword', )
    subaccount_loader = dict(fields=('name',), page_size=10, placeholder='-')
    form_ajax_refs = dict(positive_debit_subaccount=subaccount_loader, positive_credit_subaccount=subaccount_loader,
                          negative_debit_subaccount=subaccount_loader, negative_credit_subaccount=subaccount_loader)
    column_editable_list = ('keyword',)
admin.add_view(MappingsModelView(Mappings, db.session, category='Admin'))


class MappingOverlapsModelView(PrivateModelView):
    can_create = False
    can_delete = False
    can_edit = False
    column_display_actions = False
    column_labels = dict(mapping_id_1='Mapping ID 1', mapping_id_2='Mapping ID 2')
    column_formatters = dict(mapping_id_1=link_mapping_formatter, mapping_id_2=link_mapping_formatter,
                             mapping_keyword_1=link_transaction_search_formatter, mapping_keyword_2=link_transaction_search_formatter)
admin.add_view(MappingOverlapsModelView(MappingOverlaps, db.session, category='Admin', name='Mapping Overlaps', endpoint='mapping-overlaps'))
