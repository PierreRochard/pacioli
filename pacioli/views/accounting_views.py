from flask import url_for, redirect, request
from flask.ext.admin import expose
from pacioli.extensions import admin
from pacioli.models import (db, JournalEntries, Subaccounts,
                            Accounts, Classifications, Elements, TrialBalances)
from pacioli.views import PrivateModelView
from pacioli.views.utilities import currency_formatter, fs_currency_format, fs_linked_currency_formatter
from sqlalchemy import text, func


class TrialBalancesView(PrivateModelView):
    list_template = 'trial_balances.html'
    column_list = ('id', 'period', 'period_interval', 'subaccount', 'debit_balance',
                   'credit_balance', 'net_balance', 'debit_changes', 'credit_changes', 'net_changes')
    column_default_sort = {'field': 'period', 'sort_desc': True, 'absolute_value': False}
    column_searchable_list = ['subaccount']
    column_filters = column_list
    column_sortable_list = column_list
    column_formatters = dict(debit_balance=currency_formatter, credit_balance=currency_formatter, net_balance=currency_formatter,
                             debit_changes=currency_formatter, credit_changes=currency_formatter, net_changes=currency_formatter)
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
        for debit_subaccount, credit_subaccount in db.session.query(JournalEntries.debit_subaccount,
                                                                    JournalEntries.credit_subaccount).distinct():
            for period_interval_name in ['YYYY', 'YYYY-Q', 'YYYY-MM', 'YYYY-WW', 'YYYY-MM-DD']:
                for period_name in db.session.query(db.func.to_char(JournalEntries.timestamp, period_interval_name)).distinct():
                    transaction = connection.begin()
                    connection.execute(query_text, debit_subaccount=debit_subaccount, credit_subaccount=credit_subaccount,
                                       period_interval_name=period_interval_name, period_name=period_name)
                    transaction.commit()
                    transaction.close()
        connection.close()
        return redirect(url_for('trialbalances.index_view'))


admin.add_view(TrialBalancesView(TrialBalances, db.session, category='Accounting'))


class IncomeStatementsView(PrivateModelView):
    list_template = 'financial_statements.html'
    column_list = ('subaccount', 'net_changes')
    column_default_sort = {'field': 'net_changes', 'sort_desc': True, 'absolute_value': True}
    column_searchable_list = ['subaccount']
    column_filters = column_list
    column_sortable_list = column_list
    column_formatters = dict(net_changes=fs_linked_currency_formatter)

    can_edit = False
    can_create = False
    can_delete = False
    can_view_details = False
    can_export = True
    column_display_actions = False
    page_size = 100

    def get_query(self):
        return (self.session.query(self.model)
                .join(Subaccounts)
                .join(Accounts)
                .join(Classifications)
                .join(Elements)
                .filter(self.model.net_changes != 0)
                .filter(db.or_(Elements.name == 'Revenues', Elements.name == 'Expenses',
                               Elements.name == 'Gains', Elements.name == 'Losses'))
                .filter(self.model.period_interval == request.view_args['period_interval'])
                .filter(self.model.period == request.view_args['period']))

    def get_count_query(self):
        return (self.session.query(func.count('*')).select_from(self.model)
                .join(Subaccounts)
                .join(Accounts)
                .join(Classifications)
                .join(Elements)
                .filter(self.model.net_changes != 0)
                .filter(db.or_(Elements.name == 'Revenues', Elements.name == 'Expenses',
                               Elements.name == 'Gains', Elements.name == 'Losses'))
                .filter(self.model.period_interval == request.view_args['period_interval'])
                .filter(self.model.period == request.view_args['period']))

    @expose('/')
    @expose('/<period_interval>/')
    @expose('/<period_interval>/<period>/')
    def index_view(self, period_interval=None, period=None):
        period_interval = request.view_args.get('period_interval', 'YYYY-MM')
        self._template_args['period_interval'] = period_interval
        request.view_args['period_interval'] = period_interval
        period = request.view_args.get('period', None)
        if not period:
            net_income, period = (self.session.query(func.sum(self.model.net_changes), self.model.period)
                                  .join(Subaccounts)
                                  .join(Accounts)
                                  .join(Classifications)
                                  .join(Elements)
                                  .filter(self.model.net_changes != 0)
                                  .filter(db.or_(Elements.name == 'Revenues', Elements.name == 'Expenses',
                                                 Elements.name == 'Gains', Elements.name == 'Losses'))
                                  .filter(self.model.period_interval == request.view_args['period_interval'])
                                  .group_by(self.model.period)
                                  .order_by(self.model.period.desc())
                                  .first())
            request.view_args['period'] = period
        else:
            net_income, = (self.session.query(func.sum(self.model.net_changes))
                           .join(Subaccounts)
                           .join(Accounts)
                           .join(Classifications)
                           .join(Elements)
                           .filter(self.model.net_changes != 0)
                           .filter(db.or_(Elements.name == 'Revenues', Elements.name == 'Expenses',
                                          Elements.name == 'Gains', Elements.name == 'Losses'))
                           .filter(self.model.period_interval == request.view_args['period_interval'])
                           .filter(self.model.period == period)
                           .first())
        self._template_args['period'] = period
        self._template_args['period_intervals'] = [('YYYY', 'Annual'), ('YYYY-Q', 'Quarterly'), ('YYYY-MM', 'Monthly'), ('YYYY-MM-DD', 'Daily')]
        self._template_args['periods'] = [period[0] for period in (self.session.query(self.model.period)
                                                                   .order_by(self.model.period.desc())
                                                                   .filter(self.model.net_changes != 0)
                                                                   .filter(db.or_(Elements.name == 'Revenues', Elements.name == 'Expenses',
                                                                                  Elements.name == 'Gains', Elements.name == 'Losses'))
                                                                   .filter(self.model.period_interval == request.view_args['period_interval'])
                                                                   .distinct().limit(10))]
        net_income = fs_currency_format(-net_income)
        self._template_args['footer_row'] = {'subaccount': 'Net Income', 'net_changes': net_income}
        return super(IncomeStatementsView, self).index_view()
