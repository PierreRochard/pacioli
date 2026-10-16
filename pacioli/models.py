from uuid import uuid4

from flask import current_app
from flask_security import RoleMixin, UserMixin, SQLAlchemyUserDatastore
from sqlalchemy import MetaData, PrimaryKeyConstraint
from sqlalchemy.ext.automap import automap_base

from pacioli.extensions import db
from pacioli.secure_fields import EncryptedText

roles_users = db.Table('roles_users', db.Model.metadata,
                       db.Column('users_id', db.Integer(),
                                 db.ForeignKey('admin.users.id')),
                       db.Column('roles_id', db.Integer(),
                                 db.ForeignKey('admin.roles.id')),
                       schema='admin')


class Roles(db.Model, RoleMixin):
    __table_args__ = {'schema': 'admin'}

    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(), unique=True)
    description = db.Column(db.String())


class Users(db.Model, UserMixin):
    __table_args__ = {'schema': 'admin'}

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(), unique=True)
    password = db.Column(db.String())
    active = db.Column(db.Boolean())
    confirmed_at = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    current_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String())
    current_login_ip = db.Column(db.String())
    login_count = db.Column(db.Integer)
    # Flask-Security keys sessions and tokens on this value; rotating it signs out
    # every session for the user.
    fs_uniquifier = db.Column(db.String(64), unique=True, nullable=False,
                              default=lambda: uuid4().hex)
    roles = db.relationship('Roles',
                            secondary=roles_users,
                            backref=db.backref('users'))


class AdminSetupLinks(db.Model):
    __tablename__ = 'admin_setup_links'
    __table_args__ = {'schema': 'admin'}

    id = db.Column(db.Integer, primary_key=True)
    token_digest = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))
    revoked_at = db.Column(db.DateTime(timezone=True))


user_datastore = SQLAlchemyUserDatastore(db, Users, Roles)


class Connections(db.Model):
    __table_args__ = {'schema': 'admin'}
    __tablename__ = 'connections'

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String)
    currency = db.Column(db.String)
    type = db.Column(db.String)
    url = db.Column(db.String)
    org = db.Column(db.String)
    fid = db.Column(db.String)
    routing_number = db.Column(db.String)
    account_number = db.Column(db.String)
    user = db.Column(db.String)
    password = db.Column(EncryptedText)
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
    response = db.Column(EncryptedText)
    import_status = db.Column(db.String(16), nullable=False, default='unreviewed',
                              server_default='unreviewed')
    import_error = db.Column(db.String(64))


class ChaseBrowserCredentials(db.Model):
    """Saved Chase login and the accounts approved for on-demand sync."""
    __tablename__ = 'chase_browser_credentials'
    __table_args__ = {'schema': 'admin'}

    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey('admin.connections.id'),
                              nullable=False, unique=True)
    username = db.Column(EncryptedText, nullable=False)
    password = db.Column(EncryptedText, nullable=False)
    selected_account_labels = db.Column(db.JSON)
    selected_ofx_account_ids = db.Column(db.JSON)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=db.func.now())


class BrokerageCredentials(db.Model):
    """Brokerage sign-in, encrypted at rest; the database refuses plaintext."""
    __tablename__ = 'brokerage_credentials'
    __table_args__ = (db.CheckConstraint("provider IN ('schwab')", name='brokerage_credentials_provider'),
                      db.CheckConstraint("username LIKE 'fernet:%' AND password LIKE 'fernet:%'",
                                         name='brokerage_credentials_encrypted'),
                      {'schema': 'admin'})

    provider = db.Column(db.String, primary_key=True)
    username = db.Column(EncryptedText, nullable=False)
    password = db.Column(EncryptedText, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class ChaseLocalPreferences(db.Model):
    """Per-operator account selection for on-demand incremental Chase updates."""
    __tablename__ = 'chase_local_preferences'
    __table_args__ = (
        db.CheckConstraint('overlap_days BETWEEN 1 AND 90', name='chase_overlap_days'),
        {'schema': 'admin'},
    )
    user_id = db.Column(db.Integer, db.ForeignKey('admin.users.id'), primary_key=True)
    account_ids = db.Column(db.JSON, nullable=False)
    overlap_days = db.Column(db.Integer, nullable=False, default=7)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=db.func.now())


