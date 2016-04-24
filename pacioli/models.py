from flask import current_app
from flask.ext.security import RoleMixin, UserMixin, SQLAlchemyUserDatastore
from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy.ext.automap import automap_base

from pacioli.extensions import db

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

    confirmed_at = db.Column(db.DateTime())

    last_login_at = db.Column(db.DateTime())
    current_login_at = db.Column(db.DateTime())
    last_login_ip = db.Column(db.String(255))
    current_login_ip = db.Column(db.String(255))
    login_count = db.Column(db.Integer)
    roles = db.relationship('Role', secondary=roles_users, backref=db.backref('users'))


user_datastore = SQLAlchemyUserDatastore(db, User, Role)


class Connections(db.Model):
    __table_args__ = {'schema': 'admin'}
    __tablename__ = 'connections'

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String)
    type = db.Column(db.String)
    url = db.Column(db.String)
    org = db.Column(db.String)
    fid = db.Column(db.String)
    routing_number = db.Column(db.String)
    account_number = db.Column(db.String)
    user = db.Column(db.String)
    password = db.Column(db.String)
    clientuid = db.Column(db.String)

    created_at = db.Column(db.DateTime(timezone=True))
    synced_at = db.Column(db.DateTime(timezone=True))

    def __repr__(self):
        return '{0} - {1}'.format(self.source, self.type)


class ConnectionResponses(db.Model):
    __table_args__ = {'schema': 'admin'}
    __tablename__ = 'connection_responses'

    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey('admin.connections.id'))
    connection = db.relationship('Connections')

    connected_at = db.Column(db.DateTime(timezone=True))
    response = db.Column(db.String)


class Mappings(db.Model):
    __table_args__ = (db.UniqueConstraint('source', 'keyword', name='mappings_unique_constraint'),
                      {'schema': 'admin'})
    __tablename__ = 'mappings'

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String)
    keyword = db.Column(db.String)

    positive_debit_subaccount_id = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'))
    positive_debit_subaccount = db.relationship('Subaccounts', foreign_keys=[positive_debit_subaccount_id])

    positive_credit_subaccount_id = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'))
    positive_credit_subaccount = db.relationship('Subaccounts', foreign_keys=[positive_credit_subaccount_id])

    negative_debit_subaccount_id = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'))
    negative_debit_subaccount = db.relationship('Subaccounts', foreign_keys=[negative_debit_subaccount_id])

    negative_credit_subaccount_id = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'))
    negative_credit_subaccount = db.relationship('Subaccounts', foreign_keys=[negative_credit_subaccount_id])

    def __repr__(self):
        return '{0} - {1}'.format(self.source, self.keyword)


class TrialBalances(db.Model):
    __table_args__ = (db.UniqueConstraint('subaccount', 'period', 'period_interval',
                                          name='trial_balances_unique_constraint'),
                      {'schema': 'pacioli'})
    __tablename__ = 'trial_balances'

    id = db.Column(db.Integer, primary_key=True)
    subaccount = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'))
    period = db.Column(db.String)
    period_interval = db.Column(db.String)
    debit_balance = db.Column(db.Numeric, nullable=False, default=0)
    credit_balance = db.Column(db.Numeric, nullable=False, default=0)
    net_balance = db.Column(db.Numeric, nullable=False, default=0)
    debit_changes = db.Column(db.Numeric, nullable=False, default=0)
    credit_changes = db.Column(db.Numeric, nullable=False, default=0)
    net_changes = db.Column(db.Numeric, nullable=False, default=0)


class JournalEntries(db.Model):
    __tablename__ = 'journal_entries'

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String)
    transaction_source = db.Column(db.String)
    mapping_id = db.Column(db.Integer, db.ForeignKey('admin.mappings.id'))
    mapping = db.relationship('Mappings', backref='journal_entries')

    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)
    debit_subaccount = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'), nullable=False)
    credit_subaccount = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'), nullable=False)
    functional_amount = db.Column(db.Numeric, nullable=False)
    functional_currency = db.Column(db.String, nullable=False)
    source_amount = db.Column(db.Numeric, nullable=False)
    source_currency = db.Column(db.String, nullable=False)

    __table_args__ = (db.UniqueConstraint('transaction_id', 'transaction_source',
                                          name='journal_entries_unique_constraint'),
                      db.CheckConstraint(functional_amount >= 0, name='check_functional_amount_positive'),
                      db.CheckConstraint(source_amount >= 0, name='check_source_amount_positive'),
                      {'schema': 'pacioli'})


class Subaccounts(db.Model):
    __table_args__ = {'schema': 'pacioli'}
    __tablename__ = 'subaccounts'

    name = db.Column(db.String, primary_key=True)
    parent = db.Column(db.String, db.ForeignKey('pacioli.accounts.name'))

    def __repr__(self):
        return '{0} - {1}'.format(self.parent, self.name)


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


class AmazonCategories(db.Model):
    __table_args__ = {'schema': 'amazon'}
    __tablename__ = 'categories'

    name = db.Column(db.String, primary_key=True)

    items = db.relationship('AmazonItems',
                            backref='category',
                            lazy="select",
                            cascade="save-update, merge, delete")

    def __repr__(self):
        return self.name


class AmazonOrders(db.Model):
    __table_args__ = {'schema': 'amazon'}
    __tablename__ = 'orders'

    id = db.Column(db.String, primary_key=True)
    order_date = db.Column(db.Date)

    items = db.relationship('AmazonItems',
                            backref='order',
                            lazy="select",
                            cascade="save-update, merge, delete")

    def __repr__(self):
        return self.id


