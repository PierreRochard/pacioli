from flask import url_for, redirect, request, abort
from flask_security import current_user
from flask import Blueprint
from flask.ext.admin.contrib import sqla

from pacioli.extensions import admin
from pacioli.models import db, User, Role
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
        """
        Override builtin _handle_view in order to redirect users when a view is not accessible.
        """
        if not self.is_accessible():
            if current_user.is_authenticated:
                # permission denied
                abort(403)
            else:
                # login
                return redirect(url_for('security.login', next=request.url))


def register_ofx():
    Base = declarative_base()

    class OFXModelView(MyModelView):
        can_create = False
        can_edit = False
        can_delete = False
        can_view_details = True
        column_display_pk = True
        column_display_all_relations = True

    class Transactions(Base):
        __table__ = db.Table('stmttrn', db.metadata, autoload=True, autoload_with=db.engine)

    class Accounts(Base):
        __table__ = db.Table('acctfrom', db.metadata, autoload=True, autoload_with=db.engine)

    class AvailableBalance(Base):
        __table__ = db.Table('availbal', db.metadata, autoload=True, autoload_with=db.engine)

    class BankAccounts(Base):
        __table__ = db.Table('bankacctfrom', db.metadata, autoload=True, autoload_with=db.engine)

    class CreditCardAccounts(Base):
        __table__ = db.Table('ccacctfrom', db.metadata, autoload=True, autoload_with=db.engine)

    class LedgerBalances(Base):
        __table__ = db.Table('ledgerbal', db.metadata, autoload=True, autoload_with=db.engine)

    class TransactionsModelView(OFXModelView):
        columns = ['fitid', 'dtposted', 'trnamt', 'trntype', 'name', 'memo', 'checknum', 'acctfrom_id', 'acctto_id']

        column_list = columns
        column_searchable_list = ['name', 'memo']
        column_default_sort = ('fitid', True)

    admin.add_view(TransactionsModelView(Transactions, db.session, category='OFX'))
    admin.add_view(OFXModelView(Accounts, db.session, category='OFX'))
    admin.add_view(OFXModelView(AvailableBalance, db.session, category='OFX'))
    admin.add_view(OFXModelView(BankAccounts, db.session, category='OFX'))
    admin.add_view(OFXModelView(CreditCardAccounts, db.session, category='OFX'))
    admin.add_view(OFXModelView(LedgerBalances, db.session, category='OFX'))


admin.add_view(MyModelView(User, db.session, category='Admin'))
admin.add_view(MyModelView(Role, db.session, category='Admin'))

