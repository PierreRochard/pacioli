from __future__ import print_function

import traceback
from datetime import datetime, date
from urllib2 import HTTPError

from dateutil.tz import tzlocal
from flask import redirect, request, url_for, current_app
from flask.ext.admin import expose
from flask.ext.admin.contrib.sqla.ajax import QueryAjaxModelLoader
from flask.ext.admin.model.fields import AjaxSelectField
from ofxtools import OFXClient
from ofxtools.ofxalchemy import OFXParser, DBSession
from ofxtools.Client import CcAcct, BankAcct
from sqlalchemy import PrimaryKeyConstraint, func, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.orm.exc import NoResultFound
from wtforms import Form, HiddenField

from pacioli.controllers import PacioliModelView
from pacioli.controllers.utilities import (account_formatter, date_formatter, currency_formatter,
                                           id_formatter, type_formatter)
from pacioli.extensions import admin
from pacioli.models import db, Subaccounts, Mappings, JournalEntries, Connections, ConnectionResponses


def sync_ofx():
    for connection in db.session.query(Connections).filter(Connections.source == 'ofx').all():
        if connection.type in ['Checking', 'Savings']:
            try:
                start, = (db.session.query(Transactions.date)
                          .join(AccountsFrom, Transactions.account_id == AccountsFrom.id)
                          .filter(BankAccounts.acctid == connection.account_number)
                          .order_by(Transactions.date.desc()).first())
                start = start.date()
                end = date.today()
            except TypeError:
                start = None
                end = None
            account = BankAcct(connection.routing_number, connection.account_number, connection.type)
        elif connection.type in ['Credit Card']:
            try:
                start, = (db.session.query(Transactions.date)
                          .join(AccountsFrom, Transactions.account_id == AccountsFrom.id)
                          .join(CreditCardAccounts, CreditCardAccounts.id == AccountsFrom.id)
                          .filter(CreditCardAccounts.acctid == connection.account_number)
                          .order_by(Transactions.date.desc()).first())
                start = start.date()
                end = date.today()
            except TypeError:
                start = None
                end = None
            account = CcAcct(connection.account_number)
        else:
            return
        ofx_client = OFXClient(connection.url, connection.org, connection.fid)
        if start and end:
            statement_request = ofx_client.statement_request(connection.user, connection.password, connection.clientuid,
                                                             [account], dtstart=start, dtend=end)
        else:
            statement_request = ofx_client.statement_request(connection.user, connection.password, connection.clientuid,
                                                             [account])
        response = ofx_client.download(statement_request)

        new_response = ConnectionResponses()
        new_response.connection_id = connection.id
        new_response.connected_at = datetime.now(tzlocal())
        new_response.response = response.read()
        db.session.add(new_response)
        db.session.commit()

        response.seek(0)
        parser = OFXParser()
        engine = create_engine(current_app.config['SQLALCHEMY_DATABASE_URI'], echo=False)
        DBSession.configure(bind=engine)
        parser.parse(response)
        parser.instantiate()
        DBSession.commit()
        connection.synced_at = datetime.now(tzlocal())
        db.session.commit()

    for account in db.session.query(AccountsFrom).filter(AccountsFrom.name.is_(None)).all():
        account.name = ''
        db.session.commit()


def apply_all_mappings():
    for mapping in db.session.query(Mappings).all():
        matched_transactions = (db.session.query(Transactions.id, Transactions.date, Transactions.amount,
                                                 Transactions.description, Transactions.account)
                                .outerjoin(JournalEntries, JournalEntries.transaction_id == Transactions.id)
                                .filter(JournalEntries.transaction_id.is_(None))
                                .filter(func.lower(Transactions.description).like('%' + mapping.keyword.lower() + '%'))
                                .order_by(Transactions.date.desc()).all())
        for transaction in matched_transactions:
            new_journal_entry = JournalEntries()
            new_journal_entry.transaction_id = transaction.id
            new_journal_entry.transaction_source = 'ofx'
            new_journal_entry.timestamp = transaction.date
            if transaction.amount > 0:
                new_journal_entry.debit_subaccount = transaction.account
                try:
                    db.session.query(Subaccounts).filter(Subaccounts.name == mapping.positive_credit_subaccount_id).one()
                except NoResultFound:
                    new_subaccount = Subaccounts()
                    new_subaccount.name = mapping.positive_credit_subaccount_id
                    new_subaccount.parent = 'Discretionary Costs'
                    db.session.add(new_subaccount)
                    db.session.commit()
                new_journal_entry.credit_subaccount = mapping.positive_credit_subaccount_id
            elif transaction.amount < 0:
                new_journal_entry.credit_subaccount = transaction.account
                try:
                    db.session.query(Subaccounts).filter(Subaccounts.name == mapping.negative_debit_subaccount_id).one()
                except NoResultFound:
                    new_subaccount = Subaccounts()
                    new_subaccount.name = mapping.negative_debit_subaccount_id
                    new_subaccount.parent = 'Discretionary Costs'
                    db.session.add(new_subaccount)
                    db.session.commit()
                new_journal_entry.debit_subaccount = mapping.negative_debit_subaccount_id

            else:
                raise Exception()
            new_journal_entry.functional_amount = abs(transaction.amount)
            new_journal_entry.functional_currency = 'USD'
            new_journal_entry.source_amount = abs(transaction.amount)
            new_journal_entry.source_currency = 'USD'
            db.session.add(new_journal_entry)
            db.session.commit()