class AmazonItems(db.Model):
    __table_args__ = (db.UniqueConstraint('order_id', 'title', name='amazon_items_unique_constraint'),
                      {'schema': 'amazon'})
    __tablename__ = 'items'

    asin_isbn = db.Column(db.String)
    buyer_name = db.Column(db.String)
    carrier_name_and_tracking_number = db.Column(db.String)
    category_id = db.Column(db.String, db.ForeignKey('amazon.categories.name'))
    condition = db.Column(db.String)
    currency = db.Column(db.String)
    id = db.Column(db.Integer, primary_key=True)
    item_subtotal = db.Column(db.Numeric)
    item_subtotal_tax = db.Column(db.Numeric)
    item_total = db.Column(db.Numeric)
    list_price_per_unit = db.Column(db.Numeric)
    order_id = db.Column(db.String, db.ForeignKey('amazon.orders.id'))
    order_status = db.Column(db.String)
    ordering_customer_email = db.Column(db.String)
    payment_instrument_type = db.Column(db.String)
    purchase_price_per_unit = db.Column(db.Numeric)
    quantity = db.Column(db.Integer)
    release_date = db.Column(db.Date)
    seller = db.Column(db.String)
    shipment_date = db.Column(db.Date)
    shipping_address_city = db.Column(db.String)
    shipping_address_name = db.Column(db.String)
    shipping_address_state = db.Column(db.String)
    shipping_address_street_1 = db.Column(db.String)
    shipping_address_street_2 = db.Column(db.String)
    shipping_address_zip = db.Column(db.String)
    title = db.Column(db.String)
    unspsc_code = db.Column(db.Integer)
    website = db.Column(db.String)

    def __repr__(self):
        return self.title


class SecurityPrices(db.Model):
    __table_args__ = (db.UniqueConstraint('ticker', 'date', name='security_prices_unique_constraint'),
                      {'schema': 'investments'})
    __tablename__ = 'security_prices'

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String)
    date = db.Column(db.Date)
    adjusted_close = db.Column(db.Numeric)
    close = db.Column(db.Numeric)
    high = db.Column(db.Numeric)
    low = db.Column(db.Numeric)
    open = db.Column(db.Numeric)
    volume = db.Column(db.Numeric)


class Paystubs(db.Model):
    __table_args__ = (db.UniqueConstraint('employer_name', 'period_beginning', 'period_ending',
                                          name='paystubs_unique_constraint'),
                      {'schema': 'payroll'})
    __tablename__ = 'paystubs'

    id = db.Column(db.Integer, primary_key=True)
    employer_name = db.Column(db.String)
    period_beginning = db.Column(db.Date)
    period_ending = db.Column(db.Date)
    pay_date = db.Column(db.Date)

    items = db.relationship('PaystubItems',
                            backref='paystub',
                            lazy="select",
                            cascade="save-update, merge, delete")

    def __repr__(self):
        return '{0}: {1} to {2}'.format(self.employer_name, self.period_beginning, self.period_ending)


class PaystubItems(db.Model):
    __table_args__ = (db.UniqueConstraint('paystub_id', 'description',
                                          name='earnings_unique_constraint'),
                      {'schema': 'payroll'},)
    __tablename__ = 'paystub_items'

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String)
    rate = db.Column(db.Numeric)
    hours = db.Column(db.Numeric)
    this_period = db.Column(db.Numeric)
    year_to_date = db.Column(db.Numeric)
    statutory = db.Column(db.Boolean)
    paystub_id = db.Column(db.Integer, db.ForeignKey('payroll.paystubs.id'))

    def __repr__(self):
        return self.description


def register_models():
    for schema_name in current_app.config['MODEL_MAP'].keys():
        db.metadata.reflect(bind=db.engine, schema=schema_name, views=True, only=current_app.config['MODEL_MAP'][schema_name].keys())
    db.metadata.tables['admin.mapping_overlaps'].append_constraint(PrimaryKeyConstraint('description', 'mapping_id_1', 'mapping_id_2', name='mapping_overlaps_pk'))
    db.metadata.tables['amazon.amazon_transactions'].append_constraint(PrimaryKeyConstraint('id', name='amazon_transactions_pk'))
    db.metadata.tables['ofx.cost_bases'].append_constraint(PrimaryKeyConstraint('ticker', name='cost_bases_pk'))
    db.metadata.tables['ofx.investment_transactions'].append_constraint(PrimaryKeyConstraint('id', name='investment_transactions_pk'))
    db.metadata.tables['ofx.transactions'].append_constraint(PrimaryKeyConstraint('id', name='transactions_pk'))
    db.metadata.tables['pacioli.detailed_journal_entries'].append_constraint(PrimaryKeyConstraint('id', name='detailed_journal_entries_pk'))

    for schema_name in current_app.config['MODEL_MAP'].keys():
        base = automap_base(metadata=db.metadata)
        base.prepare()
        for table_name in current_app.config['MODEL_MAP'][schema_name]:
            globals()[current_app.config['MODEL_MAP'][schema_name][table_name]] = base.classes[table_name]

    setattr(AccountsFrom, '__repr__', lambda self: self.name)
    setattr(InvestmentAccounts, '__repr__', lambda self: self.acctfrom.name)
    setattr(InvestmentPositions, '__repr__', lambda self: str(self.id))
    setattr(InvestmentTransactions, '__repr__', lambda self: self.subclass)
    setattr(Securities, '__repr__', lambda self: '{0} ({1})'.format(self.secname, self.ticker))