class ChaseBrowserRuns(db.Model):
    """Secret-free progress for one supervised Chase QFX export."""
    __tablename__ = 'chase_browser_runs'
    __table_args__ = (
        db.CheckConstraint('singleton_slot = 1', name='chase_browser_singleton_slot'),
        db.Index('chase_browser_one_active_run', 'singleton_slot', unique=True,
                 postgresql_where=db.text("status IN ('queued', 'signing_in', "
                                          "'waiting_for_mfa', 'choosing_accounts', "
                                          "'exporting', 'importing')")),
        {'schema': 'admin'},
    )

    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey('admin.connections.id'),
                              nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey('admin.users.id'),
                                nullable=False)
    singleton_slot = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(32), nullable=False, default='queued')
    mode = db.Column(db.String(16), nullable=False, default='interactive',
                     server_default='interactive')
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    reviewed_usd = db.Column(db.Boolean, nullable=False, default=False)
    account_choices = db.Column(db.JSON)
    selected_accounts = db.Column(db.JSON)
    mfa_prompt = db.Column(db.String(16))
    mfa_code = db.Column(EncryptedText)
    browser_requested = db.Column(db.Boolean, nullable=False, default=False,
                                 server_default=db.text('false'))
    browser_visible = db.Column(db.Boolean, nullable=False, default=False,
                               server_default=db.text('false'))
    imported_count = db.Column(db.Integer, nullable=False, default=0)
    duplicate_count = db.Column(db.Integer, nullable=False, default=0)
    error_code = db.Column(db.String(48))
    diagnostics = db.Column(EncryptedText)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=db.func.now())
    finished_at = db.Column(db.DateTime(timezone=True))


class ChaseLocalEvents(db.Model):
    """Durable, ordered activity for the local connector; details encrypted at rest."""
    __tablename__ = 'chase_local_events'
    __table_args__ = (db.Index('chase_local_events_run_cursor', 'run_id', 'id'),
                      {'schema': 'admin'})
    id = db.Column(db.BigInteger, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('admin.chase_browser_runs.id',
                                                ondelete='CASCADE'), nullable=False)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False,
                            server_default=db.func.now())
    kind = db.Column(db.String(48), nullable=False)
    details = db.Column(EncryptedText, nullable=False)


class OFXFileImports(db.Model):
    """Provenance for a manually downloaded OFX/QFX statement."""
    __tablename__ = 'ofx_file_imports'
    __table_args__ = (db.UniqueConstraint('sha256', name='ofx_file_imports_sha256_unique'),
                      {'schema': 'admin'})

    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey('admin.connections.id'), nullable=False)
    response_id = db.Column(db.Integer, db.ForeignKey('admin.connection_responses.id'), nullable=False)
    ofx_account_id = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=False,
                            server_default=db.func.now())
    transaction_count = db.Column(db.Integer, nullable=False)
    coverage_start = db.Column(db.DateTime(timezone=True))
    coverage_end = db.Column(db.DateTime(timezone=True))
    first_transaction = db.Column(db.DateTime(timezone=True))
    last_transaction = db.Column(db.DateTime(timezone=True))
    balance_amount = db.Column(db.Numeric, nullable=False)
    balance_asof = db.Column(db.DateTime(timezone=True), nullable=False)


