from pacioli.models import db


def create_all():
    create_ofx_views()
    create_trial_balances_trigger_function()
    create_amazon_views()
    create_bookkeeping_views()
    create_mappings_views()


def create_trial_balances_trigger_function():
    db.engine.execute("""
    CREATE OR REPLACE FUNCTION
      bookkeeping.update_trial_balance(
          _subaccount VARCHAR,
          period_interval_name VARCHAR,
          period_name VARCHAR
      ) RETURNS VOID AS $$
      DECLARE
          existing_debit_record RECORD;
          existing_credit_record RECORD;
          debit_balance_amount NUMERIC;
          debit_changes_amount NUMERIC;
          credit_balance_amount NUMERIC;
          credit_changes_amount NUMERIC;

      BEGIN

      SELECT coalesce(sum(functional_amount), 0) INTO debit_balance_amount
        FROM bookkeeping.journal_entries
        WHERE debit_subaccount = _subaccount
          AND to_char(timestamp, period_interval_name) <= period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO debit_changes_amount
        FROM bookkeeping.journal_entries
        WHERE debit_subaccount = _subaccount
          AND to_char(timestamp, period_interval_name) = period_name;

      SELECT * INTO existing_debit_record
        FROM bookkeeping.trial_balances
        WHERE bookkeeping.trial_balances.subaccount = _subaccount
          AND bookkeeping.trial_balances.period = period_name
          AND bookkeeping.trial_balances.period_interval = period_interval_name;

      IF existing_debit_record IS NULL THEN
        INSERT INTO bookkeeping.trial_balances
          (subaccount, debit_balance, credit_balance,
            net_balance, debit_changes, credit_changes,
            net_changes, period, period_interval)
        VALUES (_subaccount, debit_balance_amount, 0,
            debit_balance_amount, debit_changes_amount, 0,
            debit_changes_amount, period_name, period_interval_name);
      ELSE
        UPDATE bookkeeping.trial_balances
          SET debit_balance = debit_balance_amount,
              net_balance = debit_balance_amount - existing_debit_record.credit_balance,
              debit_changes = debit_changes_amount,
              net_changes = debit_changes_amount - existing_debit_record.credit_changes
          WHERE id = existing_debit_record.id;
      END IF;

      SELECT coalesce(sum(functional_amount), 0) INTO credit_balance_amount
        FROM bookkeeping.journal_entries
        WHERE credit_subaccount = _subaccount
          AND to_char(timestamp, period_interval_name) <= period_name;

      SELECT coalesce(sum(functional_amount), 0) INTO credit_changes_amount
        FROM bookkeeping.journal_entries
        WHERE credit_subaccount = _subaccount
          AND to_char(timestamp, period_interval_name) = period_name;

      SELECT * INTO existing_credit_record
        FROM bookkeeping.trial_balances
        WHERE subaccount = _subaccount
          AND period = period_name
          AND period_interval = period_interval_name;

      IF existing_credit_record IS NULL THEN
        INSERT INTO bookkeeping.trial_balances
            (subaccount, debit_balance, credit_balance,
              net_balance, debit_changes, credit_changes,
              net_changes, period, period_interval)
        VALUES (_subaccount, 0, credit_balance_amount,
                -credit_balance_amount, 0, credit_changes_amount,
                -credit_changes_amount, period_name, period_interval_name);
      ELSE
        UPDATE bookkeeping.trial_balances
          SET credit_balance = credit_balance_amount,
              net_balance = existing_credit_record.debit_balance - credit_balance_amount,
              credit_changes = credit_changes_amount,
              net_changes = existing_credit_record.debit_changes - credit_changes_amount
          WHERE id = existing_credit_record.id;
      END IF;

      RETURN;
      END;
    $$
    SECURITY DEFINER
    LANGUAGE  plpgsql;
    """)

    db.engine.execute("""
        CREATE OR REPLACE FUNCTION bookkeeping.subaccount_insert_triggered()
        RETURNS trigger AS $$
          DECLARE
          period_intervals VARCHAR[5] := '{"YYYY", "YYYY-Q", "YYYY-MM",
                                           "YYYY-WW", "YYYY-MM-DD"}';
          period_interval_name VARCHAR;
          period_name RECORD;
          BEGIN
            <<period_interval_loop>>
            FOREACH period_interval_name IN ARRAY period_intervals
              LOOP
                <<periods_loop>>
                 FOR period_name in SELECT DISTINCT
                          to_char(bookkeeping.journal_entries.timestamp,
                          period_interval_name) AS p
                    FROM bookkeeping.journal_entries
                    WHERE bookkeeping.journal_entries.timestamp >= new.timestamp LOOP
                  PERFORM bookkeeping.update_trial_balance(
                        new.debit_subaccount,
                        period_interval_name,
                        period_name.p);
                  PERFORM bookkeeping.update_trial_balance(
                        new.credit_subaccount,
                        period_interval_name,
                        period_name.p);
                END LOOP periods_loop;
              END LOOP period_interval_loop;
            RETURN new;
          END;
        $$
        SECURITY DEFINER
        LANGUAGE  plpgsql;
        """)

    db.engine.execute("""
        DROP TRIGGER IF EXISTS subaccount_insert_trigger
            ON bookkeeping.journal_entries;
        CREATE TRIGGER subaccount_insert_trigger
            AFTER INSERT OR UPDATE
            ON bookkeeping.journal_entries
            FOR EACH ROW
            EXECUTE PROCEDURE bookkeeping.subaccount_insert_triggered();
        """)


