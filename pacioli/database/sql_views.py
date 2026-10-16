from pacioli.extensions import run_sql


def create_all():
    create_journal_audit_trigger()
    create_ofx_views()
    create_trial_balances_trigger_function()
    create_period_close_trigger()
    create_amazon_views()
    create_bookkeeping_views()
    create_mappings_views()


def create_journal_audit_trigger():
    run_sql("""
    CREATE OR REPLACE FUNCTION bookkeeping.record_journal_event()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      INSERT INTO bookkeeping.journal_entry_events
        (journal_entry_id, action, happened_at, actor, reason, before_row, after_row)
      VALUES
        (COALESCE(NEW.id, OLD.id), TG_OP, clock_timestamp(),
         NULLIF(current_setting('pacioli.actor', true), ''),
         NULLIF(current_setting('pacioli.reason', true), ''),
         CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) ELSE NULL END,
         CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN to_jsonb(NEW) ELSE NULL END);
      RETURN NULL;
    END;
    $$;
    DROP TRIGGER IF EXISTS journal_event_trigger ON bookkeeping.journal_entries;
    CREATE TRIGGER journal_event_trigger
    AFTER INSERT OR UPDATE OR DELETE ON bookkeeping.journal_entries
    FOR EACH ROW EXECUTE FUNCTION bookkeeping.record_journal_event();
    CREATE OR REPLACE FUNCTION bookkeeping.reject_journal_event_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'Journal audit events cannot be changed';
    END;
    $$;
    DROP TRIGGER IF EXISTS journal_event_immutable ON bookkeeping.journal_entry_events;
    CREATE TRIGGER journal_event_immutable
    BEFORE UPDATE OR DELETE ON bookkeeping.journal_entry_events
    FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_journal_event_change();
    """)


def create_period_close_trigger():
    run_sql("""
    CREATE OR REPLACE FUNCTION bookkeeping.reject_closed_period_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
      last_closed text;
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
      SELECT max(period) INTO last_closed FROM bookkeeping.closed_periods;
      IF last_closed IS NOT NULL THEN
        IF TG_OP IN ('UPDATE', 'DELETE') AND
           to_char(OLD.timestamp AT TIME ZONE 'UTC', 'YYYY-MM') <= last_closed THEN
          RAISE EXCEPTION 'Cannot change a journal in or before closed month %%', last_closed;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') AND
           to_char(NEW.timestamp AT TIME ZONE 'UTC', 'YYYY-MM') <= last_closed THEN
          RAISE EXCEPTION 'Cannot post a journal in or before closed month %%', last_closed;
        END IF;
      END IF;
      IF TG_OP = 'DELETE' THEN
        RETURN OLD;
      END IF;
      RETURN NEW;
    END;
    $$;
    DROP TRIGGER IF EXISTS journal_closed_period_guard ON bookkeeping.journal_entries;
    CREATE TRIGGER journal_closed_period_guard
    BEFORE INSERT OR UPDATE OR DELETE ON bookkeeping.journal_entries
    FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_closed_period_change();
    CREATE OR REPLACE FUNCTION bookkeeping.reject_closed_record_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'Closed month records cannot be changed';
    END;
    $$;
    DROP TRIGGER IF EXISTS closed_period_immutable ON bookkeeping.closed_periods;
    CREATE TRIGGER closed_period_immutable
    BEFORE UPDATE OR DELETE ON bookkeeping.closed_periods
    FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_closed_record_change();
    """)


