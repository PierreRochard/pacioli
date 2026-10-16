from flask import url_for, redirect, request, abort
from flask_admin import expose
from pacioli.extensions import admin
from pacioli.models import db, JournalEntries, TrialBalances, ClosedPeriods
from pacioli.views import PrivateModelView
from pacioli.functions.accounting_functions import journal_period
from pacioli.functions.accounting_report import (INTERVALS, period_key,
                                                period_bounds, statement)
from datetime import datetime, timezone
from pacioli.views.utilities import currency_formatter, fs_currency_format
from sqlalchemy import text, func


class TrialBalancesView(PrivateModelView):
    list_template = 'trial_balances.html'
    column_list = ('id', 'period', 'period_interval', 'subaccount',
                   'debit_balance', 'credit_balance', 'net_balance',
                   'debit_changes', 'credit_changes', 'net_changes')
    # column_default_sort = {'field': 'period',
    #                        'sort_desc': True,
    #                        'absolute_value': False}
    column_default_sort = ('period', True)
    column_searchable_list = ['subaccount']
    column_filters = column_list
    column_sortable_list = column_list
    column_formatters = dict(debit_balance=currency_formatter,
                             credit_balance=currency_formatter,
                             net_balance=currency_formatter,
                             debit_changes=currency_formatter,
                             credit_changes=currency_formatter,
                             net_changes=currency_formatter)
    can_edit = False
    can_create = False
    can_delete = False
    can_export = True

    @expose('/refresh_subaccounts/', methods=('POST',))
    def refresh_all_subaccounts(self):
        from pacioli.views import require_action_csrf
        require_action_csrf()
        query_text = text('''
            SELECT bookkeeping.update_trial_balance(
                :subaccount,
                :period_interval_name,
                :period_name);
        ''')
        try:
            db.session.execute(text('TRUNCATE bookkeeping.trial_balances RESTART IDENTITY'))
            for period_interval_name in ['YYYY', 'YYYY-Q', 'YYYY-MM',
                                         'YYYY-WW', 'YYYY-MM-DD']:
                periods = [row[0] for row in db.session.execute(
                    text("SELECT DISTINCT to_char(timestamp AT TIME ZONE 'UTC', :format) "
                         'FROM bookkeeping.journal_entries'),
                    {'format': period_interval_name})]
                for period_name in periods:
                    subaccounts = [row[0] for row in db.session.execute(
                        text("""SELECT debit_subaccount AS subaccount
                                FROM bookkeeping.journal_entries
                                WHERE to_char(timestamp AT TIME ZONE 'UTC', :format) <= :period
                                UNION
                                SELECT credit_subaccount AS subaccount
                                FROM bookkeeping.journal_entries
                                WHERE to_char(timestamp AT TIME ZONE 'UTC', :format) <= :period"""),
                        {'format': period_interval_name, 'period': period_name})]
                    for subaccount in subaccounts:
                        db.session.execute(query_text, {
                            'subaccount': subaccount,
                            'period_interval_name': period_interval_name,
                            'period_name': period_name})
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return redirect(url_for('trialbalances.index_view'))


admin.add_view(TrialBalancesView(TrialBalances, db,
                                 category='Accounting'))


def render_personal_statement(view, kind):
    interval = request.view_args.get('period_interval') or 'YYYY-MM'
    if interval not in INTERVALS:
        abort(400)
    period = request.view_args.get('period')
    if not period:
        latest = db.session.query(func.max(JournalEntries.timestamp)).scalar()
        period = period_key(max(datetime.now(timezone.utc), latest) if latest
                            else datetime.now(timezone.utc), interval)
    try:
        period_bounds(interval, period)
    except ValueError:
        abort(400)
    closed = (db.session.get(ClosedPeriods, period)
              if interval == 'YYYY-MM' else None)
    if closed is not None:
        from pacioli.functions.period_close import snapshot_report, closed_period_matches
        report = snapshot_report(closed)
        closed_mismatch = not closed_period_matches(db.session, closed)
    else:
        report = statement(db.session, interval, period)
        closed_mismatch = False
    observed = {period_key(row[0], interval) for row in
                db.session.query(JournalEntries.timestamp).all()}
    observed.add(period)
    if interval == 'YYYY-MM' and observed:
        first = min(observed)
        cursor = max(int(first[:4]) * 12 + int(first[5:]) - 1,
                     int(period[:4]) * 12 + int(period[5:]) - 240)
        last = int(period[:4]) * 12 + int(period[5:]) - 1
        while cursor <= last:
            observed.add('{}-{:02d}'.format(cursor // 12, cursor % 12 + 1))
            cursor += 1
    return view.render('personal_statement.html', kind=kind, report=report,
                       closed_period=closed,
                       closed_mismatch=closed_mismatch,
                       period=period, period_interval=interval,
                       periods=sorted(observed, reverse=True),
                       period_intervals=[('YYYY', 'Annual'), ('YYYY-Q', 'Quarterly'),
                                         ('YYYY-MM', 'Monthly'),
                                         ('YYYY-WW', 'Weekly'),
                                         ('YYYY-MM-DD', 'Daily')],
                       money=fs_currency_format)


class IncomeStatementsView(PrivateModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    can_view_details = False

    @expose('/')
    @expose('/<period_interval>/')
    @expose('/<period_interval>/<period>/')
    def index_view(self, period_interval=None, period=None):
        return render_personal_statement(self, 'income')


admin.add_view(IncomeStatementsView(TrialBalances, db, category='Accounting', name='Income Statements', endpoint='income-statements'))


class BalanceSheetView(PrivateModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    can_view_details = False

    @expose('/')
    @expose('/<period_interval>/')
    @expose('/<period_interval>/<period>/')
    def index_view(self, period_interval=None, period=None):
        return render_personal_statement(self, 'balance')


admin.add_view(BalanceSheetView(TrialBalances, db, category='Accounting',
                                name='Balance Sheet', endpoint='balance-sheet'))

import pacioli.views.reconciliation_views
import pacioli.views.cash_flow_views
