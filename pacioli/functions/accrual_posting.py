"""Deterministic daily income and expense accrual postings."""

from datetime import datetime, time, timedelta, timezone

from pacioli.extensions import db
from pacioli.functions.accounting_report import chart_accounts
from pacioli.functions.journal_posting import decimal_amount, post_pair
from pacioli.models import Accruals, JournalEntries


def generate_accruals(through_date):
    """Stage missing daily accruals in the caller's transaction.

    Expense schedules debit expense and credit a payable. Income schedules
    debit a receivable and credit income. Existing dates are compared exactly;
    a changed schedule requires a reviewed reversal instead of rewriting it.
    """
    accounts = chart_accounts(db.session)
    added = []
    for schedule in db.session.query(Accruals).order_by(Accruals.id):
        kind = schedule.accrual_type.lower() if schedule.accrual_type else None
        if kind not in ('expense', 'income') or schedule.start_date > schedule.end_date:
            raise ValueError('Invalid accrual type or date range for schedule {}'.format(
                schedule.id))
        amount = decimal_amount(schedule.daily_amount, 'USD')
        income_element = accounts.get(schedule.income_statement_subaccount, (None,))[0]
        balance_element = accounts.get(schedule.balance_sheet_subaccount, (None,))[0]
        expected = (('Expenses', 'Liabilities') if kind == 'expense'
                    else ('Revenues', 'Assets'))
        if (income_element, balance_element) != expected:
            raise ValueError('Accrual {} has incompatible account categories'.format(
                schedule.id))
        debit, credit = ((schedule.income_statement_subaccount,
                          schedule.balance_sheet_subaccount)
                         if kind == 'expense' else
                         (schedule.balance_sheet_subaccount,
                          schedule.income_statement_subaccount))
        day = schedule.start_date
        while day <= min(schedule.end_date, through_date):
            if not schedule.business_days or day.weekday() < 5:
                source_id = '{}:{}'.format(schedule.id, day.isoformat())
                existing = (db.session.query(JournalEntries)
                            .filter_by(transaction_source='accrual',
                                       transaction_id=source_id).first())
                if existing is not None:
                    if ((existing.debit_subaccount, existing.credit_subaccount,
                         existing.functional_amount, existing.source_amount,
                         existing.source_currency,
                         existing.timestamp.astimezone(timezone.utc).date()) !=
                        (debit, credit, amount, amount, 'USD', day)):
                        raise ValueError('Accrual {} changed after posting {}'.format(
                            schedule.id, day))
                else:
                    added.append(post_pair(
                        timestamp=datetime.combine(day, time.min, tzinfo=timezone.utc),
                        debit=debit, credit=credit, functional_amount=amount,
                        transaction_source='accrual', transaction_id=source_id,
                        memo='{} accrual: {}'.format(kind.capitalize(), schedule.name)))
            day += timedelta(days=1)
    return added
