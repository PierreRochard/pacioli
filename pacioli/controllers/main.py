from datetime import datetime

from dateutil.tz import tzlocal
from flask import url_for, redirect
from flask.ext.admin import expose
from flask.ext.security.utils import encrypt_password
from pacioli.controllers.utilities import date_formatter, id_formatter, currency_formatter
from sqlalchemy import text
from wtforms import StringField

from pacioli.controllers import PacioliModelView
from pacioli.controllers.ofx_views import sync_ofx
from pacioli.extensions import admin
from pacioli.models import (db, User, Role, JournalEntries, Subaccounts,
                            Accounts, Classifications, Elements, Connections, Mappings, TrialBalances)


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
                        source=[('ofx', 'ofx'), ('amazon', 'amazon'), ('gmail', 'gmail')])
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


class JournalEntriesView(PacioliModelView):
    column_list = ('transaction_id', 'transaction_source', 'timestamp', 'debit_subaccount', 'credit_subaccount', 'functional_amount')
    column_searchable_list = column_list
    column_default_sort = ('timestamp', True)
    column_filters = column_list
    column_sortable_list = column_list
    column_formatters = dict(transaction_id=id_formatter, timestamp=date_formatter, functional_amount=currency_formatter)


admin.add_view(JournalEntriesView(JournalEntries, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Subaccounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Accounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Classifications, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Elements, db.session, category='Bookkeeping'))


class TrialBalancesView(PacioliModelView):
    list_template = 'trial_balances.html'
    column_list = ('id', 'period', 'period_interval', 'subaccount', 'debit_balance', 'credit_balance', 'debit_changes', 'credit_changes')
    column_default_sort = ('period', True)
    column_searchable_list = ['subaccount']
    column_filters = column_list
    column_sortable_list = column_list
    column_formatters = dict(debit_balance=currency_formatter, credit_balance=currency_formatter,
                             debit_changes=currency_formatter, credit_changes=currency_formatter)
    can_edit = False
    can_create = False
    can_delete = False
    can_export = True

    @expose('/refresh_subaccounts/')
    def refresh_all_subaccounts(self):
        connection = db.engine.connect()
        transaction = connection.begin()
        connection.execute('TRUNCATE pacioli.trial_balances RESTART IDENTITY CASCADE;')
        transaction.commit()
        transaction.close()
        query_text = text('SELECT pacioli.update_trial_balance(:debit_subaccount, :credit_subaccount, :period_interval_name, :period_name);')
        for debit_subaccount, credit_subaccount in db.session.query(JournalEntries.debit_subaccount, JournalEntries.credit_subaccount).distinct():
            for period_interval_name in ['YYYY', 'YYYY-Q', 'YYYY-MM', 'YYYY-WW', 'YYYY-MM-DD']:
                for period_name in db.session.query(db.func.to_char(JournalEntries.timestamp, period_interval_name)).distinct():
                    transaction = connection.begin()
                    connection.execute(query_text, debit_subaccount=debit_subaccount, credit_subaccount=credit_subaccount, period_interval_name=period_interval_name, period_name=period_name)
                    transaction.commit()
                    transaction.close()
        connection.close()
        return redirect(url_for('trialbalances.index_view'))


admin.add_view(TrialBalancesView(TrialBalances, db.session, category='Accounting'))
