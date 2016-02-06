from datetime import datetime, date

from dateutil.tz import tzlocal
from flask import redirect, request, url_for
from flask.ext.admin import expose
from ofxtools import OFXClient
from ofxtools.ofxalchemy import OFXParser
from ofxtools.Client import CcAcct, BankAcct
from sqlalchemy import PrimaryKeyConstraint, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.automap import automap_base
from wtforms import Form, HiddenField
from wtforms.ext.sqlalchemy.fields import QuerySelectField

from pacioli.controllers import PacioliModelView
from pacioli.controllers.utilities import (account_formatter, date_formatter, currency_formatter,
                                           id_formatter, type_formatter)
from pacioli.extensions import admin
from pacioli.models import db, Subaccounts, Mappings, JournalEntries, Connections


def sync_ofx(connection_id):
    connection = db.session.query(Connections).filter(Connections.id == connection_id).one()
    if connection.type in ['Checking', 'Savings']:
        start, = (db.session.query(Transactions.dtposted).join(AccountsFrom)
                  .join(BankAccounts, BankAccounts.id == AccountsFrom.id)
                  .filter(BankAccounts.acctid == connection.account_number)
                  .order_by(Transactions.dtposted.desc()).first())
        account = BankAcct(connection.routing_number, connection.account_number, connection.type)
    elif connection.type in ['Credit Card']:
        start, = (db.session.query(Transactions.dtposted).join(AccountsFrom)
                  .join(CreditCardAccounts, CreditCardAccounts.id == AccountsFrom.id)
                  .filter(CreditCardAccounts.acctid == connection.account_number)
                  .order_by(Transactions.dtposted.desc()).first())
        account = CcAcct(connection.account_number)
    else:
        return
    start = start.date()
    end = date.today()

    ofx_client = OFXClient(connection.url, connection.org, connection.fid)
    request = ofx_client.statement_request(connection.user, connection.password, connection.clientuid,
                                           [account], dtstart=start, dtend=end)
    response = ofx_client.download(request)
    parser = OFXParser()
    parser.parse(response)
    parser.instantiate()
    connection.synced_at = datetime.now(tzlocal())
    db.session.commit()
    return


def apply_all_mappings():
    pass


def apply_single_mapping(mapping_id):
    mapping = db.session.query(Mappings).filter(Mappings.id == mapping_id).one()
    matched_transactions = (db.session.query(db.func.concat(Transactions.fitid, Transactions.acctfrom_id).label('id'),
                                             Transactions.dtposted.label('date'), Transactions.trnamt.label('amount'),
                                             db.func.concat(Transactions.name, ' ', Transactions.memo).label(
                                                 'description'),
                                             AccountsFrom.name.label('account_name'))
                            .outerjoin(JournalEntries, JournalEntries.transaction_id ==
                                       db.func.concat(Transactions.fitid, Transactions.acctfrom_id))
                            .join(AccountsFrom, AccountsFrom.id == Transactions.acctfrom_id)
                            .filter(JournalEntries.transaction_id.is_(None))
                            .filter(func.lower(Transactions.name).like('%' + mapping.keyword.lower() + '%'))
                            .order_by(Transactions.fitid.desc()).all())
    for transaction in matched_transactions:
        new_journal_entry = JournalEntries()
        new_journal_entry.transaction_id = transaction.id
        new_journal_entry.transaction_source = 'ofx'
        new_journal_entry.timestamp = transaction.date
        if transaction.amount > 0:
            new_journal_entry.debit_subaccount = transaction.account_name
            new_journal_entry.credit_subaccount = mapping.positive_credit_subaccount
        elif transaction.amount < 0:
            new_journal_entry.debit_subaccount = mapping.negative_debit_subaccount
            new_journal_entry.credit_subaccount = transaction.account_name
        else:
            raise Exception()
        new_journal_entry.functional_amount = transaction.amount
        new_journal_entry.functional_currency = 'USD'
        new_journal_entry.source_amount = transaction.amount
        new_journal_entry.source_currency = 'USD'
        db.session.add(new_journal_entry)
        db.session.commit()


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
                new_mapping.source = 'ofx'
                new_mapping.keyword = form['keyword']
                new_mapping.positive_credit_subaccount = form['subaccount']
                new_mapping.negative_debit_subaccount = form['subaccount']
                try:
                    db.session.add(new_mapping)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                mapping_id, = (db.session.query(Mappings.id).filter(Mappings.source == 'ofx')
                               .filter(Mappings.keyword == form['keyword']).one())
                apply_single_mapping(mapping_id)
                return redirect(url_for('ofx/new-transactions.index_view'))

            def available_subaccounts():
                return Subaccounts.query.order_by(Subaccounts.parent).order_by(Subaccounts.name)

            class NewOFXTransactionMapping(Form):
                keyword = HiddenField()
                subaccount = QuerySelectField(query_factory=available_subaccounts, allow_blank=False)

            new_mapping_form = NewOFXTransactionMapping()

            self._template_args['new_mapping_form'] = new_mapping_form
            return super(NewTransactionsView, self).index_view()

        @expose('/<expense_account>/<keyword>')
        def favorite(self, expense_account, keyword):
            new_mapping = Mappings()
            new_mapping.source = 'ofx'
            new_mapping.keyword = keyword
            new_mapping.positive_credit_subaccount = expense_account
            new_mapping.negative_debit_subaccount = expense_account
            try:
                db.session.add(new_mapping)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            mapping_id, = (db.session.query(Mappings.id).filter(Mappings.source == 'ofx')
                           .filter(Mappings.keyword == keyword).one())
            apply_single_mapping(mapping_id)
            return redirect(url_for('ofx/new-transactions.index_view'))


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
