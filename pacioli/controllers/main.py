from flask import Blueprint
from flask import url_for, redirect, request, abort
from flask.ext.admin import expose
from flask.ext.admin.contrib import sqla
from flask_security import current_user
from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy.ext.automap import automap_base

from pacioli.controllers.utilities import account_formatter, date_formatter, currency_formatter, id_formatter, type_formatter
from pacioli.extensions import admin
from pacioli.models import db, User, Role, JournalEntries, Subaccounts, Accounts, Classifications, Elements

main = Blueprint('main', __name__)


class MyModelView(sqla.ModelView):
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


def register_ofx(app):
    db.metadata.reflect(bind=db.engine, schema='ofx', views=True, only=app.config['MAIN_DATABASE_MODEL_MAP'].keys())
    db.metadata.tables['ofx.new_transactions'].append_constraint(PrimaryKeyConstraint('id', name='new_transactions_pk'))

    Base = automap_base(metadata=db.metadata)
    Base.prepare()
    for cls in Base.classes:
        if cls.__table__.name in app.config['MAIN_DATABASE_MODEL_MAP']:
            globals()[app.config['MAIN_DATABASE_MODEL_MAP'][cls.__table__.name]] = cls

    setattr(AccountsFrom, '__repr__', lambda self: self.name)

    # setattr(Transactions, '__repr__', lambda self: ''.join([str(self.name), str(self.memo)]))

    class OFXModelView(MyModelView):
        can_create = False
        can_delete = False
        can_export = True

    class AccountsFromModelView(OFXModelView):
        column_default_sort = ('id', False)
        column_list = ['id', 'name', 'subclass']
        column_searchable_list = ['name']
        column_filters = column_list
        column_labels = dict(name='Name', subclass='Account Type', id='ID')
        column_formatters = dict(subclass=account_formatter)

        can_edit = True
        form_columns = ['name']

    class TransactionsModelView(OFXModelView):
        column_default_sort = ('dtposted', True)
        column_list = ['fitid', 'dtposted', 'acctfrom', 'trnamt', 'name', 'memo', 'trntype']
        column_searchable_list = ['name', 'memo']
        column_filters = column_list
        column_labels = dict(fitid='ID', acctfrom='From Account', dtposted='Date Posted', trnamt='Amount',
                             trntype='Type', name='Name', memo='Memo')
        column_formatters = dict(fitid=id_formatter, dtposted=date_formatter, trnamt=currency_formatter,
                                 trntype=type_formatter)
        can_edit = False

    class NewTransactionsView(OFXModelView):
        column_default_sort = ('date', True)
        column_searchable_list = ['description']
        column_filters = ['id', 'date', 'amount', 'description', 'account']
        column_labels = dict(id='ID')
        column_formatters = dict(id=id_formatter, date=date_formatter, amount=currency_formatter)

        can_edit = False

        list_template = 'new_transactions_categorization.html'

        # @expose('/', methods=('GET', 'POST'))
        # def index_view(self):
        #      self._template_args['foo'] = 'bar'
        #      return super(NewTransactionsView, self).index_view()
        #

        # @expose('/post/<transaction_id>/')
        # def post(self, transaction_id):
        #     new_journal_entry = JournalEntries()
        #     transaction = (db.session.query(Transactions)
        #                    .filter(db.func.concat(Transactions.fitid,
        #                                           Transactions.acctfrom_id).label('id') == transaction_id).one())
        #     new_journal_entry.transaction_id = transaction_id
        #     new_journal_entry.transaction_source = 'ofx'
        #     account = (db.session.query(AccountsFrom).filter(AccountsFrom.id == transaction.acctfrom_id).one())
        #     if transaction.trnamt > 0:
        #         new_journal_entry.debit_subaccount = account.name
        #         new_journal_entry.credit_subaccount = 'Discretionary Costs'
        #     elif transaction.trnamt <= 0:
        #         new_journal_entry.debit_subaccount = 'Revenues'
        #         new_journal_entry.credit_subaccount = account.name
        #     new_journal_entry.functional_amount = transaction.trnamt
        #     new_journal_entry.functional_currency = 'USD'
        #     new_journal_entry.timestamp = transaction.dtposted
        #     db.session.add(new_journal_entry)
        #     db.session.commit()
        #     return redirect(url_for('ofx/new_transactions.index'))


    admin.add_view(NewTransactionsView(NewTransactions, db.session,
                                         name='New Transactions', category='OFX', endpoint='ofx/new-transactions'))
    admin.add_view(TransactionsModelView(Transactions, db.session,
                                         name='Transactions', category='OFX', endpoint='ofx/transactions'))
    admin.add_view(AccountsFromModelView(AccountsFrom, db.session,
                                         name='Accounts', category='OFX', endpoint='ofx/accounts'))
    admin.add_view(OFXModelView(BankAccounts, db.session,
                                name='Bank Accounts', category='OFX', endpoint='ofx/bank-accounts'))
    admin.add_view(OFXModelView(CreditCardAccounts, db.session,
                                name='Credit Card Accounts', category='OFX', endpoint='ofx/credit-card-accounts'))


admin.add_view(MyModelView(User, db.session, category='Admin'))
admin.add_view(MyModelView(Role, db.session, category='Admin'))

admin.add_view(MyModelView(JournalEntries, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Subaccounts, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Accounts, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Classifications, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Elements, db.session, category='Bookkeeping'))
