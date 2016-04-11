from __future__ import print_function

from flask import redirect, request, url_for
from flask.ext.admin import expose
from flask.ext.admin.contrib.sqla.ajax import QueryAjaxModelLoader
from flask.ext.admin.model.fields import AjaxSelectField
from pacioli.extensions import admin
from pacioli.functions.ofx_functions import apply_all_mappings, apply_single_ofx_mapping
from pacioli.models import (db, Subaccounts, Mappings, Transactions, AccountsFrom,
                            BankAccounts, CreditCardAccounts, InvestmentTransactions, CostBases, InvestmentAccounts,
                            InvestmentBalances, InvestmentPositions, Securities)
from pacioli.views import PacioliModelView
from pacioli.views.utilities import (account_formatter, date_formatter, currency_formatter,
                                     id_formatter, type_formatter, string_formatter, percent_formatter)
from sqlalchemy.exc import IntegrityError
from wtforms import Form, HiddenField

class OFXModelView(PacioliModelView):
    can_create = False
    can_delete = False
    can_export = True


class AccountsFromModelView(OFXModelView):
    column_default_sort = {'field': 'id', 'sort_desc': False, 'absolute_value': False}
    column_list = ['id', 'name', 'subclass']
    column_searchable_list = ['name']
    column_filters = column_list
    column_labels = dict(name='Name', subclass='Account Type', id='ID')
    column_formatters = dict(subclass=account_formatter)

    can_edit = True
    form_columns = ['name']


class TransactionsModelView(OFXModelView):
    column_default_sort = {'field': 'date', 'sort_desc': True, 'absolute_value': False}
    column_list = ['journal_entry_id', 'id', 'date', 'account', 'amount', 'description', 'type']
    column_searchable_list = ['description']
    column_filters = column_list
    column_labels = dict(id='ID', account='From Account', date='Date Posted', journal_entry_id='JE')
    column_formatters = dict(id=id_formatter, date=date_formatter, amount=currency_formatter,
                             type=type_formatter)
    can_edit = False
    list_template = 'transactions.html'

    ajax_subaccount_loader = QueryAjaxModelLoader('subaccounts', db.session, Subaccounts, fields=['name'],
                                                  page_size=10, placeholder='Subaccount')

    form_ajax_refs = {'subaccounts': ajax_subaccount_loader}

    @expose('/', methods=('GET', 'POST'))
    def index_view(self):

        if request.method == 'POST':
            form = request.form.copy().to_dict()
            new_mapping = Mappings()
            new_mapping.source = 'ofx'
            new_mapping.keyword = form['keyword']
            new_mapping.positive_credit_subaccount_id = form['subaccount']
            new_mapping.negative_debit_subaccount_id = form['subaccount']
            try:
                db.session.add(new_mapping)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            mapping_id, = (db.session.query(Mappings.id).filter(Mappings.source == 'ofx')
                           .filter(Mappings.keyword == form['keyword']).one())
            apply_single_ofx_mapping(mapping_id)
            return redirect(url_for('banking/transactions.index_view'))

        class NewOFXTransactionMapping(Form):
            keyword = HiddenField()
            subaccount = AjaxSelectField(loader=self.ajax_subaccount_loader, allow_blank=False)

        new_mapping_form = NewOFXTransactionMapping()

        self._template_args['new_mapping_form'] = new_mapping_form
        return super(TransactionsModelView, self).index_view()

    @expose('/<expense_account>/<keyword>')
    def favorite(self, expense_account, keyword):
        new_mapping = Mappings()
        new_mapping.source = 'ofx'
        new_mapping.keyword = keyword
        new_mapping.positive_credit_subaccount_id = expense_account
        new_mapping.negative_debit_subaccount_id = expense_account
        try:
            db.session.add(new_mapping)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        mapping_id, = (db.session.query(Mappings.id).filter(Mappings.source == 'ofx')
                       .filter(Mappings.keyword == keyword).one())
        apply_single_ofx_mapping(mapping_id)
        return redirect(url_for('banking/transactions.index_view'))

    @expose('/apply-all-mappings/')
    def apply_all_mappings_view(self):
        apply_all_mappings()
        return redirect(url_for('banking/transactions.index_view'))


