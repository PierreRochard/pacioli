"""Shared definitions for reviewed Qwen mappings and provisional bookkeeping."""
from sqlalchemy import func, text

from pacioli.extensions import db
from pacioli.models import Mappings


SUSPENSE_ACCOUNTS = ('Bookkeeping Suspense', 'Transfer Clearing')
ADDITIONAL_ACCOUNTS = {
    'Bookkeeping Suspense': ('Assets', 'Current Assets', 'Bookkeeping Suspense'),
    'Transfer Clearing': ('Assets', 'Current Assets', 'Transfer Clearing'),
    'Software Subscriptions': ('Expenses', 'Discretionary Costs', 'Programming Expense'),
    'Books and Publications': ('Expenses', 'Discretionary Costs', 'Publications Expense'),
    'Household Services': ('Expenses', 'Fixed Costs', 'Household Services Expense'),
    'Home Maintenance': ('Expenses', 'Fixed Costs', 'Home Maintenance Expense'),
    'Vehicle Fuel': ('Expenses', 'Fixed Costs', 'Transportation Expense'),
    'Vehicle Maintenance': ('Expenses', 'Fixed Costs', 'Transportation Expense'),
    'Parking': ('Expenses', 'Fixed Costs', 'Transportation Expense'),
    'Ride Services': ('Expenses', 'Fixed Costs', 'Transportation Expense'),
    'Air Travel': ('Expenses', 'Discretionary Costs', 'Travel Expense'),
    'Lodging': ('Expenses', 'Discretionary Costs', 'Travel Expense'),
    'Fitness': ('Expenses', 'Discretionary Costs', 'Fitness Expense'),
    'Personal Care': ('Expenses', 'Discretionary Costs', 'Personal Care Expense'),
    'Education': ('Expenses', 'Discretionary Costs', 'Education Expense'),
    'Charitable Contributions': ('Expenses', 'Discretionary Costs', 'Donations Expense'),
    'Bank Fees': ('Expenses', 'Fixed Costs', 'Bank Fees Expense'),
    # Owner policy: cash is treated as spent when it is withdrawn.
    'Cash Spending': ('Expenses', 'Discretionary Costs', 'Cash Expense'),
    'Water and Sewer': ('Expenses', 'Fixed Costs', 'Utilities Expense'),
    'Waste Disposal': ('Expenses', 'Fixed Costs', 'Utilities Expense'),
}


def planned_chart(existing):
    """Add explicit chart extensions; refuse to reinterpret existing accounts."""
    rows = {row['name']: dict(row) for row in existing}
    for name, (element, classification, account) in ADDITIONAL_ACCOUNTS.items():
        desired = dict(name=name, account=account, classification=classification, element=element)
        if name in rows and rows[name] != desired:
            raise ValueError('Existing chart account conflicts with the planned extension')
        rows[name] = desired
    return sorted(rows.values(), key=lambda row: (row['element'], row['name']))


def install_mapping_columns(connection):
    for clause in (
        "ADD COLUMN IF NOT EXISTS match_mode varchar NOT NULL DEFAULT 'keyword'",
        'ADD COLUMN IF NOT EXISTS review_required boolean NOT NULL DEFAULT false',
        'ADD COLUMN IF NOT EXISTS review_note text',
        'ADD COLUMN IF NOT EXISTS provenance json',
    ):
        connection.execute(text('ALTER TABLE admin.mappings ' + clause))
    connection.execute(text("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
          WHERE conrelid='admin.mappings'::regclass AND conname='mapping_match_mode') THEN
          ALTER TABLE admin.mappings ADD CONSTRAINT mapping_match_mode
          CHECK (match_mode IN ('keyword', 'exact'));
        END IF;
    END $$"""))


def mapping_values(category):
    return dict(positive_debit_subaccount_id=None,
                positive_credit_subaccount_id=category,
                negative_debit_subaccount_id=None if category == 'Salary' else category,
                negative_credit_subaccount_id=None)


def persist_exact_mapping(item, snapshot, review_hash):
    """Save a reusable literal description rule, never a broader inferred match."""
    keyword = item['source']['description'].lower()
    if not keyword:
        raise ValueError('An empty description requires a source-specific mapping')
    desired = mapping_values(item['category'])
    existing = db.session.query(Mappings).filter(
        Mappings.source=='ofx', func.lower(Mappings.keyword)==keyword).one_or_none()
    if existing is not None:
        if existing.match_mode != 'exact' or any(getattr(existing, key) != value
                                                 for key, value in desired.items()):
            raise ValueError('An existing mapping conflicts with the reviewed classification')
        return existing, False
    mapping = Mappings(source='ofx', keyword=keyword, match_mode='exact',
                       review_required=item['category'] in SUSPENSE_ACCOUNTS,
                       review_note=item['review_reason'],
                       provenance={'model': snapshot.get('model'),
                                   'model_digest': snapshot.get('model_digest'),
                                   'review_sha256': review_hash,
                                   'proposal_sha256': item['proposal_sha256']}, **desired)
    db.session.add(mapping)
    db.session.flush()
    return mapping, True


def rebuild_trial_balances():
    """Rebuild derived balances under the posting transaction's exclusive lock.

    Group once per interval, then carry each account forward over the global
    set of occupied periods. The independent Python ledger audit must still
    validate the result before the calling transaction commits.
    """
    db.session.execute(text('DELETE FROM bookkeeping.trial_balances'))
    db.session.execute(text('''WITH legs AS (
        SELECT timestamp,debit_subaccount subaccount,functional_amount debit,0::numeric credit
          FROM bookkeeping.journal_entries
        UNION ALL
        SELECT timestamp,credit_subaccount,0::numeric,functional_amount
          FROM bookkeeping.journal_entries
      ), changes AS (
        SELECT format.interval, to_char(timestamp AT TIME ZONE 'UTC',format.interval) period,
               subaccount,sum(debit) debit,sum(credit) credit
          FROM legs CROSS JOIN (VALUES ('YYYY'),('YYYY-Q'),('YYYY-MM'),('YYYY-WW'),('YYYY-MM-DD')) format(interval)
          GROUP BY format.interval,period,subaccount
      ), periods AS (SELECT DISTINCT interval,period FROM changes),
      first_seen AS (SELECT interval,subaccount,min(period) first_period FROM changes GROUP BY interval,subaccount),
      grid AS (
        SELECT p.interval,p.period,a.subaccount,coalesce(c.debit,0) debit,coalesce(c.credit,0) credit
          FROM periods p JOIN first_seen a ON p.interval=a.interval AND p.period>=a.first_period
          LEFT JOIN changes c ON c.interval=p.interval AND c.period=p.period AND c.subaccount=a.subaccount
      ), totals AS (
        SELECT *,sum(debit) OVER w debit_balance,sum(credit) OVER w credit_balance
          FROM grid WINDOW w AS (PARTITION BY interval,subaccount ORDER BY period ROWS UNBOUNDED PRECEDING)
      )
      INSERT INTO bookkeeping.trial_balances
        (subaccount,period_interval,period,debit_balance,credit_balance,net_balance,
         debit_changes,credit_changes,net_changes)
      SELECT subaccount,interval,period,debit_balance,credit_balance,debit_balance-credit_balance,
             debit,credit,debit-credit FROM totals'''))
