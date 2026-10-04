from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from pacioli.extensions import db
from pacioli.models import JournalEntries, Subaccounts, TrialBalances


@pytest.mark.parametrize('first_debit,first_credit,second_debit,second_credit,account,prior_balance', [
    ('Rent', 'Accounts Payable', 'Accounts Payable', 'Chase Checking',
     'Accounts Payable', Decimal('-100')),
    ('Accounts Receivable', 'Salary', 'Chase Checking', 'Accounts Receivable',
     'Accounts Receivable', Decimal('100')),
])
def test_accrual_and_settlement_update_trial_balance(
        clean_ledger, first_debit, first_credit, second_debit, second_credit,
        account, prior_balance):
    now = datetime.now(timezone.utc)

    for when, debit, credit in [
        (now - timedelta(days=40), first_debit, first_credit),
        (now, second_debit, second_credit),
    ]:
        entry = JournalEntries(timestamp=when,
                               debit_subaccount=debit,
                               credit_subaccount=credit,
                               functional_amount=Decimal('100'),
                               functional_currency='USD',
                               source_amount=Decimal('100'),
                               source_currency='USD')
        db.session.add(entry)
        db.session.commit()

    balances = (db.session.query(TrialBalances)
                .filter_by(subaccount=account, period_interval='YYYY-MM')
                .order_by(TrialBalances.period.desc()).limit(2).all())

    assert len(balances) == 2
    assert balances[0].net_balance == Decimal('0')
    assert balances[1].net_balance == prior_balance


def test_corrections_and_deletion_recalculate_balances(clean_ledger):
    suffix = uuid4().hex[:12]
    original = 'Original ' + suffix
    corrected = 'Corrected ' + suffix
    payable = 'Payable ' + suffix
    db.session.add_all([
        Subaccounts(name=original, parent='Rent Expense'),
        Subaccounts(name=corrected, parent='Groceries Expense'),
        Subaccounts(name=payable, parent='Accounts Payable'),
    ])
    db.session.commit()

    def balance(account, period):
        return (db.session.query(TrialBalances.net_balance)
                .filter_by(subaccount=account, period_interval='YYYY-MM',
                           period=period).scalar())

    entry = JournalEntries(timestamp=datetime(2035, 1, 10, tzinfo=timezone.utc),
                           debit_subaccount=original, credit_subaccount=payable,
                           functional_amount=Decimal('100'), functional_currency='USD',
                           source_amount=Decimal('100'), source_currency='USD')
    db.session.add(entry)
    db.session.commit()
    assert balance(original, '2035-01') == Decimal('100')

    entry.debit_subaccount = corrected
    entry.functional_amount = Decimal('200')
    entry.source_amount = Decimal('200')
    db.session.commit()
    assert balance(original, '2035-01') is None
    assert balance(corrected, '2035-01') == 200
    assert balance(payable, '2035-01') == -200

    entry.timestamp = datetime(2035, 2, 10, tzinfo=timezone.utc)
    db.session.commit()
    # A period with no remaining journal entries is removed, as refresh does.
    assert balance(corrected, '2035-01') is None
    assert balance(payable, '2035-01') is None
    assert balance(corrected, '2035-02') == 200
    assert balance(payable, '2035-02') == -200

    db.session.delete(entry)
    db.session.commit()
    assert balance(corrected, '2035-02') is None
    assert balance(payable, '2035-02') is None