def create_trial_balances_trigger_function():
    run_sql("""
    CREATE OR REPLACE FUNCTION
      bookkeeping.update_trial_balance(
          _subaccount VARCHAR,
          period_interval_name VARCHAR,
          period_name VARCHAR
      ) RETURNS VOID AS $$
      DECLARE
          debit_balance_amount NUMERIC;
          debit_changes_amount NUMERIC;
          credit_balance_amount NUMERIC;
          credit_changes_amount NUMERIC;
      BEGIN
      SELECT coalesce(sum(functional_amount), 0) INTO debit_balance_amount
        FROM bookkeeping.journal_entries
        WHERE debit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO debit_changes_amount
        FROM bookkeeping.journal_entries
        WHERE debit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO credit_balance_amount
        FROM bookkeeping.journal_entries
        WHERE credit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO credit_changes_amount
        FROM bookkeeping.journal_entries
        WHERE credit_subaccount = _subaccount
          AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name;

      INSERT INTO bookkeeping.trial_balances
        (subaccount, debit_balance, credit_balance, net_balance,
         debit_changes, credit_changes, net_changes, period, period_interval)
      VALUES
        (_subaccount, debit_balance_amount, credit_balance_amount,
         debit_balance_amount - credit_balance_amount,
         debit_changes_amount, credit_changes_amount,
         debit_changes_amount - credit_changes_amount,
         period_name, period_interval_name)
      ON CONFLICT ON CONSTRAINT trial_balances_unique_constraint
      DO UPDATE SET
        debit_balance = EXCLUDED.debit_balance,
        credit_balance = EXCLUDED.credit_balance,
        net_balance = EXCLUDED.net_balance,
        debit_changes = EXCLUDED.debit_changes,
        credit_changes = EXCLUDED.credit_changes,
        net_changes = EXCLUDED.net_changes;

      RETURN;
      END;
    $$
    SECURITY DEFINER
    SET search_path = pg_catalog, bookkeeping
    LANGUAGE  plpgsql;
    """)

    run_sql("""
        CREATE OR REPLACE FUNCTION bookkeeping.subaccount_insert_triggered()
        RETURNS trigger AS $$
          DECLARE
          period_intervals VARCHAR[5] := '{"YYYY", "YYYY-Q", "YYYY-MM",
                                           "YYYY-WW", "YYYY-MM-DD"}';
          period_interval_name VARCHAR;
          period_name VARCHAR;
          subaccount_name VARCHAR;
          affected_subaccounts VARCHAR[];
          affected_since TIMESTAMP WITH TIME ZONE;
          BEGIN
            -- Serializing recalculation prevents concurrent inserts from
            -- overwriting each other's aggregate with a partial snapshot.
            PERFORM pg_advisory_xact_lock(hashtext('pacioli.trial_balances'));
            IF TG_OP = 'INSERT' THEN
              affected_subaccounts := ARRAY[NEW.debit_subaccount, NEW.credit_subaccount];
              affected_since := NEW.timestamp;
            ELSIF TG_OP = 'DELETE' THEN
              affected_subaccounts := ARRAY[OLD.debit_subaccount, OLD.credit_subaccount];
              affected_since := OLD.timestamp;
            ELSE
              affected_subaccounts := ARRAY[OLD.debit_subaccount, OLD.credit_subaccount,
                                            NEW.debit_subaccount, NEW.credit_subaccount];
              affected_since := least(OLD.timestamp, NEW.timestamp);
            END IF;

            FOREACH period_interval_name IN ARRAY period_intervals
            LOOP
              FOR period_name IN
                SELECT period FROM (
                  SELECT to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) AS period
                    FROM bookkeeping.journal_entries
                    WHERE timestamp >= affected_since
                  UNION
                  SELECT period FROM bookkeeping.trial_balances
                    WHERE period_interval = period_interval_name
                ) affected_periods
                WHERE period >= to_char(affected_since AT TIME ZONE 'UTC', period_interval_name)
              LOOP
                IF NOT EXISTS (
                  SELECT 1 FROM bookkeeping.journal_entries
                  WHERE to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name
                ) THEN
                  DELETE FROM bookkeeping.trial_balances
                    WHERE period_interval = period_interval_name
                      AND period = period_name;
                ELSE
                  -- A new global period needs a carried-forward row for every
                  -- account with earlier activity, even if the new entry does
                  -- not mention that account. Existing rows only need updating
                  -- for accounts touched by this journal mutation.
                  FOR subaccount_name IN
                    SELECT DISTINCT account FROM (
                      SELECT unnest(affected_subaccounts) AS account
                      UNION
                      SELECT debit_subaccount AS account
                        FROM bookkeeping.journal_entries entry
                        WHERE to_char(entry.timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name
                          AND NOT EXISTS (
                            SELECT 1 FROM bookkeeping.trial_balances balance
                            WHERE balance.subaccount = entry.debit_subaccount
                              AND balance.period_interval = period_interval_name
                              AND balance.period = period_name)
                      UNION
                      SELECT credit_subaccount AS account
                        FROM bookkeeping.journal_entries entry
                        WHERE to_char(entry.timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name
                          AND NOT EXISTS (
                            SELECT 1 FROM bookkeeping.trial_balances balance
                            WHERE balance.subaccount = entry.credit_subaccount
                              AND balance.period_interval = period_interval_name
                              AND balance.period = period_name)
                    ) accounts_to_update
                  LOOP
                    IF EXISTS (
                      SELECT 1 FROM bookkeeping.journal_entries entry
                      WHERE (entry.debit_subaccount = subaccount_name
                             OR entry.credit_subaccount = subaccount_name)
                        AND to_char(entry.timestamp AT TIME ZONE 'UTC',
                                    period_interval_name) <= period_name
                    ) THEN
                      PERFORM bookkeeping.update_trial_balance(
                        subaccount_name, period_interval_name, period_name);
                    ELSE
                      DELETE FROM bookkeeping.trial_balances
                        WHERE subaccount = subaccount_name
                          AND period_interval = period_interval_name
                          AND period = period_name;
                    END IF;
                  END LOOP;
                END IF;
              END LOOP;
            END LOOP;
            IF TG_OP = 'DELETE' THEN
              RETURN OLD;
            END IF;
            RETURN NEW;
          END;
        $$
        SECURITY DEFINER
        SET search_path = pg_catalog, bookkeeping
        LANGUAGE  plpgsql;
        """)

    run_sql("""
        DROP TRIGGER IF EXISTS subaccount_insert_trigger
            ON bookkeeping.journal_entries;
        CREATE TRIGGER subaccount_insert_trigger
            AFTER INSERT OR UPDATE OR DELETE
            ON bookkeeping.journal_entries
            FOR EACH ROW
            EXECUTE PROCEDURE bookkeeping.subaccount_insert_triggered();
        """)


