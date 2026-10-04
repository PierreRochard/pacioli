"""Brokerage source evidence, identities and journal links (schema `investments`).

Additive and repeatable. Source rows are immutable; corrections are new rows
(realized lots supersede by reference). Each source gets a public id
'<provider>:v1:<seq>' used as the journal transaction_id. Journals reach
registered brokerage subaccounts only through their provider source, so a bank
leg cannot be booked straight into brokerage cash.
"""
from pacioli.extensions import db


TABLES = '''
CREATE TABLE IF NOT EXISTS investments.brokerage_accounts (
    id serial PRIMARY KEY,
    provider varchar NOT NULL CHECK (provider IN ('schwab')),
    label varchar NOT NULL UNIQUE,
    masked_number varchar(8) NOT NULL,
    currency varchar NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    opened_on date NOT NULL,
    cash_subaccount varchar NOT NULL REFERENCES bookkeeping.subaccounts(name),
    securities_subaccount varchar NOT NULL REFERENCES bookkeeping.subaccounts(name),
    valuation_subaccount varchar NOT NULL REFERENCES bookkeeping.subaccounts(name),
    oci_subaccount varchar NOT NULL REFERENCES bookkeeping.subaccounts(name),
    reviewer varchar NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (cash_subaccount <> securities_subaccount AND cash_subaccount <> valuation_subaccount
           AND cash_subaccount <> oci_subaccount AND securities_subaccount <> valuation_subaccount
           AND securities_subaccount <> oci_subaccount AND valuation_subaccount <> oci_subaccount)
);
CREATE TABLE IF NOT EXISTS investments.brokerage_exports (
    id serial PRIMARY KEY,
    account_id integer NOT NULL REFERENCES investments.brokerage_accounts(id),
    kind varchar NOT NULL CHECK (kind IN ('transactions', 'realized_lots', 'positions')),
    format varchar NOT NULL CHECK (format IN ('json', 'csv', 'xml')),
    evidence_id integer NOT NULL REFERENCES bookkeeping.accounting_evidence(id),
    sha256 char(64) NOT NULL UNIQUE,
    window_start date,
    window_end date,
    provisional_from date,
    as_of timestamptz,
    row_count integer NOT NULL,
    state varchar NOT NULL CHECK (state IN ('active', 'disagreement')),
    reviewer varchar NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    CHECK (window_start IS NULL OR window_start <= window_end)
);
CREATE TABLE IF NOT EXISTS investments.brokerage_sources (
    source_id bigserial PRIMARY KEY,
    public_id text NOT NULL UNIQUE,
    account_id integer NOT NULL REFERENCES investments.brokerage_accounts(id),
    kind varchar NOT NULL CONSTRAINT brokerage_sources_kind_check
        CHECK (kind IN ('activity', 'wash', 'valuation', 'opening')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS investments.brokerage_activity (
    source_id bigint PRIMARY KEY REFERENCES investments.brokerage_sources(source_id),
    account_id integer NOT NULL REFERENCES investments.brokerage_accounts(id),
    posted_on date NOT NULL,
    as_of date,
    action varchar NOT NULL,
    symbol varchar,
    description varchar NOT NULL DEFAULT '',
    quantity numeric,
    price numeric,
    fees numeric,
    amount numeric CHECK (amount IS NULL OR amount = round(amount, 2)),
    economic_digest char(64) NOT NULL,
    day_ordinal integer NOT NULL CHECK (day_ordinal >= 1),
    first_export_id integer NOT NULL REFERENCES investments.brokerage_exports(id),
    UNIQUE (account_id, posted_on, economic_digest, day_ordinal)
);
CREATE TABLE IF NOT EXISTS investments.activity_observations (
    export_id integer NOT NULL REFERENCES investments.brokerage_exports(id),
    source_id bigint NOT NULL REFERENCES investments.brokerage_activity(source_id),
    PRIMARY KEY (export_id, source_id)
);
CREATE TABLE IF NOT EXISTS investments.realized_lots (
    id serial PRIMARY KEY,
    account_id integer NOT NULL REFERENCES investments.brokerage_accounts(id),
    export_id integer NOT NULL REFERENCES investments.brokerage_exports(id),
    symbol varchar NOT NULL,
    quantity numeric NOT NULL CHECK (quantity > 0),
    opened_on date,
    closed_on date NOT NULL,
    proceeds numeric NOT NULL CHECK (proceeds = round(proceeds, 2)),
    cost_basis numeric NOT NULL CHECK (cost_basis = round(cost_basis, 2)),
    disallowed_loss numeric NOT NULL DEFAULT 0 CHECK (disallowed_loss >= 0),
    term varchar NOT NULL CHECK (term IN ('short', 'long')),
    lot_digest char(64) NOT NULL,
    lot_ordinal integer NOT NULL CHECK (lot_ordinal >= 1),
    supersedes_id integer REFERENCES investments.realized_lots(id),
    UNIQUE (account_id, lot_digest, lot_ordinal)
);
CREATE TABLE IF NOT EXISTS investments.wash_adjustments (
    source_id bigint PRIMARY KEY REFERENCES investments.brokerage_sources(source_id),
    lot_id integer NOT NULL REFERENCES investments.realized_lots(id),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    trigger_on date NOT NULL,
    trigger_source_id bigint REFERENCES investments.brokerage_activity(source_id)
);
CREATE TABLE IF NOT EXISTS investments.position_snapshots (
    id serial PRIMARY KEY,
    account_id integer NOT NULL REFERENCES investments.brokerage_accounts(id),
    export_id integer NOT NULL REFERENCES investments.brokerage_exports(id),
    as_of timestamptz NOT NULL,
    market_date date NOT NULL,
    end_of_day boolean NOT NULL,
    total_market_value numeric
);
CREATE TABLE IF NOT EXISTS investments.position_lines (
    id serial PRIMARY KEY,
    snapshot_id integer NOT NULL REFERENCES investments.position_snapshots(id),
    symbol varchar,
    description varchar NOT NULL DEFAULT '',
    security_type varchar,
    quantity numeric,
    price numeric,
    market_value numeric,
    cost_basis numeric,
    is_cash boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS investments.valuation_points (
    source_id bigint PRIMARY KEY REFERENCES investments.brokerage_sources(source_id),
    account_id integer NOT NULL REFERENCES investments.brokerage_accounts(id),
    kind varchar NOT NULL CHECK (kind IN ('positions', 'market_close')),
    valuation_date date NOT NULL,
    valued_at timestamptz NOT NULL,
    market_value numeric NOT NULL CHECK (market_value = round(market_value, 2)),
    cost_basis numeric NOT NULL CHECK (cost_basis = round(cost_basis, 2)),
    target numeric NOT NULL CHECK (target = market_value - cost_basis),
    evidence_id integer NOT NULL REFERENCES bookkeeping.accounting_evidence(id),
    UNIQUE (account_id, valuation_date)
);
CREATE TABLE IF NOT EXISTS investments.brokerage_openings (
    source_id bigint PRIMARY KEY REFERENCES investments.brokerage_sources(source_id),
    account_id integer NOT NULL UNIQUE REFERENCES investments.brokerage_accounts(id),
    as_of date NOT NULL,
    cash numeric NOT NULL CHECK (cash = round(cash, 2)),
    cost_basis numeric NOT NULL CHECK (cost_basis >= 0 AND cost_basis = round(cost_basis, 2)),
    snapshot_id integer NOT NULL REFERENCES investments.position_snapshots(id),
    evidence_id integer NOT NULL REFERENCES bookkeeping.accounting_evidence(id),
    reviewer varchar NOT NULL,
    reason text NOT NULL CHECK (length(trim(reason)) > 0)
);
CREATE TABLE IF NOT EXISTS investments.brokerage_opening_lines (
    source_id bigint NOT NULL REFERENCES investments.brokerage_openings(source_id),
    symbol varchar NOT NULL,
    quantity numeric NOT NULL CHECK (quantity > 0),
    cost_basis numeric NOT NULL CHECK (cost_basis >= 0 AND cost_basis = round(cost_basis, 2)),
    PRIMARY KEY (source_id, symbol)
);
CREATE TABLE IF NOT EXISTS investments.brokerage_journal_sources (
    journal_entry_id integer PRIMARY KEY REFERENCES bookkeeping.journal_entries(id) ON DELETE CASCADE,
    source_id bigint NOT NULL REFERENCES investments.brokerage_sources(source_id)
);
CREATE INDEX IF NOT EXISTS brokerage_journal_sources_source
    ON investments.brokerage_journal_sources(source_id);
CREATE TABLE IF NOT EXISTS investments.share_move_proposals (
    id serial PRIMARY KEY,
    signature char(64) NOT NULL,
    action varchar NOT NULL,
    symbol varchar NOT NULL,
    description varchar NOT NULL,
    direction varchar NOT NULL CHECK (direction IN ('in', 'out')),
    row_count integer NOT NULL CHECK (row_count > 0),
    facts jsonb NOT NULL,
    classification varchar,
    reason text,
    validation_errors jsonb NOT NULL,
    model varchar NOT NULL,
    model_digest char(64) NOT NULL,
    policy_version varchar NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS investments.share_move_rules (
    id serial PRIMARY KEY,
    action varchar NOT NULL,
    symbol varchar NOT NULL,
    description varchar NOT NULL,
    direction varchar NOT NULL CHECK (direction IN ('in', 'out')),
    resolution varchar NOT NULL CHECK (resolution IN ('memo')),
    proposal_id integer NOT NULL REFERENCES investments.share_move_proposals(id),
    reviewer varchar NOT NULL,
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (action, symbol, description, direction)
);
CREATE TABLE IF NOT EXISTS investments.symbol_map (
    provider_symbol varchar PRIMARY KEY,
    market_ticker varchar NOT NULL,
    reviewer varchar NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS investments.price_request_universe (
    ticker varchar PRIMARY KEY,
    added_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS admin.brokerage_credentials (
    provider varchar PRIMARY KEY CHECK (provider IN ('schwab')),
    username text NOT NULL CHECK (username LIKE 'fernet:%%'),
    password text NOT NULL CHECK (password LIKE 'fernet:%%'),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS admin.integrity_migrations (
    version text PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now()
)
'''

