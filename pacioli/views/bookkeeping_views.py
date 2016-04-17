from flask import request
from flask.ext.admin import expose
from pacioli.extensions import admin
from pacioli.models import (db, JournalEntries, Subaccounts,
                            Accounts, Classifications, Elements, DetailedJournalEntries)
from pacioli.views import PacioliModelView
from pacioli.views.utilities import date_formatter, id_formatter, currency_formatter, string_formatter
from sqlalchemy import func
from wtforms import StringField


class JournalEntriesView(PacioliModelView):
    column_list = ('transaction_id', 'transaction_source', 'timestamp', 'debit_subaccount',
                   'credit_subaccount', 'functional_amount', 'description')
    # column_editable_list = ['debit_subaccount', 'credit_subaccount']
    column_searchable_list = column_list
    column_default_sort = {'field': 'timestamp', 'sort_desc': True, 'absolute_value': False}
    column_filters = column_list
    column_sortable_list = column_list
    column_formatters = dict(transaction_id=id_formatter, timestamp=date_formatter,
                             functional_amount=currency_formatter, description=string_formatter)

    def get_query(self):
        if 'subaccount' not in request.view_args:
            return super(JournalEntriesView, self).get_query()
        elif not request.view_args['period_interval']:
            return (self.session.query(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount'])))
        elif not request.view_args['cumulative']:
            return (self.session.query(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(db.func.to_char(self.model.timestamp, request.view_args['period_interval']) == request.view_args['period']))
        else:
            return (self.session.query(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(db.func.to_char(self.model.timestamp, request.view_args['period_interval']) <= request.view_args['period']))

    def get_count_query(self):
        if 'subaccount' not in request.view_args:
            return super(JournalEntriesView, self).get_count_query()
        elif not request.view_args['period_interval']:
            return (self.session.query(func.count('*')).select_from(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount'])))
        elif not request.view_args['cumulative']:
            return (self.session.query(func.count('*')).select_from(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(db.func.to_char(self.model.timestamp, request.view_args['period_interval']) == request.view_args['period']))
        else:
            return (self.session.query(func.count('*')).select_from(self.model)
                    .filter(db.or_(self.model.debit_subaccount == request.view_args['subaccount'],
                                   self.model.credit_subaccount == request.view_args['subaccount']))
                    .filter(db.func.to_char(self.model.timestamp, request.view_args['period_interval']) <= request.view_args['period']))

    @expose('/')
    @expose('/<subaccount>/')
    @expose('/<subaccount>/<period_interval>/')
    @expose('/<subaccount>/<period_interval>/<period>/')
    @expose('/<subaccount>/<period_interval>/<period>/<cumulative>/')
    def index_view(self, subaccount=None, period_interval=None, period=None, cumulative=None):
        period_interval = request.view_args.get('period_interval', 'YYYY-MM')
        if not request.view_args.get('period', None):
            most_recent_period, = (self.session.query(db.func.to_char(JournalEntries.timestamp, period_interval))
                       .order_by(db.func.to_char(JournalEntries.timestamp, period_interval).desc()).first())
            request.view_args['period'] = most_recent_period

        self._template_args['period_interval'] = period_interval
        self._template_args['period'] = request.view_args['period']
        self._template_args['period_intervals'] = [('YYYY', 'Annual'), ('YYYY-Q', 'Quarterly'), ('YYYY-MM', 'Monthly'), ('YYYY-MM-DD', 'Daily')]
        self._template_args['periods'] = (self.session.query(db.func.to_char(JournalEntries.timestamp, self._template_args['period_interval']))
                                          .order_by(db.func.to_char(JournalEntries.timestamp, self._template_args['period_interval'])
                                                    .desc()).distinct().limit(30))
        self._template_args['periods'] = [p[0] for p in self._template_args['periods']]
        return super(JournalEntriesView, self).index_view()
admin.add_view(JournalEntriesView(DetailedJournalEntries, db.session, category='Bookkeeping', endpoint='journalentries', name='Journal Entries'))


class TaxonomyModelView(PacioliModelView):
    form_extra_fields = dict(name=StringField('Name'))
    column_searchable_list = ['name']
admin.add_view(TaxonomyModelView(Subaccounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Accounts, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Classifications, db.session, category='Bookkeeping'))
admin.add_view(TaxonomyModelView(Elements, db.session, category='Bookkeeping'))