class ChaseExportCoverage(db.Model):
    """Verified browser request bounds, distinct from dates declared in QFX."""
    __tablename__ = 'chase_export_coverage'
    __table_args__ = (
        db.UniqueConstraint('file_import_id', 'start', 'end', name='chase_export_coverage_unique'),
        db.CheckConstraint('"start" <= "end"', name='chase_export_coverage_dates'),
        {'schema': 'admin'},
    )
    id = db.Column(db.Integer, primary_key=True)
    file_import_id = db.Column(db.Integer, db.ForeignKey('admin.ofx_file_imports.id', ondelete='CASCADE'), nullable=False)
    start = db.Column(db.Date, nullable=False)
    end = db.Column(db.Date, nullable=False)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class Mappings(db.Model):
    __table_args__ = (db.UniqueConstraint('source', 'keyword',
                                          name='mappings_unique_constraint'),
                      db.CheckConstraint("match_mode IN ('keyword', 'exact')",
                                         name='mapping_match_mode'),
                      {'schema': 'admin'})
    __tablename__ = 'mappings'

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String)
    keyword = db.Column(db.String)
    match_mode = db.Column(db.String, nullable=False, default='keyword', server_default='keyword')
    review_required = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    review_note = db.Column(db.Text)
    provenance = db.Column(db.JSON)

    positive_debit_subaccount_id = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'))
    positive_debit_subaccount = db.relationship('Subaccounts', foreign_keys=[positive_debit_subaccount_id])

    positive_credit_subaccount_id = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'))
    positive_credit_subaccount = db.relationship('Subaccounts', foreign_keys=[positive_credit_subaccount_id])

    negative_debit_subaccount_id = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'))
    negative_debit_subaccount = db.relationship('Subaccounts', foreign_keys=[negative_debit_subaccount_id])

    negative_credit_subaccount_id = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'))
    negative_credit_subaccount = db.relationship('Subaccounts', foreign_keys=[negative_credit_subaccount_id])

    def __repr__(self):
        return '{0} - {1}'.format(self.source, self.keyword)


class TrialBalances(db.Model):
    __table_args__ = (db.UniqueConstraint('subaccount', 'period', 'period_interval',
                                          name='trial_balances_unique_constraint'),
                      {'schema': 'bookkeeping'})
    __tablename__ = 'trial_balances'

    id = db.Column(db.Integer, primary_key=True)
    subaccount = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'))
    period = db.Column(db.String)
    period_interval = db.Column(db.String)
    debit_balance = db.Column(db.Numeric, nullable=False, default=0)
    credit_balance = db.Column(db.Numeric, nullable=False, default=0)
    net_balance = db.Column(db.Numeric, nullable=False, default=0)
    debit_changes = db.Column(db.Numeric, nullable=False, default=0)
    credit_changes = db.Column(db.Numeric, nullable=False, default=0)
    net_changes = db.Column(db.Numeric, nullable=False, default=0)


class Accruals(db.Model):
    __table_args__ = (db.UniqueConstraint('id', 'name', 'start_date', 'end_date',
                                          name='accruals_unique_constraint'),
                      {'schema': 'bookkeeping'},)
    __tablename__ = 'accruals'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.String)
    accrual_type = db.Column(db.String, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    business_days = db.Column(db.Boolean, nullable=False)
    daily_amount = db.Column(db.Numeric, nullable=False)
    income_statement_subaccount = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'), nullable=False)
    balance_sheet_subaccount = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'), nullable=False)


class JournalEntries(db.Model):
    __tablename__ = 'journal_entries'

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String)
    transaction_source = db.Column(db.String)
    allocation_key = db.Column(db.String, nullable=False, default='0', server_default='0')
    mapping_id = db.Column(db.Integer, db.ForeignKey('admin.mappings.id'))
    mapping = db.relationship('Mappings', backref='journal_entries')
    mapping_decision = db.Column(db.JSON(none_as_null=True))

    timestamp = db.Column(db.DateTime(timezone=True), nullable=False)
    debit_subaccount = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'), nullable=False)
    credit_subaccount = db.Column(db.String, db.ForeignKey('bookkeeping.subaccounts.name'), nullable=False)
    functional_amount = db.Column(db.Numeric, nullable=False)
    functional_currency = db.Column(db.String, nullable=False)
    source_amount = db.Column(db.Numeric, nullable=False)
    source_currency = db.Column(db.String, nullable=False)
    conversion_rate = db.Column(db.Numeric)
    conversion_source = db.Column(db.String)
    conversion_date = db.Column(db.Date)
    memo = db.Column(db.String)

    __table_args__ = (db.UniqueConstraint('transaction_id', 'transaction_source',
                                          'allocation_key', name='journal_entries_source_allocation_unique'),
                      db.CheckConstraint("(transaction_id IS NULL) = (transaction_source IS NULL)",
                                         name='journal_source_pair'),
                      db.CheckConstraint("allocation_key <> ''", name='journal_allocation_key'),
                      db.CheckConstraint("functional_currency = 'USD' AND functional_amount > 0 "
                                         "AND functional_amount::text NOT IN ('NaN', 'Infinity', '-Infinity') "
                                         "AND functional_amount = round(functional_amount, 2)",
                                         name='journal_functional_money'),
                      db.CheckConstraint("source_currency IN ('USD', 'EUR', 'BTC') "
                                         "AND source_amount > 0 AND source_amount::text NOT IN ('NaN', 'Infinity', '-Infinity') "
                                         "AND source_amount = round(source_amount, "
                                         "CASE WHEN source_currency = 'BTC' THEN 8 ELSE 2 END)",
                                         name='journal_source_money'),
                      db.CheckConstraint("((source_currency = 'USD' AND source_amount = functional_amount "
                                         "AND conversion_rate IS NULL AND conversion_source IS NULL "
                                         "AND conversion_date IS NULL) OR "
                                         "(source_currency IN ('EUR', 'BTC') AND conversion_rate IS NOT NULL AND conversion_rate > 0 "
                                         "AND conversion_rate::text NOT IN ('NaN', 'Infinity', '-Infinity') "
                                         "AND NULLIF(btrim(conversion_source), '') IS NOT NULL "
                                         "AND conversion_date IS NOT NULL "
                                         "AND functional_amount = round(source_amount * conversion_rate, 2))) IS TRUE",
                                         name='journal_conversion_evidence'),
                      {'schema': 'bookkeeping'})


