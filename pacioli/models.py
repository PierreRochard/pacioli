from flask.ext.security import RoleMixin, UserMixin, SQLAlchemyUserDatastore
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
    debit_changes = db.Column(db.Numeric, nullable=False, default=0)
    credit_changes = db.Column(db.Numeric, nullable=False, default=0)


class JournalEntries(db.Model):
    __table_args__ = (db.UniqueConstraint('transaction_id', 'transaction_source',
                                          name='journal_entries_unique_constraint'),
                      {'schema': 'pacioli'})
    __tablename__ = 'journal_entries'

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String)
    transaction_source = db.Column(db.String)
    mapping_id = db.Column(db.Integer, db.ForeignKey('admin.mappings.id'))
    mapping = db.relationship('Mappings', backref='journal_entries',)

    timestamp = db.Column(db.DateTime(timezone=True))
    debit_subaccount = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'), nullable=False)
    credit_subaccount = db.Column(db.String, db.ForeignKey('pacioli.subaccounts.name'), nullable=False)
    functional_amount = db.Column(db.Numeric, nullable=False)
    functional_currency = db.Column(db.String, nullable=False)
    source_amount = db.Column(db.Numeric, nullable=False)
    source_currency = db.Column(db.String, nullable=False)


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
