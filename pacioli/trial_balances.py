from pacioli.models import db
from sqlalchemy.exc import ProgrammingError


def create_trigger_function():
    try:
        db.engine.execute('DROP FUNCTION pacioli.update_trial_balance(character varying, character varying, character varying, character varying);')
    except ProgrammingError:
        pass

    try:
        db.engine.execute("""
        CREATE FUNCTION pacioli.update_trial_balance(_debit_subaccount VARCHAR, _credit_subaccount VARCHAR, period_interval_name VARCHAR, period_name VARCHAR) RETURNS VOID AS $$
          DECLARE
          existing_debit_record RECORD;
          existing_credit_record RECORD;
          debit_balance_amount NUMERIC;
          debit_changes_amount NUMERIC;
          credit_balance_amount NUMERIC;
          credit_changes_amount NUMERIC;
          BEGIN
              SELECT sum(functional_amount) INTO debit_balance_amount FROM pacioli.journal_entries WHERE pacioli.journal_entries.debit_subaccount = _debit_subaccount
                                                                                                    AND to_char(timestamp, period_interval_name) <= period_name;
              IF debit_balance_amount IS NULL THEN
                debit_balance_amount := 0;
              END IF;

              SELECT sum(functional_amount) INTO debit_changes_amount FROM pacioli.journal_entries WHERE pacioli.journal_entries.debit_subaccount = _debit_subaccount
                                                                                                     AND to_char(timestamp, period_interval_name) = period_name;
              IF debit_changes_amount IS NULL THEN
                debit_changes_amount := 0;
              END IF;

              SELECT * INTO existing_debit_record FROM pacioli.trial_balances WHERE pacioli.trial_balances.subaccount = _debit_subaccount
                                                                                AND pacioli.trial_balances.period = period_name
                                                                                AND pacioli.trial_balances.period_interval = period_interval_name;
              IF existing_debit_record IS NULL THEN
                INSERT INTO pacioli.trial_balances (subaccount, debit_balance, credit_balance, net_balance, debit_changes, credit_changes, net_changes, period, period_interval)
                                            VALUES (_debit_subaccount, debit_balance_amount, 0, debit_balance_amount, debit_changes_amount, 0, debit_changes_amount, period_name, period_interval_name);
              ELSE
                UPDATE pacioli.trial_balances SET debit_balance = debit_balance_amount, net_balance = debit_balance_amount - existing_debit_record.credit_balance,
                                                  debit_changes = debit_changes_amount, net_changes = debit_changes_amount - existing_debit_record.credit_changes WHERE id = existing_debit_record.id;
              END IF;

              SELECT sum(functional_amount) INTO credit_balance_amount FROM pacioli.journal_entries WHERE pacioli.journal_entries.credit_subaccount = _credit_subaccount
                                                                                                      AND to_char(timestamp, period_interval_name) <= period_name;
              IF credit_balance_amount IS NULL THEN
                credit_balance_amount := 0;
              END IF;

              SELECT sum(functional_amount) INTO credit_changes_amount FROM pacioli.journal_entries WHERE pacioli.journal_entries.credit_subaccount = _credit_subaccount
                                                                                                      AND to_char(timestamp, period_interval_name) = period_name;
              IF credit_changes_amount IS NULL THEN
                credit_changes_amount := 0;
              END IF;

              SELECT * INTO existing_credit_record FROM pacioli.trial_balances WHERE pacioli.trial_balances.subaccount = _credit_subaccount
                                                                                 AND pacioli.trial_balances.period = period_name
                                                                                 AND pacioli.trial_balances.period_interval = period_interval_name;
              IF existing_credit_record IS NULL THEN
                INSERT INTO pacioli.trial_balances (subaccount, debit_balance, credit_balance, net_balance, debit_changes, credit_changes, net_changes, period, period_interval)
                                            VALUES (_credit_subaccount, 0, credit_balance_amount, -credit_balance_amount, 0, credit_changes_amount, -credit_changes_amount, period_name, period_interval_name);
              ELSE
                UPDATE pacioli.trial_balances SET credit_balance = credit_balance_amount, net_balance = existing_credit_record.debit_balance - credit_balance_amount,
                                                  credit_changes = credit_changes_amount, net_changes = existing_credit_record.debit_changes - credit_changes_amount WHERE id = existing_credit_record.id;
              END IF;
            RETURN;
          END;
        $$
        SECURITY DEFINER
        LANGUAGE  plpgsql;
        """)
    except:
        raise

    try:
        db.engine.execute('DROP FUNCTION pacioli.subaccount_insert_triggered() CASCADE;')
    except ProgrammingError:
        pass

    try:
        db.engine.execute("""
        CREATE FUNCTION pacioli.subaccount_insert_triggered() RETURNS trigger AS $$
          DECLARE
          period_intervals VARCHAR[5] := '{"YYYY", "YYYY-Q", "YYYY-MM", "YYYY-WW", "YYYY-MM-DD"}';
          period_interval_name VARCHAR;
          period_name RECORD;
          BEGIN
            <<period_interval_loop>>
            FOREACH period_interval_name IN ARRAY period_intervals
              LOOP
                <<periods_loop>>
                 FOR period_name in SELECT DISTINCT to_char(pacioli.journal_entries.timestamp, period_interval_name) AS p from pacioli.journal_entries WHERE pacioli.journal_entries.timestamp >= new.timestamp LOOP
                  PERFORM pacioli.update_trial_balance(new.debit_subaccount, new.credit_subaccount, period_interval_name, period_name.p);
                END LOOP periods_loop;
              END LOOP period_interval_loop;
            RETURN new;
          END;
        $$
        SECURITY DEFINER
        LANGUAGE  plpgsql;
        """)
    except:
        raise

    try:
        db.engine.execute("""
        CREATE TRIGGER subaccount_insert_trigger AFTER INSERT OR UPDATE ON pacioli.journal_entries
          FOR EACH ROW EXECUTE PROCEDURE pacioli.subaccount_insert_triggered();
        """)
    except:
        raise