class JournalEntryEvents(db.Model):
    """Database-written evidence of every journal insertion or mutation."""
    __tablename__ = 'journal_entry_events'
    __table_args__ = {'schema': 'bookkeeping'}

    id = db.Column(db.BigInteger, primary_key=True)
    journal_entry_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String, nullable=False)
    happened_at = db.Column(db.DateTime(timezone=True), nullable=False)
    actor = db.Column(db.String)
    reason = db.Column(db.String)
    before_row = db.Column(db.JSON)
    after_row = db.Column(db.JSON)


class ClosedPeriods(db.Model):
    """Reviewed monthly statements and a journal fingerprint at close."""
    __tablename__ = 'closed_periods'
    __table_args__ = {'schema': 'bookkeeping'}

    period = db.Column(db.String, primary_key=True)
    closed_at = db.Column(db.DateTime(timezone=True), nullable=False,
                          server_default=db.func.now())
    journal_count = db.Column(db.Integer, nullable=False)
    journal_digest = db.Column(db.String, nullable=False)
    statement_snapshot = db.Column(db.JSON, nullable=False)


class BankFeedAccounts(db.Model):
    """Reviewed currency of an imported OFX account."""
    __tablename__ = 'bank_feed_accounts'
    __table_args__ = (db.CheckConstraint(
        "currency IN ('USD', 'EUR', 'BTC')", name='bank_feed_currency_supported'),
        {'schema': 'bookkeeping'})

    ofx_account_id = db.Column(db.Integer, primary_key=True)
    currency = db.Column(db.String, nullable=False)
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=False,
                             server_default=db.func.now())


class AccountingEvidence(db.Model):
    """Encrypted, content-addressed documents; metadata contains no credentials."""
    __tablename__ = 'accounting_evidence'
    __table_args__ = {'schema': 'bookkeeping'}
    id = db.Column(db.Integer, primary_key=True)
    sha256 = db.Column(db.String(64), unique=True, nullable=False)
    kind = db.Column(db.String, nullable=False)
    payload = db.Column(EncryptedText, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    reviewer = db.Column(db.String, nullable=False)


class BankStatements(db.Model):
    __tablename__ = 'bank_statements'
    __table_args__ = (db.UniqueConstraint('account_id', 'end_date'),
                      db.CheckConstraint('start_date <= end_date'),
                      {'schema': 'bookkeeping'})
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, nullable=False)
    evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    opening = db.Column(db.Numeric, nullable=False)
    closing = db.Column(db.Numeric, nullable=False)
    details = db.Column(EncryptedText, nullable=False)