def create_ofx_views():
    run_sql("""
    CREATE OR REPLACE VIEW ofx.transactions
      AS SELECT
        identity.public_id AS id,
        ofx.stmttrn.dtposted AS date,
        ofx.stmttrn.trnamt AS amount,
        concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
        ofx.stmttrn.trntype AS type,
        ofx.acctfrom.name AS account,
        ofx.stmttrn.acctfrom_id AS account_id,
        posted.id AS journal_entry_id,
        posted.debit_subaccount AS debit_subaccount,
        posted.credit_subaccount AS credit_subaccount
      FROM ofx.stmttrn
      JOIN bookkeeping.ofx_source_identities identity
        ON identity.acctfrom_id=ofx.stmttrn.acctfrom_id AND identity.fitid=ofx.stmttrn.fitid
      LEFT OUTER JOIN (
        SELECT association.source_id, min(journal.id) AS id,
               (CASE WHEN count(*) = 1 THEN min(debit_subaccount)
                    ELSE '[split]' END)::varchar AS debit_subaccount,
               (CASE WHEN count(*) = 1 THEN min(credit_subaccount)
                    ELSE '[split]' END)::varchar AS credit_subaccount
        FROM bookkeeping.journal_entries journal
        JOIN bookkeeping.ofx_journal_sources association ON association.journal_entry_id=journal.id
        WHERE transaction_source = 'ofx'
        GROUP BY association.source_id
      ) posted ON posted.source_id=identity.source_id
      JOIN ofx.acctfrom
        ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
      ORDER BY ofx.stmttrn.dtposted DESC;
    """)

    run_sql("""
    CREATE OR REPLACE VIEW ofx.investment_transactions AS SELECT
            ofx.invtran.*,
            ofx.acctfrom.name AS account_name,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf_secinfo.secname
                WHEN 'sellmf' THEN sellmf_secinfo.secname
                WHEN 'reinvest' THEN reinvest_secinfo.secname
            END AS secname,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf_secinfo.ticker
                WHEN 'sellmf' THEN sellmf_secinfo.ticker
                WHEN 'reinvest' THEN reinvest_secinfo.ticker
            END AS ticker,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf.units
                WHEN 'sellmf' THEN sellmf.units
                WHEN 'reinvest' THEN reinvest.units
            END AS units,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf.unitprice
                WHEN 'sellmf' THEN sellmf.unitprice
                WHEN 'reinvest' THEN reinvest.unitprice
            END AS unitprice,
            CASE ofx.invtran.subclass
                WHEN 'buymf' THEN buymf.total*-1
                WHEN 'sellmf' THEN sellmf.total*-1
                WHEN 'reinvest' THEN reinvest.total*-1
            END AS total
        FROM ofx.invtran
        LEFT OUTER JOIN ofx.buymf ON ofx.buymf.id = ofx.invtran.id
                    and ofx.invtran.subclass = 'buymf'
        LEFT OUTER JOIN ofx.sellmf ON ofx.sellmf.id = ofx.invtran.id
                    and ofx.invtran.subclass = 'sellmf'
        LEFT OUTER JOIN ofx.reinvest ON ofx.reinvest.id = ofx.invtran.id
                    and ofx.invtran.subclass = 'reinvest'
        LEFT OUTER JOIN ofx.secinfo buymf_secinfo
          ON buymf_secinfo.id = ofx.buymf.secinfo_id
        LEFT OUTER JOIN ofx.secinfo sellmf_secinfo
          ON sellmf_secinfo.id = ofx.sellmf.secinfo_id
        LEFT OUTER JOIN ofx.secinfo reinvest_secinfo
          ON reinvest_secinfo.id = ofx.reinvest.secinfo_id
        JOIN ofx.acctfrom ON acctfrom.id = ofx.invtran.acctfrom_id
        ORDER BY ofx.invtran.dttrade DESC;
    """)

    run_sql("""
        CREATE OR REPLACE VIEW ofx.cost_bases AS SELECT
            investment_transactions.secname,
            sum(investment_transactions.units) AS total_units,
            sum(investment_transactions.total) AS cost_basis,
            q1.ticker,
            q1.adjusted_close AS "close",
            q1.adjusted_close
                  * sum(investment_transactions.units) AS market_value,
            (q1.adjusted_close
                  * sum(investment_transactions.units)
                  - sum(investment_transactions.total)) AS pnl,
            (q1.adjusted_close
                  * sum(investment_transactions.units)
                  - sum(investment_transactions.total))
              / sum(investment_transactions.total) AS pnl_percent,
            q2.date AS price_date

        FROM ofx.investment_transactions
        JOIN (SELECT ticker, max(date) AS date
                FROM investments.security_prices
                GROUP BY ticker) AS q2
          ON q2.ticker = investment_transactions.ticker
        JOIN investments.security_prices q1
          ON q1.ticker = ofx.investment_transactions.ticker
            AND q2.date = q1.date
        GROUP BY investment_transactions.secname,
                 q1.ticker,
                 q2.date,
                 q1.adjusted_close
        ORDER BY sum(investment_transactions.total);
    """)


