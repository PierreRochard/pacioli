from pacioli.models import db
from sqlalchemy.exc import ProgrammingError


def create_trigger_function():
    try:
        db.engine.execute("""
        CREATE FUNCTION pacioli.subaccount_insert(new pacioli.journal_entries) RETURNS VOID AS $$
          DECLARE
          period_intervals VARCHAR[5] := '{"YYYY-MM-DD", "YYYY-WW", "YYYY-MM", "YYYY-Q", "YYYY"}';
          period_interval_name VARCHAR;
          period_name RECORD;
          existing_debit_record RECORD;
          existing_credit_record RECORD;
          debit_debit_balance NUMERIC;
          debit_changes_amount NUMERIC;
          credit_credit_balance NUMERIC;
          credit_changes_amount NUMERIC;
          BEGIN
              <<period_interval_loop>>
              FOREACH period_interval_name IN ARRAY period_intervals
              LOOP
                <<periods_loop>>
                FOR period_name in SELECT to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) AS p from pacioli.journal_entries
                GROUP BY to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) ORDER BY to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) DESC LOOP

                  SELECT sum(functional_amount) INTO debit_debit_balance FROM pacioli.journal_entries WHERE debit_subaccount = new.debit_subaccount
                    AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name.p;
                  IF debit_debit_balance IS NULL THEN
                    debit_debit_balance := 0;
                  END IF;

                  SELECT sum(functional_amount) INTO debit_changes_amount FROM pacioli.journal_entries WHERE debit_subaccount = new.debit_subaccount
                    AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name.p;
                  IF debit_changes_amount IS NULL THEN
                    debit_changes_amount := 0;
                  END IF;

                  SELECT * INTO existing_debit_record FROM pacioli.trial_balances WHERE pacioli.trial_balances.subaccount = new.debit_subaccount
                    AND pacioli.trial_balances.period = period_name.p;
                  IF existing_debit_record IS NULL THEN
                    INSERT INTO pacioli.trial_balances (subaccount, debit_balance, credit_balance, debit_changes, credit_changes, period, period_interval)
                      VALUES (new.debit_subaccount, debit_debit_balance, 0, debit_changes_amount, 0, period_name.p, period_interval_name);

                  ELSE
                    UPDATE pacioli.trial_balances SET debit_balance = debit_debit_balance, debit_changes = debit_changes_amount WHERE id = existing_debit_record.id;
                  END IF;

                  SELECT sum(functional_amount) INTO credit_credit_balance FROM pacioli.journal_entries WHERE credit_subaccount = new.credit_subaccount
                    AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) <= period_name.p;
                  IF credit_credit_balance IS NULL THEN
                    credit_credit_balance := 0;
                  END IF;

                  SELECT sum(functional_amount) INTO credit_changes_amount FROM pacioli.journal_entries WHERE credit_subaccount = new.credit_subaccount
                    AND to_char(timestamp AT TIME ZONE 'UTC', period_interval_name) = period_name.p;
                  IF credit_changes_amount IS NULL THEN
                    credit_changes_amount := 0;
                  END IF;

                  SELECT * INTO existing_credit_record FROM pacioli.trial_balances WHERE pacioli.trial_balances.subaccount = new.credit_subaccount
                    AND pacioli.trial_balances.period = period_name.p;
                  IF existing_credit_record IS NULL THEN
                    INSERT INTO pacioli.trial_balances (subaccount, debit_balance, credit_balance, debit_changes, credit_changes, period, period_interval)
                      VALUES (new.credit_subaccount, 0, credit_credit_balance, 0, credit_changes_amount, period_name.p, period_interval_name);
                  ELSE
                    UPDATE pacioli.trial_balances SET credit_balance = credit_credit_balance, credit_changes = credit_changes_amount WHERE id = existing_credit_record.id;
                  END IF;

                END LOOP periods_loop;
              END LOOP period_interval_loop;
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
    except:
        raise

    try:
        db.engine.execute("""
        CREATE FUNCTION pacioli.subaccount_insert_triggered() RETURNS trigger AS $$
          BEGIN
            PERFORM pacioli.subaccount_insert(new);
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

    try:
        db.engine.execute('DROP FUNCTION pacioli.trigger_all_subaccounts() CASCADE;')
    except ProgrammingError:
        pass

    try:
        db.engine.execute("""
        CREATE FUNCTION pacioli.trigger_all_subaccounts() RETURNS VOID AS $$
          DECLARE
          journal_entry pacioli.journal_entries;
          stack TEXT;
          BEGIN
            <<periods_loop>>
                FOR journal_entry in SELECT * from pacioli.journal_entries ORDER BY pacioli.journal_entries.timestamp LOOP
                  PERFORM pacioli.subaccount_insert(journal_entry);
                END LOOP periods_loop;
            RETURN;
          END;
        $$
        SECURITY DEFINER
        LANGUAGE  plpgsql;
        """)
    except:
        raise


def trigger_all_subaccounts():
    try:
        db.engine.execute('SELECT pacioli.trigger_all_subaccounts();')
    except ProgrammingError:
        raise