class OpeningBalances(db.Model):
    __tablename__ = 'opening_balances'
    __table_args__ = {'schema': 'bookkeeping'}
    account_id = db.Column(db.Integer, primary_key=True)
    cutover = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric, nullable=False)
    evidence_digest = db.Column(db.String(64), nullable=False)
    details = db.Column(EncryptedText, nullable=False)
    journal_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.journal_entries.id'))
    reviewer = db.Column(db.String, nullable=False)


class TransferMatches(db.Model):
    __tablename__ = 'transfer_matches'
    __table_args__ = {'schema': 'bookkeeping'}
    id = db.Column(db.Integer, primary_key=True)
    fingerprint = db.Column(db.String(64), nullable=False, unique=True)
    details = db.Column(EncryptedText, nullable=False)
    reviewer = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)


class TransferMatchLegs(db.Model):
    __tablename__ = 'transfer_match_legs'
    __table_args__ = {'schema': 'bookkeeping'}
    journal_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.journal_entries.id'), primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.transfer_matches.id'), nullable=False)


class AmazonEvidenceBundles(db.Model):
    """A shipment/charge or refund, with distinct, stable item identities."""
    __tablename__ = 'evidence_bundles'
    __table_args__ = {'schema': 'amazon'}
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String, unique=True, nullable=False)
    evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False)
    details = db.Column(EncryptedText, nullable=False)
    transaction_id = db.Column(db.String, unique=True)
    posting_digest = db.Column(db.String(64))


class AmazonItemRuns(db.Model):
    """On-demand item proposals. Financial approval is a separate operation."""
    __tablename__ = 'item_runs'
    __table_args__ = (db.CheckConstraint("state IN ('queued','running','completed','needs_attention')"),
                      {'schema': 'amazon'})
    id = db.Column(db.Integer, primary_key=True)
    manifest_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False)
    input_digest = db.Column(db.String(64), unique=True, nullable=False)
    state = db.Column(db.String, nullable=False, default='queued')
    progress = db.Column(db.String, nullable=False, default='Waiting for the local model')
    reviewer = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class AmazonItemJobs(db.Model):
    __tablename__ = 'item_jobs'
    __table_args__ = (db.UniqueConstraint('run_id', 'item_id'),
                     db.CheckConstraint("state IN ('queued','running','suggested','held','failed')"),
                     {'schema': 'amazon'})
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('amazon.item_runs.id'), nullable=False, index=True)
    item_id = db.Column(db.String(64), nullable=False)
    record_key = db.Column(db.String(64), nullable=False)
    state = db.Column(db.String, nullable=False, default='queued')
    proposal = db.Column(EncryptedText)
    decision_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class AmazonItemPostRuns(db.Model):
    """Durable explicit approval, executed atomically outside the web request."""
    __tablename__ = 'item_post_runs'
    __table_args__ = (
        db.CheckConstraint("state IN ('queued','running','completed','failed')"),
        db.Index('amazon_item_one_active_post_run', 'run_id', unique=True,
                 postgresql_where=db.text("state IN ('queued','running')")),
        {'schema': 'amazon'},
    )
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('amazon.item_runs.id'), nullable=False, index=True)
    evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False)
    request_key = db.Column(db.String(64), nullable=False, unique=True)
    state = db.Column(db.String, nullable=False, default='queued')
    progress = db.Column(db.String, nullable=False, default='Approved posting is queued')
    result = db.Column(EncryptedText)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class AccountingReviews(db.Model):
    __tablename__ = 'accounting_reviews'
    __table_args__ = {'schema': 'bookkeeping'}
    id = db.Column(db.Integer, primary_key=True)
    mapping_id = db.Column(db.Integer, db.ForeignKey('admin.mappings.id'), nullable=False)
    snapshot = db.Column(EncryptedText, nullable=False)
    facts = db.Column(EncryptedText, nullable=False)
    proposal = db.Column(EncryptedText)
    decision = db.Column(EncryptedText)
    state = db.Column(db.String, nullable=False, default='ready')
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    reviewer = db.Column(db.String, nullable=False)


