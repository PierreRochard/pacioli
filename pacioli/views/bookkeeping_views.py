from flask import request
from flask_admin import expose
from sqlalchemy import func
from wtforms import StringField

from pacioli.extensions import admin, db
from pacioli.models import (db, JournalEntries, Subaccounts,
                            Accounts, Classifications, Elements, DetailedJournalEntries)
from pacioli.views import PrivateModelView
from pacioli.functions.accounting_functions import journal_period
from pacioli.views.utilities import date_formatter, id_formatter, currency_formatter, string_formatter
from wtforms import TextAreaField


class JournalEntriesView(PrivateModelView):
    can_create = False
    can_edit = False
    can_delete = False
    column_list = ('id',
                   'mapping_id',
                   'transaction_id',
                   'transaction_source',
                   'timestamp',
                   'debit_subaccount',
                   'credit_subaccount',
                   'functional_amount',
                   'description',
                   )

    column_searchable_list = column_list

    column_filters = column_list

    column_sortable_list = column_list
    column_details_list = column_list + ('mapping_decision',)

    # column_default_sort = dict(field='timestamp', sort_desc=True, absolute_value=False)
    column_default_sort = ('timestamp', True)
    column_formatters = dict(transaction_id=id_formatter,
                             timestamp=date_formatter,
                             functional_amount=currency_formatter,
                             description=string_formatter,
                             )

    column_labels = dict(id='JE',
                         mapping_id='Mapping',
                         transaction_id='Tx',
                         transaction_source='Source',
                         timestamp='Date',
                         debit_subaccount='Debit',
                         credit_subaccount='Credit',
                         functional_amount='Amount',
                         mapping_decision='Original mapping decision',
                         )

    def get_query(self):
        if 'subaccount' not in request.view_args:
            return super(JournalEntriesView, self).get_query()
        elif not request.view_args.get('cumulative', None):
            return (db.session.query(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(journal_period(self.model.timestamp, request.view_args['period_interval']) == request.view_args['period']))
        else:
            return (db.session.query(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(journal_period(self.model.timestamp, request.view_args['period_interval']) <= request.view_args['period']))

    def get_count_query(self):
        if 'subaccount' not in request.view_args:
            return super(JournalEntriesView, self).get_count_query()
        elif not request.view_args.get('cumulative', None):
            return (db.session.query(func.count('*')).select_from(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(journal_period(self.model.timestamp, request.view_args['period_interval']) == request.view_args['period']))
        else:
            return (db.session.query(func.count('*')).select_from(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(journal_period(self.model.timestamp, request.view_args['period_interval']) <= request.view_args['period']))

    @expose('/')
    @expose('/<subaccount>/')
    @expose('/<subaccount>/<period_interval>/')
    @expose('/<subaccount>/<period_interval>/<period>/')
    @expose('/<subaccount>/<period_interval>/<period>/<cumulative>/')
    def index_view(self, subaccount=None, period_interval=None, period=None, cumulative=None):
        period_interval = request.view_args.get('period_interval', 'YYYY-MM')
        if not request.view_args.get('period', None):
            latest = (db.session.query(journal_period(JournalEntries.timestamp, period_interval))
                      .order_by(journal_period(JournalEntries.timestamp, period_interval).desc()).first())
            most_recent_period = latest[0] if latest else ''
            request.view_args['period'] = most_recent_period

        self._template_args['period_interval'] = period_interval
        self._template_args['period'] = request.view_args['period']
        self._template_args['period_intervals'] = [('YYYY', 'Annual'), ('YYYY-Q', 'Quarterly'), ('YYYY-MM', 'Monthly'), ('YYYY-MM-DD', 'Daily')]
        self._template_args['periods'] = (db.session.query(journal_period(JournalEntries.timestamp, self._template_args['period_interval']))
                                          .order_by(journal_period(JournalEntries.timestamp, self._template_args['period_interval'])
                                                    .desc()).distinct().limit(30))
        self._template_args['periods'] = [p[0] for p in self._template_args['periods']]
        return super(JournalEntriesView, self).index_view()
admin.add_view(JournalEntriesView(DetailedJournalEntries, db, category='Bookkeeping', endpoint='journalentries', name='Journal Entries'))


class TaxonomyModelView(PrivateModelView):
    form_extra_fields = dict(name=StringField('Name'))
    column_searchable_list = ['name']
    create_modal = True
    edit_modal = True


class SubaccountsModelView(PrivateModelView):
    column_list = ('name', 'description', 'parent', 'tax_tags')
    column_labels = dict(parent='Account')
    column_filters = column_list
    column_searchable_list = column_list[:-1]
    form_overrides = dict(description=TextAreaField)


class AccountsModelView(PrivateModelView):
    column_list = ('name', 'cash_source', 'is_cash', 'parent', 'subaccounts')
    column_labels = dict(parent='Classification')


class ClassificationsModelView(PrivateModelView):
    column_list = ('name', 'parent', 'accounts')
    column_labels = dict(parent='Element')


class ElementsModelView(PrivateModelView):
    column_list = ('name', 'classifications')

admin.add_view(SubaccountsModelView(Subaccounts, db, category='Bookkeeping'))
admin.add_view(AccountsModelView(Accounts, db, category='Bookkeeping'))
admin.add_view(ClassificationsModelView(Classifications, db, category='Bookkeeping'))
admin.add_view(ElementsModelView(Elements, db, category='Bookkeeping'))