def apply_single_mapping(mapping_id):
    mapping = db.session.query(Mappings).filter(Mappings.id == mapping_id).one()
    matched_transactions = (db.session.query(Transactions.id, Transactions.date, Transactions.amount,
                                             Transactions.description, Transactions.account)
                            .outerjoin(JournalEntries, JournalEntries.transaction_id == Transactions.id)
                            .filter(JournalEntries.transaction_id.is_(None))
                            .filter(func.lower(Transactions.description).like('%' + mapping.keyword.lower() + '%'))
                            .order_by(Transactions.date.desc()).all())
    for transaction in matched_transactions:
        new_journal_entry = JournalEntries()
        new_journal_entry.transaction_id = transaction.id
        new_journal_entry.mapping_id = mapping_id
        new_journal_entry.transaction_source = 'ofx'
        new_journal_entry.timestamp = transaction.date
        if transaction.amount > 0:
            new_journal_entry.debit_subaccount = transaction.account
            new_journal_entry.credit_subaccount = mapping.positive_credit_subaccount_id
        elif transaction.amount < 0:
            new_journal_entry.debit_subaccount = mapping.negative_debit_subaccount_id
            new_journal_entry.credit_subaccount = transaction.account
        else:
            raise Exception()
        new_journal_entry.functional_amount = abs(transaction.amount)
        new_journal_entry.functional_currency = 'USD'
        new_journal_entry.source_amount = abs(transaction.amount)
        new_journal_entry.source_currency = 'USD'
        db.session.add(new_journal_entry)
        db.session.commit()


def register_ofx():
    db.metadata.reflect(bind=db.engine, schema='ofx', views=True, only=current_app.config['OFX_MODEL_MAP'].keys())
    db.metadata.tables['ofx.transactions'].append_constraint(PrimaryKeyConstraint('id', name='transactions_pk'))
    db.metadata.tables['ofx.new_transactions'].append_constraint(PrimaryKeyConstraint('id', name='new_transactions_pk'))

    Base = automap_base(metadata=db.metadata)
    Base.prepare()
    for cls in Base.classes:
        if cls.__table__.name in current_app.config['OFX_MODEL_MAP']:
            globals()[current_app.config['OFX_MODEL_MAP'][cls.__table__.name]] = cls

    setattr(AccountsFrom, '__repr__', lambda self: self.name)

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
        column_list = ['id', 'date', 'account', 'amount', 'description', 'type']
        column_searchable_list = ['description']
        column_filters = column_list
        column_labels = dict(id='ID', account='From Account', date='Date Posted')
        column_formatters = dict(id=id_formatter, date=date_formatter, amount=currency_formatter,
                                 type=type_formatter)
        can_edit = False
        list_template = 'transactions.html'

    class NewTransactionsView(OFXModelView):
        column_default_sort = {'field': 'date', 'sort_desc': True, 'absolute_value': False}
        column_list = ['id', 'date', 'account', 'amount', 'description', 'type']
        column_searchable_list = ['description']
        column_filters = column_list
        column_labels = dict(id='ID')
        column_formatters = dict(id=id_formatter, date=date_formatter, amount=currency_formatter,
                                 type=type_formatter)
        can_edit = False
        list_template = 'new_transactions.html'

        ajax_subaccount_loader = QueryAjaxModelLoader('subaccounts', db.session, Subaccounts, fields=['name'],
                                                      page_size=10, placeholder='Expense Subaccount')

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
                apply_single_mapping(mapping_id)
                return redirect(url_for('ofx/new-transactions.index_view'))

            class NewOFXTransactionMapping(Form):
                keyword = HiddenField()
                subaccount = AjaxSelectField(loader=self.ajax_subaccount_loader, allow_blank=False)

            new_mapping_form = NewOFXTransactionMapping()

            self._template_args['new_mapping_form'] = new_mapping_form
            return super(NewTransactionsView, self).index_view()

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
            apply_single_mapping(mapping_id)
            return redirect(url_for('ofx/new-transactions.index_view'))

        @expose('/apply-all-mappings/')
        def apply_all_mappings_view(self):
            apply_all_mappings()
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