def create_amazon_views():
    run_sql("""
    CREATE OR REPLACE VIEW amazon.amazon_transactions
    AS SELECT
      amazon.items.*,
      posted.id AS journal_entry_id
    FROM amazon.items
    LEFT OUTER JOIN (
      SELECT transaction_id, min(id) AS id
      FROM bookkeeping.journal_entries
      WHERE transaction_source = 'amazon'
      GROUP BY transaction_id
    ) posted ON cast(amazon.items.id AS CHARACTER VARYING) = posted.transaction_id
    ORDER BY amazon.items.shipment_date DESC;
    """)



def create_bookkeeping_views():
    run_sql("""
    CREATE OR REPLACE VIEW bookkeeping.detailed_journal_entries
    AS SELECT
        bookkeeping.journal_entries.id AS id,
        bookkeeping.journal_entries.transaction_source AS transaction_source,
        bookkeeping.journal_entries.transaction_id AS transaction_id,
        bookkeeping.journal_entries."timestamp" AS "timestamp",
        bookkeeping.journal_entries.debit_subaccount as debit_subaccount,
        bookkeeping.journal_entries.credit_subaccount as credit_subaccount,
        bookkeeping.journal_entries.functional_amount as functional_amount,
        CASE bookkeeping.journal_entries.transaction_source
          WHEN 'ofx' THEN concat(ofx.stmttrn.name, ofx.stmttrn.memo)
          WHEN 'amazon' THEN amazon.items.title
        END AS description,
        bookkeeping.journal_entries.mapping_id AS mapping_id,
        bookkeeping.journal_entries.mapping_decision AS mapping_decision
    FROM bookkeeping.journal_entries
    LEFT OUTER JOIN bookkeeping.ofx_journal_sources association
      ON association.journal_entry_id=bookkeeping.journal_entries.id
    LEFT OUTER JOIN bookkeeping.ofx_source_identities identity
      ON identity.source_id=association.source_id
    LEFT OUTER JOIN ofx.stmttrn
      ON identity.acctfrom_id=ofx.stmttrn.acctfrom_id AND identity.fitid=ofx.stmttrn.fitid
      AND bookkeeping.journal_entries.transaction_source = 'ofx'
    LEFT OUTER JOIN amazon.items
      ON cast(amazon.items.id AS CHARACTER VARYING)
                  = bookkeeping.journal_entries.transaction_id
      AND bookkeeping.journal_entries.transaction_source = 'amazon'
    LEFT OUTER JOIN ofx.acctfrom
      ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
    ORDER BY bookkeeping.journal_entries."timestamp" DESC;
    """)


