from flask.ext.security import RoleMixin, UserMixin, SQLAlchemyUserDatastore, Security
from flask.ext.sqlalchemy import SQLAlchemy

db = SQLAlchemy()

roles_users = db.Table('roles_users',
                       db.Column('user_id', db.Integer(), db.ForeignKey('admin.user.id')),
                       db.Column('role_id', db.Integer(), db.ForeignKey('admin.role.id')))


class Role(db.Model, RoleMixin):
    __table_args__ = {'schema': 'admin'}

    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))


class User(db.Model, UserMixin):
    __table_args__ = {'schema': 'admin'}

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True)
    password = db.Column(db.String(255))
    active = db.Column(db.Boolean())

    confirmed_at = db.Column(db.DateTime(timezone=True))

    last_login_at = db.Column(db.DateTime(timezone=True))
    current_login_at = db.Column(db.DateTime(timezone=True))
    last_login_ip = db.Column(db.String(255))
    current_login_ip = db.Column(db.String(255))
    login_count = db.Column(db.Integer)
    roles = db.relationship('Role', secondary=roles_users, backref=db.backref('users'))


class JournalEntries(db.Model):
    __table_args__ = {'schema': 'pacioli'}
    __tablename__ = 'journal_entries'

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String)
    transaction_source = db.Column(db.String)

    timestamp = db.Column(db.DateTime(timezone=True))
    debit_subaccount = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'))
    credit_subaccount = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'))
    functional_amount = db.Column(db.Numeric)
    functional_currency = db.Column(db.String)
    source_amount = db.Column(db.Numeric)
    source_currency = db.Column(db.String)


class Subaccounts(db.Model):
    __table_args__ = {'schema': 'pacioli'}
    __tablename__ = 'subaccounts'

    name = db.Column(db.String, primary_key=True)
    parent = db.Column(db.String, db.ForeignKey('pacioli.accounts.name'))

    def __repr__(self):
        return self.name


class Accounts(db.Model):
    __table_args__ = {'schema': 'pacioli'}
    __tablename__ = 'accounts'

    name = db.Column(db.String, primary_key=True)
    cash_source = db.Column(db.String)
    parent = db.Column(db.String, db.ForeignKey('pacioli.classifications.name'))
    subaccounts = db.relationship('Subaccounts',
                                  backref='account',
                                  lazy='select',
                                  cascade="save-update, merge, delete")

    def __repr__(self):
        return self.name


class Classifications(db.Model):
    __table_args__ = {'schema': 'pacioli'}
    __tablename__ = 'classifications'

    name = db.Column(db.String, primary_key=True)
    parent = db.Column(db.String, db.ForeignKey('pacioli.elements.name'))
    accounts = db.relationship('Accounts',
                               backref='classification',
                               lazy='select',
                               cascade="save-update, merge, delete")

    def __repr__(self):
        return self.name


class Elements(db.Model):
    __table_args__ = {'schema': 'pacioli'}
    __tablename__ = 'elements'

    name = db.Column(db.String, primary_key=True)
    classifications = db.relationship('Classifications',
                                      backref='element',
                                      lazy='select',
                                      cascade="save-update, merge, delete")

    def __repr__(self):
        return self.name


user_datastore = SQLAlchemyUserDatastore(db, User, Role)
