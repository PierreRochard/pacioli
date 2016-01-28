from flask import url_for, redirect, request, abort
from flask.ext.admin import BaseView, expose
from flask_security import current_user
from flask import Blueprint
from flask.ext.admin.contrib import sqla

from pacioli.extensions import admin
from pacioli.models import db, User, Role, JournalEntries, Subaccounts, Accounts, Classifications, Elements
from sqlalchemy import MetaData, create_engine
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.ext.declarative import declarative_base

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
                # permission denied
                abort(403)
            else:
                # login
                return redirect(url_for('security.login', next=request.url))

    can_view_details = True
    column_display_pk = True
    column_display_all_relations = True


def name_for_scalar_relationship(base, local_cls, referred_cls, constraint):
    name = referred_cls.__name__.lower()
    local_table = local_cls.__table__
    if name in local_table.columns:
        newname = name + "_"
        return newname
    return name


def name_for_collection_relationship(base, local_cls, referred_cls, constraint):
    name = referred_cls.__name__.lower() + '_collection'
    for c in referred_cls.__table__.columns:
        if c == name:
            name += "_"
    return name


def register_ofx(app):
    ofx_engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
    metadata = MetaData(ofx_engine)
    metadata.reflect(bind=ofx_engine, schema='ofx', only=app.config['MAIN_DATABASE_MODEL_MAP'].keys())
    Model = declarative_base(metadata=metadata, cls=(db.Model,), bind=ofx_engine)
    Base = automap_base(metadata=metadata, declarative_base=Model)
    Base.prepare(name_for_scalar_relationship=name_for_scalar_relationship,
                 name_for_collection_relationship=name_for_collection_relationship)

    for cls in Base.classes:
        if cls.__table__.name in app.config['MAIN_DATABASE_MODEL_MAP']:
            globals()[app.config['MAIN_DATABASE_MODEL_MAP'][cls.__table__.name]] = cls

    class OFXModelView(MyModelView):
        can_create = False
        can_edit = False
        can_delete = False

    class TransactionsModelView(OFXModelView):
        columns = ['fitid', 'dtposted', 'trnamt', 'trntype', 'name', 'memo', 'checknum', 'acctfrom_id', 'acctto_id']

        column_list = columns
        column_searchable_list = ['name', 'memo']
        column_default_sort = ('fitid', True)

    admin.add_view(TransactionsModelView(Transactions, db.session, category='OFX'))
    admin.add_view(OFXModelView(AccountsFrom, db.session, category='OFX'))
    admin.add_view(OFXModelView(AvailableBalance, db.session, category='OFX'))
    admin.add_view(OFXModelView(BankAccounts, db.session, category='OFX'))
    admin.add_view(OFXModelView(CreditCardAccounts, db.session, category='OFX'))
    admin.add_view(OFXModelView(LedgerBalances, db.session, category='OFX'))


admin.add_view(MyModelView(User, db.session, category='Admin'))
admin.add_view(MyModelView(Role, db.session, category='Admin'))


class ReconciliationsView(BaseView):
    def is_accessible(self):
        if not current_user.is_active or not current_user.is_authenticated:
            return False

        if current_user.has_role('superuser'):
            return True

        return False

    def _handle_view(self, name, **kwargs):
        if not self.is_accessible():
            if current_user.is_authenticated:
                # permission denied
                abort(403)
            else:
                # login
                return redirect(url_for('security.login', next=request.url))

    @expose('/')
    def index(self):
        new_transactions = (db.session.query(db.func.concat(Transactions.fitid, Transactions.acctfrom_id).label('id'),
                                             Transactions.dtposted.label('date'), Transactions.trnamt.label('amount'),
                                             db.func.concat(Transactions.name, ' ', Transactions.memo).label('description'),
                                             AccountsFrom.name.label('account'))
                            .outerjoin(JournalEntries, JournalEntries.transaction_id ==
                                       db.func.concat(Transactions.fitid, Transactions.acctfrom_id))
                            .join(AccountsFrom, AccountsFrom.id == Transactions.acctfrom_id)
                            .filter(JournalEntries.transaction_id.is_(None))
                            .order_by(Transactions.fitid.desc()).limit(20))
        return self.render('new_transactions.html', data=new_transactions)

    @expose('/post/<transaction_id>/')
    def post(self, transaction_id):
        new_journal_entry = JournalEntries()
        transaction = (db.session.query(Transactions)
                       .filter(db.func.concat(Transactions.fitid,
                                              Transactions.acctfrom_id).label('id') == transaction_id).one())
        new_journal_entry.transaction_id = transaction_id
        new_journal_entry.transaction_source = 'ofx'
        account = (db.session.query(AccountsFrom).filter(AccountsFrom.id == transaction.acctfrom_id).one())
        if transaction.trnamt > 0:
            new_journal_entry.debit_subaccount = account.name
            new_journal_entry.credit_subaccount = 'Discretionary Costs'
        elif transaction.trnamt <= 0:
            new_journal_entry.debit_subaccount = 'Revenues'
            new_journal_entry.credit_subaccount = account.name
        new_journal_entry.functional_amount = transaction.trnamt
        new_journal_entry.functional_currency = 'USD'
        new_journal_entry.timestamp = transaction.dtposted
        db.session.add(new_journal_entry)
        db.session.commit()
        return redirect(url_for('new_transactions.index'))


admin.add_view(ReconciliationsView(name='New Transactions', endpoint='new_transactions', category='Bookkeeping'))

admin.add_view(MyModelView(JournalEntries, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Subaccounts, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Accounts, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Classifications, db.session, category='Bookkeeping'))
admin.add_view(MyModelView(Elements, db.session, category='Bookkeeping'))