MIGRATIONS = '''
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='brokerage_sources_kind_check'
                   AND pg_get_constraintdef(oid) LIKE '%%opening%%') THEN
        ALTER TABLE investments.brokerage_sources DROP CONSTRAINT IF EXISTS brokerage_sources_kind_check;
        ALTER TABLE investments.brokerage_sources ADD CONSTRAINT brokerage_sources_kind_check
        CHECK (kind IN ('activity', 'wash', 'valuation', 'opening'));
    END IF;
END $$;
ALTER TABLE investments.brokerage_accounts
    ADD COLUMN IF NOT EXISTS tax_treatment varchar NOT NULL DEFAULT 'taxable';
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='brokerage_tax_treatment') THEN
        ALTER TABLE investments.brokerage_accounts ADD CONSTRAINT brokerage_tax_treatment
        CHECK (tax_treatment IN ('taxable', 'retirement'));
    END IF;
END $$;
'''

IMMUTABLE = ('brokerage_exports', 'brokerage_sources', 'brokerage_activity',
             'activity_observations', 'realized_lots', 'wash_adjustments',
             'position_snapshots', 'position_lines', 'valuation_points',
             'share_move_proposals', 'share_move_rules', 'brokerage_openings',
             'brokerage_opening_lines')

TRIGGERS = '''
CREATE OR REPLACE FUNCTION investments.reject_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'Brokerage evidence is immutable; import a new export instead';
END $$;
CREATE OR REPLACE FUNCTION investments.lock_brokerage_sources()
RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION investments.assign_public_id()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prefix text;
BEGIN
    SELECT provider INTO prefix FROM investments.brokerage_accounts WHERE id=NEW.account_id;
    NEW.public_id := prefix || ':v1:' || NEW.source_id::text;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS assign_public_id ON investments.brokerage_sources;
CREATE TRIGGER assign_public_id BEFORE INSERT ON investments.brokerage_sources
FOR EACH ROW EXECUTE FUNCTION investments.assign_public_id();
CREATE OR REPLACE FUNCTION investments.freeze_brokerage_account()
RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP='DELETE' OR EXISTS (SELECT 1 FROM investments.brokerage_sources WHERE account_id=OLD.id) THEN
        RAISE EXCEPTION 'A brokerage account with sources cannot be changed';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS freeze_brokerage_account ON investments.brokerage_accounts;
CREATE TRIGGER freeze_brokerage_account BEFORE UPDATE OR DELETE ON investments.brokerage_accounts
FOR EACH ROW EXECUTE FUNCTION investments.freeze_brokerage_account();
CREATE OR REPLACE FUNCTION investments.append_only_universe()
RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'The price request universe is append-only';
END $$;
DROP TRIGGER IF EXISTS append_only_universe ON investments.price_request_universe;
CREATE TRIGGER append_only_universe BEFORE UPDATE OR DELETE ON investments.price_request_universe
FOR EACH ROW EXECUTE FUNCTION investments.append_only_universe();
CREATE OR REPLACE FUNCTION investments.link_brokerage_journal()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE identifier bigint;
BEGIN
    IF NEW.transaction_source='schwab' THEN
        SELECT source_id INTO identifier FROM investments.brokerage_sources
        WHERE public_id=NEW.transaction_id;
        IF identifier IS NULL THEN
            RAISE EXCEPTION 'Brokerage journal references a missing source' USING ERRCODE='23503';
        END IF;
        INSERT INTO investments.brokerage_journal_sources(journal_entry_id, source_id)
        VALUES(NEW.id, identifier) ON CONFLICT (journal_entry_id)
        DO UPDATE SET source_id=excluded.source_id;
    ELSE
        DELETE FROM investments.brokerage_journal_sources WHERE journal_entry_id=NEW.id;
    END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS brokerage_journal_link ON bookkeeping.journal_entries;
CREATE TRIGGER brokerage_journal_link AFTER INSERT OR UPDATE ON bookkeeping.journal_entries
FOR EACH ROW EXECUTE FUNCTION investments.link_brokerage_journal();
CREATE OR REPLACE FUNCTION investments.validate_brokerage_journal_link()
RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM bookkeeping.journal_entries j
        JOIN investments.brokerage_sources s ON s.public_id=j.transaction_id
        WHERE j.id=NEW.journal_entry_id AND j.transaction_source='schwab' AND s.source_id=NEW.source_id) THEN
        RAISE EXCEPTION 'Brokerage association does not match journal reference' USING ERRCODE='23503';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS validate_brokerage_journal_link ON investments.brokerage_journal_sources;
CREATE TRIGGER validate_brokerage_journal_link BEFORE INSERT OR UPDATE ON investments.brokerage_journal_sources
FOR EACH ROW EXECUTE FUNCTION investments.validate_brokerage_journal_link();
CREATE OR REPLACE FUNCTION investments.guard_brokerage_subaccounts()
RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF NEW.transaction_source IS DISTINCT FROM 'schwab' AND EXISTS (
        SELECT 1 FROM investments.brokerage_accounts a
        WHERE NEW.debit_subaccount IN (a.cash_subaccount, a.securities_subaccount, a.valuation_subaccount, a.oci_subaccount)
           OR NEW.credit_subaccount IN (a.cash_subaccount, a.securities_subaccount, a.valuation_subaccount, a.oci_subaccount)) THEN
        RAISE EXCEPTION 'Brokerage subaccounts are posted only from brokerage sources';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS guard_brokerage_subaccounts ON bookkeeping.journal_entries;
CREATE TRIGGER guard_brokerage_subaccounts BEFORE INSERT OR UPDATE ON bookkeeping.journal_entries
FOR EACH ROW EXECUTE FUNCTION investments.guard_brokerage_subaccounts();
'''


def install_brokerage():
    with db.engine.begin() as connection:
        # Raw driver SQL: function bodies contain ':v1:' and '::' casts.
        run = connection.exec_driver_sql
        run("SET LOCAL lock_timeout='10s'")
        run("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))")
        run('CREATE SCHEMA IF NOT EXISTS investments')
        run(TABLES)
        run(TRIGGERS)
        run(MIGRATIONS)
        for table in IMMUTABLE:
            run('DROP TRIGGER IF EXISTS immutable_evidence ON investments.{0};'
                'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON investments.{0} '
                'FOR EACH ROW EXECUTE FUNCTION investments.reject_evidence_mutation();'
                'DROP TRIGGER IF EXISTS source_activity_lock ON investments.{0};'
                'CREATE TRIGGER source_activity_lock BEFORE INSERT ON investments.{0} '
                'FOR EACH ROW EXECUTE FUNCTION investments.lock_brokerage_sources();'.format(table))
        run("INSERT INTO admin.integrity_migrations(version) "
            "VALUES('brokerage-ledger-v1') ON CONFLICT DO NOTHING")