admin.add_view(IncomeStatementsView(TrialBalances, db.session, category='Accounting', name='Income Statements', endpoint='income-statements'))


class BalanceSheetView(PrivateModelView):
    list_template = 'financial_statements.html'
    column_list = ('subaccount', 'net_balance')
    column_default_sort = {'field': 'net_balance', 'sort_desc': True, 'absolute_value': True}
    column_searchable_list = ['subaccount']
    column_filters = column_list
    column_sortable_list = column_list
    column_formatters = dict(net_balance=fs_linked_currency_formatter)

    can_edit = False
    can_create = False
    can_delete = False
    can_view_details = False
    can_export = True
    column_display_actions = False
    page_size = 100

    def get_query(self):
        return (self.session.query(self.model)
                .join(Subaccounts)
                .join(Accounts)
                .join(Classifications)
                .join(Elements)
                .filter(self.model.net_balance != 0)
                .filter(db.or_(Elements.name == 'Assets', Elements.name == 'Liabilities',
                               Elements.name == 'Capital Contributions', Elements.name == 'Capital Distributions'))
                .filter(self.model.period_interval == request.view_args['period_interval'])
                .filter(self.model.period == request.view_args['period']))

    def get_count_query(self):
        return (self.session.query(func.count('*')).select_from(self.model)
                .join(Subaccounts)
                .join(Accounts)
                .join(Classifications)
                .join(Elements)
                .filter(self.model.net_balance != 0)
                .filter(db.or_(Elements.name == 'Assets', Elements.name == 'Liabilities',
                               Elements.name == 'Capital Contributions', Elements.name == 'Capital Distributions'))
                .filter(self.model.period_interval == request.view_args['period_interval'])
                .filter(self.model.period == request.view_args['period']))

    @expose('/')
    @expose('/<period_interval>/')
    @expose('/<period_interval>/<period>/')
    def index_view(self, period_interval=None, period=None):
        period_interval = request.view_args.get('period_interval', None)
        if not period_interval:
            period_interval = 'YYYY-MM'
        self._template_args['period_interval'] = period_interval
        request.view_args['period_interval'] = period_interval
        self._template_args['period_interval'] = period_interval
        period = request.view_args.get('period', None)
        if not period:
            period, = (self.session.query(db.func.to_char(JournalEntries.timestamp, period_interval))
                       .order_by(db.func.to_char(JournalEntries.timestamp, period_interval).desc()).first())
        self._template_args['period'] = period
        request.view_args['period'] = period
        self._template_args['period_intervals'] = [('YYYY', 'Annual'), ('YYYY-Q', 'Quarterly'),
                                                   ('YYYY-MM', 'Monthly'), ('YYYY-MM-DD', 'Daily')]

        self._template_args['periods'] = (self.session.query(db.func.to_char(JournalEntries.timestamp,
                                                                             self._template_args['period_interval']))
                                          .order_by(db.func.to_char(JournalEntries.timestamp,
                                                                    self._template_args['period_interval']).desc()).distinct().limit(30))
        self._template_args['periods'] = [period[0] for period in self._template_args['periods']]
        net_equity, = (self.session.query(func.sum(self.model.net_balance))
                       .join(Subaccounts)
                       .join(Accounts)
                       .join(Classifications)
                       .join(Elements)
                       .filter(db.or_(Elements.name == 'Assets', Elements.name == 'Liabilities',
                                      Elements.name == 'Capital Contributions', Elements.name == 'Capital Distributions'))
                       .filter(self.model.period_interval == request.view_args['period_interval'])
                       .filter(self.model.period == request.view_args['period']).one())
        net_equity = fs_currency_format(-net_equity)
        self._template_args['footer_row'] = {'subaccount': 'Net Equity', 'net_balance': net_equity}
        return super(BalanceSheetView, self).index_view()


admin.add_view(BalanceSheetView(TrialBalances, db.session, category='Accounting',
                                name='Balance Sheet', endpoint='balance-sheet'))