admin.add_view(TransactionsModelView(Transactions, db.session,
                                     name='Transactions', category='Banking', endpoint='banking/transactions'))
admin.add_view(AccountsFromModelView(AccountsFrom, db.session,
                                     name='Accounts', category='Banking', endpoint='banking/accounts'))
admin.add_view(OFXModelView(BankAccounts, db.session,
                            name='Bank Accounts', category='Banking', endpoint='banking/bank-accounts'))
admin.add_view(OFXModelView(CreditCardAccounts, db.session,
                            name='Credit Card Accounts', category='Banking', endpoint='banking/credit-card-accounts'))


class InvestmentTransactionsView(OFXModelView):
    column_default_sort = {'field': 'dttrade', 'sort_desc': True, 'absolute_value': False}
    column_list = ('account_name', 'fitid', 'subclass', 'memo', 'dttrade', 'ticker', 'secname', 'units', 'unitprice', 'total')
    column_filters = column_list
    column_labels = dict(account_name='Account', fitid='ID', dttrade='Trade', secname='Security Name',
                         unitprice='Price')
    column_formatters = dict(fitid=id_formatter, dttrade=date_formatter, units=currency_formatter,
                             unitprice=currency_formatter, total=currency_formatter)


admin.add_view(InvestmentTransactionsView(InvestmentTransactions, db.session, name='Transactions',
                                          category='Investments', endpoint='investments/transactions'))


class CostBasesView(OFXModelView):
    column_labels = dict(secname='Security Name')
    column_formatters = dict(close=currency_formatter, market_value=currency_formatter, pnl=currency_formatter, pnl_percent=percent_formatter)


admin.add_view(CostBasesView(CostBases, db.session, name='Cost Bases',
                             category='Investments', endpoint='investments/cost-bases'))


class InvestmentAccountsModelView(OFXModelView):
    column_default_sort = {'field': 'acctid', 'sort_desc': True, 'absolute_value': False}
    column_labels = dict(acctfrom='Account', brokerid='Broker ID', acctid='Account ID')


admin.add_view(InvestmentAccountsModelView(InvestmentAccounts, db.session, name='Accounts',
                                           category='Investments', endpoint='investments/accounts'))


class InvestmentBalancesView(OFXModelView):
    column_default_sort = {'field': 'availcash', 'sort_desc': True, 'absolute_value': False}
    column_labels = dict(invacctfrom='Account', dtasof='Date', availcash='Cash', marginbalance='Margin', shortbalance='Short', buypower='Buying Power')
    column_formatters = dict(dtasof=date_formatter, availcash=currency_formatter, marginbalance=currency_formatter,
                             shortbalance=currency_formatter, buypower=currency_formatter)


admin.add_view(InvestmentBalancesView(InvestmentBalances, db.session, name='Balances',
                                      category='Investments', endpoint='investments/balances'))


class InvestmentPositionsModelView(OFXModelView):
    column_default_sort = {'field': 'mktval', 'sort_desc': True, 'absolute_value': True}
    column_list = ['dtasof', 'invacctfrom', 'id', 'secinfo', 'postype', 'units', 'unitprice', 'mktval', 'dtpriceasof']
    column_filters = column_list
    column_labels = dict(id='ID', secinfo='Security Name', invacctfrom='Account', dtasof='Date',
                         postype='Type', unitprice='Price', mktval='Value', dtpriceasof='Price Date')
    column_formatters = dict(dtasof=date_formatter, dtpriceasof=date_formatter,
                             unitprice=currency_formatter, mktval=currency_formatter, postype=string_formatter)


admin.add_view(InvestmentPositionsModelView(InvestmentPositions, db.session, name='Positions',
                                            category='Investments', endpoint='investments/positions'))


class SecuritiesView(OFXModelView):
    column_list = ('id', 'subclass', 'uniqueidtype', 'uniqueid', 'secname', 'ticker')


admin.add_view(SecuritiesView(Securities, db.session, name='Securities', category='Investments',
                              endpoint='investments/securities'))