def create_mappings_views():
    # Both sides use the same literal matcher. Percent and underscore in merchant
    # descriptions are ordinary text, as in mapping_resolution.description_matches.
    run_sql("""
    CREATE OR REPLACE FUNCTION admin.mapping_description_matches(
        description text, keyword text, match_mode text)
    RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
    DECLARE
        lowered text := lower(description);
        -- Python str.split() recognizes these Unicode whitespace characters,
        -- including NBSP, which libc/POSIX space classes need not recognize.
        whitespace text := chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||
            chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||
            chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||
            chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||
            chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);
        terms text[] := regexp_split_to_array(translate(lower(keyword), whitespace,
                                                    repeat(' ', char_length(whitespace))), ' +');
        term text;
        offset_position integer := 1;
        term_position integer;
        has_term boolean := false;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM unnest(terms) value WHERE value <> '') THEN
            RETURN false;
        END IF;
        IF match_mode = 'exact' THEN RETURN lowered = lower(keyword); END IF;
        IF match_mode <> 'keyword' THEN RETURN false; END IF;
        FOREACH term IN ARRAY terms LOOP
            IF term = '' THEN CONTINUE; END IF;
            has_term := true;
            term_position := strpos(substr(lowered, offset_position), term);
            IF term_position = 0 THEN RETURN false; END IF;
            offset_position := offset_position + term_position - 1 + char_length(term);
        END LOOP;
        RETURN has_term;
    END $$;
    """)
    run_sql("""
    CREATE OR REPLACE VIEW admin.mapping_overlaps
    AS WITH sources AS (
      SELECT DISTINCT concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
             coalesce(ofx.stmttrn.trnamt > 0, false) AS positive,
             ofx.acctfrom.id AS account_id, ofx.acctfrom.name AS account_name
      FROM ofx.stmttrn
      JOIN ofx.acctfrom ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
    ), matches AS (
      -- An exact match implies equal lowercased text, so exact mappings hash-join
      -- on it; the matcher then runs on candidates, not on every source/mapping pair.
      SELECT sources.*, mappings.id, mappings.keyword, mappings.source,
             CASE WHEN sources.positive THEN mappings.positive_debit_subaccount_id
                  ELSE mappings.negative_credit_subaccount_id END AS scope
      FROM sources
      JOIN admin.mappings ON mappings.source = 'ofx' AND mappings.match_mode = 'exact'
        AND lower(mappings.keyword) = lower(sources.description)
        AND admin.mapping_description_matches(sources.description, mappings.keyword,
                                              mappings.match_mode)
      UNION ALL
      SELECT sources.*, mappings.id, mappings.keyword, mappings.source,
             CASE WHEN sources.positive THEN mappings.positive_debit_subaccount_id
                  ELSE mappings.negative_credit_subaccount_id END
      FROM sources
      JOIN admin.mappings ON mappings.source = 'ofx' AND mappings.match_mode <> 'exact'
        AND admin.mapping_description_matches(sources.description, mappings.keyword,
                                              mappings.match_mode)
    ), eligible AS (
      SELECT * FROM matches WHERE scope IS NULL OR scope = account_name
    )
    SELECT DISTINCT
      first.description,
      first.id AS mapping_id_1,
      first.keyword AS mapping_keyword_1,
      second.id AS mapping_id_2,
      second.keyword AS mapping_keyword_2,
      second.source AS source
    FROM eligible first
    JOIN eligible second ON second.description = first.description
      AND second.positive = first.positive AND second.account_id = first.account_id
      AND first.id < second.id
    ORDER BY description;
    """)