def create_ofx_views():
    db.engine.execute("""
    CREATE OR REPLACE VIEW ofx.transactions
      AS SELECT
        concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id) AS id,
        ofx.stmttrn.dtposted AS date,
        ofx.stmttrn.trnamt AS amount,
        concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
        ofx.stmttrn.trntype AS type,
        ofx.acctfrom.name AS account,
        ofx.stmttrn.acctfrom_id AS account_id,
        bookkeeping.journal_entries.id AS journal_entry_id,
        bookkeeping.journal_entries.debit_subaccount AS debit_subaccount,
        bookkeeping.journal_entries.credit_subaccount AS credit_subaccount
      FROM ofx.stmttrn
      LEFT OUTER JOIN bookkeeping.journal_entries
        ON bookkeeping.journal_entries.transaction_id
              = concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id)
          AND bookkeeping.journal_entries.transaction_source = 'ofx'
      JOIN ofx.acctfrom
        ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
      ORDER BY ofx.stmttrn.dtposted DESC;
    """)

    db.engine.execute("""
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

    db.engine.execute("""
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
    db.engine.execute("""
    CREATE OR REPLACE VIEW amazon.amazon_transactions
    AS SELECT
      amazon.items.*,
      bookkeeping.journal_entries.id AS journal_entry_id
    FROM amazon.items
    LEFT OUTER JOIN bookkeeping.journal_entries
      ON cast(amazon.items.id AS CHARACTER VARYING) = bookkeeping.journal_entries.transaction_id
        AND bookkeeping.journal_entries.transaction_source = 'amazon'
    ORDER BY amazon.items.shipment_date DESC;
    """)



def create_bookkeeping_views():
    db.engine.execute("""
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
        END AS description
    FROM bookkeeping.journal_entries
    LEFT OUTER JOIN ofx.stmttrn
      ON concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id)
                  = bookkeeping.journal_entries.transaction_id
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
    db.engine.execute("""
    CREATE OR REPLACE VIEW admin.mapping_overlaps
    AS SELECT DISTINCT
      concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
      mappings_table_1.id AS mapping_id_1,
      mappings_table_1.keyword AS mapping_keyword_1,
      mappings_table_2.id AS mapping_id_2,
      mappings_table_2.keyword AS mapping_keyword_2,
      mappings_table_2.source AS source
    FROM ofx.stmttrn
    JOIN admin.mappings mappings_table_1
      ON lower(concat(ofx.stmttrn.name, ofx.stmttrn.memo))
        LIKE '%%' || array_to_string(regexp_split_to_array(
                        lower(mappings_table_1.keyword), E'\\\s+'
                        ), '%%') || '%%'
        AND mappings_table_1.source = 'ofx'
    JOIN admin.mappings mappings_table_2
      ON lower(concat(ofx.stmttrn.name, ofx.stmttrn.memo))
        LIKE '%%' || array_to_string(regexp_split_to_array(
                        lower(mappings_table_2.keyword), E'\\\s+'
                        ), '%%')  || '%%'
        AND mappings_table_1.keyword != mappings_table_2.keyword
        AND mappings_table_2.source = 'ofx'
    ORDER BY description;
    """)

    # Todo: have view updates propagate to underlying physical tables
    # try:
    #     db.engine.execute('DROP RULE admin.ofx_mapping_overlaps_keyword_1_update_rule')
    # except ProgrammingError:
    #     pass
    # db.engine.execute("""
    # CREATE RULE ofx_mapping_overlaps_keyword_1_update_rule AS
    # ON UPDATE TO admin.ofx_mapping_overlaps WHERE NEW.mapping_keyword_1 <> OLD.mapping_keyword_1
    #     DO INSTEAD UPDATE admin.mappings SET keyword = NEW.mapping_keyword_1 WHERE id = NEW.mapping_id_1;
    # """)
    #
    # try:
    #     db.engine.execute('DROP RULE admin.ofx_mapping_overlaps_keyword_2_update_rule')
    # except ProgrammingError:
    #     pass
    # db.engine.execute("""
    # CREATE RULE ofx_mapping_overlaps_keyword_2_update_rule AS
    # ON UPDATE TO admin.ofx_mapping_overlaps WHERE NEW.mapping_keyword_2 <> OLD.mapping_keyword_2
    #     DO INSTEAD UPDATE admin.mappings SET keyword = NEW.mapping_keyword_2 WHERE id = NEW.mapping_id_2;
    # """)