class AmazonCollectionRuns(db.Model):
    __tablename__ = 'collection_runs'
    __table_args__ = (db.Index('amazon_one_active_collection', 'state', unique=True,
        postgresql_where=db.text("state = 'running'")), {'schema':'amazon'})
    id = db.Column(db.Integer, primary_key=True)
    state = db.Column(db.String, nullable=False, default='running')
    progress = db.Column(db.String, nullable=False)
    documents = db.Column(db.Integer, nullable=False, default=0)
    checkpoint = db.Column(EncryptedText, nullable=False, default='{}')
    updated_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    reviewer = db.Column(db.String, nullable=False)


class AccountingEvaluationCases(db.Model):
    __tablename__ = 'accounting_evaluation_cases'
    __table_args__ = {'schema': 'bookkeeping'}
    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.String, unique=True, nullable=False)
    merchant = db.Column(db.String, nullable=False)
    partition = db.Column(db.String, nullable=False)
    record = db.Column(EncryptedText, nullable=False)
    expected = db.Column(db.String, nullable=False)
    evidence = db.Column(EncryptedText, nullable=False)
    reviewer = db.Column(db.String, nullable=False)


class Subaccounts(db.Model):
    __table_args__ = {'schema': 'bookkeeping'}
    __tablename__ = 'subaccounts'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True)
    description = db.Column(db.String)
    parent = db.Column(db.String, db.ForeignKey('bookkeeping.accounts.name'))
    tax_tags = db.relationship('TaxTags', backref='subaccounts', secondary='tax.subaccounts_tax_tags')

    def __repr__(self):
        return '{0} - {1}'.format(self.parent, self.name)


class TaxTags(db.Model):
    __table_args__ = {'schema': 'tax'}
    __tablename__ = 'tax_tags'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True)
    description = db.Column(db.String)

    def __repr__(self):
        return self.name


subaccounts_tax_tags = db.Table('subaccounts_tax_tags', db.Model.metadata,
                                db.Column('subaccount_name', db.String(),
                                          db.ForeignKey('bookkeeping.subaccounts.name')),
                                db.Column('tax_tag_id', db.Integer(),
                                          db.ForeignKey('tax.tax_tags.id')),
                                schema='tax')


class Accounts(db.Model):
    __table_args__ = {'schema': 'bookkeeping'}
    __tablename__ = 'accounts'

    name = db.Column(db.String, primary_key=True)
    cash_source = db.Column(db.String)
    is_cash = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    parent = db.Column(db.String, db.ForeignKey('bookkeeping.classifications.name'))
    subaccounts = db.relationship('Subaccounts',
                                  backref='account',
                                  lazy='select',
                                  cascade="save-update, merge, delete")

    def __repr__(self):
        return self.name


class Classifications(db.Model):
    __table_args__ = {'schema': 'bookkeeping'}
    __tablename__ = 'classifications'

    name = db.Column(db.String, primary_key=True)
    parent = db.Column(db.String, db.ForeignKey('bookkeeping.elements.name'))
    accounts = db.relationship('Accounts',
                               backref='classification',
                               lazy='select',
                               cascade="save-update, merge, delete")

    def __repr__(self):
        return self.name


class Elements(db.Model):
    __table_args__ = {'schema': 'bookkeeping'}
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


