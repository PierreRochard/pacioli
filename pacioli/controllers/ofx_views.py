from flask import redirect, request
from flask.ext.admin import expose
from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy.ext.automap import automap_base
from wtforms import Form, HiddenField
from wtforms.ext.sqlalchemy.fields import QuerySelectField

from pacioli.controllers.main import PacioliModelView, redirect_url
from pacioli.controllers.utilities import (account_formatter, date_formatter, currency_formatter,
                                           id_formatter, type_formatter)
from pacioli.extensions import admin
from pacioli.models import db, Subaccounts, Mappings


def apply_all_mappings():
    pass

def apply_single_mapping(mapping_id):
#     mapping = db.session.query(Mappings).filter(Mappings.id == mapping_id).one()
#     matched_transactions = new_transactions = (db.session.query(db.func.concat(Transactions.fitid, Transactions.acctfrom_id).label('id'),
#                                                  Transactions.dtposted.label('date'), Transactions.trnamt.label('amount'),
#                                                  db.func.concat(Transactions.name, ' ', Transactions.memo).label('description'),
#                                                  AccountsFrom.name.label('account'))
#                                 .outerjoin(JournalEntries, JournalEntries.transaction_id ==
#                                            db.func.concat(Transactions.fitid, Transactions.acctfrom_id))
#                                 .join(AccountsFrom, AccountsFrom.id == Transactions.acctfrom_id)
#                                 .filter(JournalEntries.transaction_id.is_(None))
#                                 .filter(func.lower(Transactions.name).like('%' + mapping['pattern'].lower() + '%'))
#                                 .filter(AccountsFrom.name == mapping['account'])
#                                 .order_by(Transactions.fitid.desc()).all())
#             for transaction in new_transactions:
#                 new_journal_entry = JournalEntries()
#                 new_journal_entry.transaction_id = transaction.id
#                 new_journal_entry.transaction_source = 'ofx'
#                 new_journal_entry.timestamp = transaction.date
#                 if transaction.amount > 0:
#                     new_journal_entry.debit_subaccount = mapping['positive_debit_account']
#                     new_journal_entry.credit_subaccount = mapping['positive_credit_account']
#                 elif transaction.amount < 0:
#                     new_journal_entry.debit_subaccount = mapping['negative_debit_account']
#                     new_journal_entry.credit_subaccount = mapping['negative_credit_account']
#                 else:
#                     raise Exception()
#                 new_journal_entry.functional_amount = transaction.amount
#                 new_journal_entry.functional_currency = 'USD'
#                 new_journal_entry.source_amount = transaction.amount
#                 new_journal_entry.source_currency = 'USD'
#                 db.session.add(new_journal_entry)
#                 db.session.commit()
#                 print(transaction.description)
#                 print(transaction.account)
    pass


def register_ofx(app):
    db.metadata.reflect(bind=db.engine, schema='ofx', views=True, only=app.config['MAIN_DATABASE_MODEL_MAP'].keys())
    db.metadata.tables['ofx.new_transactions'].append_constraint(PrimaryKeyConstraint('id', name='new_transactions_pk'))

    Base = automap_base(metadata=db.metadata)
    Base.prepare()
    for cls in Base.classes:
        if cls.__table__.name in app.config['MAIN_DATABASE_MODEL_MAP']:
            globals()[app.config['MAIN_DATABASE_MODEL_MAP'][cls.__table__.name]] = cls

    setattr(AccountsFrom, '__repr__', lambda self: self.name)

    class OFXModelView(PacioliModelView):
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

        @expose('/', methods=('GET', 'POST'))
        def index_view(self):

            if request.method == 'POST':
                form = request.form.copy().to_dict()
                new_mapping = Mappings()
                new_mapping.plugin = 'ofx'
                new_mapping.keyword = form['keyword']
                new_mapping.positive_credit_subaccount = form['positive_credit_subaccount']
                new_mapping.negative_debit_subaccount = form['negative_debit_subaccount']
                db.session.add(new_mapping)
                db.session.commit()
                mapping_id, = (db.session.query(Mappings.id).filter(Mappings.plugin == 'ofx')
                               .filter(Mappings.keyword == form['keyword']).one())
                apply_single_mapping(mapping_id)
                return redirect(redirect_url())

            def available_subaccounts():
                return Subaccounts.query

            class NewOFXTransactionMapping(Form):
                keyword = HiddenField()
                positive_credit_subaccount = QuerySelectField(query_factory=available_subaccounts, allow_blank=False)
                negative_debit_subaccount = QuerySelectField(query_factory=available_subaccounts, allow_blank=False)
            new_mapping_form = NewOFXTransactionMapping()

            self._template_args['new_mapping_form'] = new_mapping_form
            return super(NewTransactionsView, self).index_view()


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