def register_views():
    """Map the OFX fork's tables and the reporting views named in MODEL_MAP.

    They are reflected into their own metadata. Automapping db.metadata would
    map every declarative model's table a second time, with relationships that
    overlap the real ones.
    """
    metadata = MetaData()
    for schema_name, names in current_app.config['MODEL_MAP'].items():
        metadata.reflect(bind=db.engine, schema=schema_name, views=True, only=list(names))
    manual_constraints = [
        {'view_name': 'admin.mapping_overlaps',
         'columns': ['description', 'mapping_id_1', 'mapping_id_2'],
         'constraint_name': 'mapping_overlaps_pk'},
        {'view_name': 'amazon.amazon_transactions',
         'columns': ['id', ],
         'constraint_name': 'amazon_transactions_pk'},
        {'view_name': 'ofx.cost_bases',
         'columns': ['ticker', ],
         'constraint_name': 'cost_bases_pk'},
        {'view_name': 'ofx.investment_transactions',
         'columns': ['id', ],
         'constraint_name': 'investment_transactions_pk'},
        {'view_name': 'ofx.transactions',
         'columns': ['id', ],
         'constraint_name': 'transactions_pk'},
        {'view_name': 'bookkeeping.detailed_journal_entries',
         'columns': ['id', ],
         'constraint_name': 'detailed_journal_entries_pk'},
    ]
    for c in manual_constraints:
        constraint = PrimaryKeyConstraint(*c['columns'],
                                          name=c['constraint_name'])
        metadata.tables[c['view_name']].append_constraint(constraint)

    base = automap_base(metadata=metadata)
    base.prepare()

    for schema_name in current_app.config['MODEL_MAP'].keys():
        tables_names = current_app.config['MODEL_MAP'][schema_name]
        for table_name in tables_names:
            model_name = tables_names[table_name]
            globals()[model_name] = base.classes[table_name]

    setattr(AccountsFrom, '__repr__', lambda self: self.name)
    setattr(InvestmentAccounts, '__repr__', lambda self: self.acctfrom.name)
    setattr(InvestmentPositions, '__repr__', lambda self: str(self.id))
    setattr(InvestmentTransactions, '__repr__', lambda self: self.subclass)
    setattr(Securities, '__repr__', lambda self: '{0} ({1})'.format(self.secname, self.ticker))


class AmazonSuspensePlans(db.Model):
    __tablename__ = 'suspense_plans'
    __table_args__ = {'schema': 'amazon'}
    id = db.Column(db.Integer, primary_key=True)
    evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False, unique=True)
    document_digest = db.Column(db.String(64), nullable=False, unique=True)
    source_run_id = db.Column(db.Integer, db.ForeignKey('amazon.item_runs.id'), nullable=False)
    review_run_id = db.Column(db.Integer, db.ForeignKey('amazon.item_runs.id'))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class AmazonSuspenseItems(db.Model):
    __tablename__ = 'suspense_items'
    __table_args__ = (db.UniqueConstraint('plan_id', 'item_id'), {'schema': 'amazon'})
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('amazon.suspense_plans.id'), nullable=False, index=True)
    item_id = db.Column(db.String(64), nullable=False)
    source_job_id = db.Column(db.Integer, db.ForeignKey('amazon.item_jobs.id'), nullable=False)
    amount = db.Column(db.Numeric, nullable=False)
    remediation_group = db.Column(db.String, nullable=False)
    proposed_category = db.Column(db.String)


class AmazonSuspenseFacts(db.Model):
    __tablename__ = 'suspense_facts'
    __table_args__ = {'schema': 'amazon'}
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('amazon.suspense_plans.id'), nullable=False, index=True)
    item_id = db.Column(db.String(64), nullable=False, index=True)
    evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class AmazonProductRules(db.Model):
    __tablename__ = 'product_rules'
    __table_args__ = {'schema': 'amazon'}
    id = db.Column(db.Integer, primary_key=True)
    product_digest = db.Column(db.String(64), nullable=False, index=True)
    evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False, unique=True)
    supersedes_id = db.Column(db.Integer, db.ForeignKey('amazon.product_rules.id'), unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class AmazonPolicyAnswers(db.Model):
    """Index of immutable owner preferences; this table is not posting authority."""
    __tablename__ = 'policy_answers'
    __table_args__ = {'schema': 'amazon'}
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('amazon.suspense_plans.id'), nullable=False, index=True)
    question_key = db.Column(db.String(128), nullable=False, index=True)
    preview_evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'),
                                    nullable=False, unique=True)
    evidence_id = db.Column(db.Integer, db.ForeignKey('bookkeeping.accounting_evidence.id'), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
