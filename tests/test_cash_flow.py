"""Direct cash flow boundaries, consolidation, gross amounts and evidence scope."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.cash_flow import cash_flow_details, cash_flow_report
from pacioli.functions.journal_posting import post_pair


START = datetime(2048, 1, 1, tzinfo=timezone.utc)
END = datetime(2049, 1, 1, tzinfo=timezone.utc)


def entry(debit, credit, amount, when=None, **kwargs):
    row = post_pair(timestamp=when or START, debit=debit, credit=credit,
                    functional_amount=amount, **kwargs)
    db.session.flush()
    return row


def by_section(report):
    return {row['key']: row for row in report['sections']}


def test_direct_cash_flow_gross_cash_only_and_full_reconciliation(clean_ledger):
    entry('Chase Checking', 'Personal Equity', '1000', START-timedelta(microseconds=1))
    entry('Chase Checking', 'Salary', '500')
    entry('Rent', 'Chase Checking', '120')
    entry('Chase Checking', 'Rent', '20')
    entry('Cold Wallet', 'Chase Checking', '200')
    entry('Chase Checking', 'Notes Payable', '400')
    entry('Notes Payable', 'Chase Checking', '50')
    entry('Bookkeeping Suspense', 'Chase Checking', '30')
    entry('Credit Card', 'Chase Checking', '70')
    entry('Chase Savings', 'Chase Checking', '100')
    entry('Groceries', 'Credit Card', '80')  # purchase on credit is noncash
    entry('Chase Checking', 'Personal Equity', '40',
          transaction_source='statement_opening', transaction_id='fixture')
    entry('Chase Checking', 'Salary', '9000', END)  # exclusive bound

    report = cash_flow_report(db.session, START, END)
    sections = by_section(report)
    assert (report['opening_cash'], report['closing_cash'], report['net_cash_change'],
            report['classified_cash_change'], report['adjustments'], report['difference']) == (
                Decimal('1000'), Decimal('1490'), Decimal('490'), Decimal('450'),
                Decimal('40'), Decimal('0'))
    assert (sections['operating']['receipts'], sections['operating']['payments'],
            sections['operating']['net']) == (Decimal('520'), Decimal('120'), Decimal('400'))
    assert report['investing'] == -200
    assert report['financing'] == 350
    assert report['unclassified'] == -100
    assert sections['internal']['receipts'] == sections['internal']['payments'] == 100
    assert sections['internal']['count'] == 2
    assert cash_flow_details(db.session, START, END, 'internal')['total'] == 2
    assert report['counts']['noncash_journals'] == 1
    assert report['counts']['journals'] == 11
    accounts = {row['name']: row for row in report['accounts']}
    assert accounts['Chase Checking']['closing'] == 1390
    assert accounts['Chase Savings']['closing'] == 100
    assert all(isinstance(report[key], Decimal) for key in (
        'opening_cash', 'closing_cash', 'net_cash_change', 'difference'))


def test_cash_flow_utc_bounds_are_independent_of_session_timezone(clean_ledger):
    entry('Chase Checking', 'Salary', '1.01', START-timedelta(microseconds=1))
    entry('Chase Checking', 'Salary', '2.02', START)
    entry('Rent', 'Chase Checking', '0.03', END-timedelta(microseconds=1))
    entry('Chase Checking', 'Salary', '100', END)
    db.session.execute(text("SET LOCAL timezone='Pacific/Honolulu'"))
    report = cash_flow_report(db.session, START, END)
    assert report['opening_cash'] == Decimal('1.01')
    assert report['operating'] == Decimal('1.99')
    assert report['closing_cash'] == Decimal('3.00')
    assert report['counts']['journals'] == 2


def test_matched_cash_transfer_eliminates_but_timing_and_card_do_not(clean_ledger):
    left = entry('Transfer Clearing', 'Chase Checking', '75')
    right = entry('Chase Savings', 'Transfer Clearing', '75')
    timing = entry('Transfer Clearing', 'Chase Checking', '22')
    card = entry('Transfer Clearing', 'Chase Checking', '31')
    entry('Credit Card', 'Transfer Clearing', '31')
    unmatched = entry('Transfer Clearing', 'Chase Checking', '11')
    context = {
        left.id: dict(kind='cash_transfer', match_id=1),
        right.id: dict(kind='cash_transfer', match_id=1),
        timing.id: dict(kind='cash_transfer_timing', match_id=2),
        card.id: dict(kind='card_settlement', match_id=3),
    }
    report = cash_flow_report(db.session, START, END, transfer_kinds=context)
    sections = by_section(report)
    assert report['net_cash_change'] == -64
    assert report['adjustments'] == -22
    assert report['unclassified'] == -42
    assert report['difference'] == 0
    assert sections['internal']['count'] == 2
    assert sections['internal']['receipts'] == sections['internal']['payments'] == 75
    assert {row['label'] for row in sections['unclassified']['rows']} == {
        'Credit-card settlements — allocation pending', 'Transfer Clearing'}
    detail = cash_flow_details(db.session, START, END, 'unclassified',
                              'Transfer Clearing', transfer_kinds=context)
    assert detail['total'] == 1
    assert detail['rows'][0]['journal_id'] == unmatched.id
    assert detail['rows'][0]['amount'] == -11


def test_unbalanced_internal_metadata_is_visible_as_reconciliation_difference(clean_ledger):
    leg = entry('Transfer Clearing', 'Chase Checking', '25')
    report = cash_flow_report(db.session, START, END, transfer_kinds={
        leg.id: dict(kind='cash_transfer', match_id=1)})
    assert report['internal_net'] == report['difference'] == -25


def test_report_only_evidence_never_changes_journal_and_shared_details(clean_ledger):
    payroll = entry('Bookkeeping Suspense', 'Chase Checking', '123.45')
    other = entry('Bookkeeping Suspense', 'Chase Checking', '67.89')
    label = "Household payroll, taxes and fees ' <reviewed>"
    overrides = {payroll.id: dict(section='Operating', label=label, evidence_id=42)}
    report = cash_flow_report(db.session, START, END, overrides=overrides)
    assert report['operating'] == Decimal('-123.45')
    assert report['unclassified'] == Decimal('-67.89')
    detail = cash_flow_details(db.session, START, END, 'operating', label,
                              overrides=overrides)
    assert detail['total'] == 1
    assert detail['rows'][0]['journal_id'] == payroll.id
    assert detail['rows'][0]['evidence_id'] == 42
    db.session.expire_all()
    assert payroll.debit_subaccount == other.debit_subaccount == 'Bookkeeping Suspense'
    assert cash_flow_report(db.session, START, END)['operating'] == 0


def test_empty_report_and_out_of_range_detail_page(clean_ledger):
    report = cash_flow_report(db.session, START, END)
    assert report['opening_cash'] == report['closing_cash'] == report['difference'] == 0
    assert all(row['net'] == 0 for row in report['sections'])
    entry('Rent', 'Chase Checking', '9.99')
    details = cash_flow_details(db.session, START, END, 'operating', 'Rent', page=2)
    assert details['total'] == 1
    assert details['rows'] == []
    assert details['page_size'] == 100


def test_section_total_drilldown_matches_summary_cash_movement_count(clean_ledger):
    salary = entry('Chase Checking', 'Salary', '20')
    rent = entry('Rent', 'Chase Checking', '7')
    refund = entry('Chase Checking', 'Rent', '1.25')
    entry('Notes Payable', 'Chase Checking', '3')
    report = cash_flow_report(db.session, START, END)
    details = cash_flow_details(db.session, START, END, 'operating')
    assert details['total'] == by_section(report)['operating']['count'] == 3
    assert {row['journal_id'] for row in details['rows']} == {salary.id, rent.id, refund.id}
    assert {row['label'] for row in details['rows']} == {'Rent', 'Salary'}


def test_gross_inflows_and_outflows_rank_independently_without_netting_refunds(clean_ledger):
    rent_one = entry('Rent', 'Chase Checking', '700')
    rent_two = entry('Rent', 'Chase Checking', '300')
    rent_refund = entry('Chase Checking', 'Rent', '1')
    groceries = entry('Groceries', 'Chase Checking', '250')
    groceries_refund = entry('Chase Checking', 'Groceries', '25')
    salary = entry('Chase Checking', 'Salary', '900')
    restaurants = entry('Restaurants', 'Chase Checking', '250')

    report = cash_flow_report(db.session, START, END)
    operating = by_section(report)['operating']
    assert [(row['label'], row['amount'], row['count']) for row in operating['inflows']] == [
        ('Salary', Decimal('900'), 1), ('Groceries', Decimal('25'), 1),
        ('Rent', Decimal('1'), 1)]
    assert [(row['label'], row['amount'], row['count']) for row in operating['outflows']] == [
        ('Rent', Decimal('1000'), 2), ('Groceries', Decimal('250'), 1),
        ('Restaurants', Decimal('250'), 1)]
    assert (operating['receipts'], operating['payments'], operating['net'], operating['count']) == (
        Decimal('926'), Decimal('1500'), Decimal('-574'), 7)
    assert sum(row['count'] for row in operating['rows']) == 7
    assert report['net_cash_change'] == Decimal('-574')
    assert report['difference'] == 0

    inflows = cash_flow_details(db.session, START, END, 'operating', direction='inflow')
    outflows = cash_flow_details(db.session, START, END, 'operating', direction='outflow')
    assert inflows['total'] == 3
    assert outflows['total'] == 4
    assert {row['journal_id'] for row in inflows['rows']} == {
        rent_refund.id, groceries_refund.id, salary.id}
    assert {row['journal_id'] for row in outflows['rows']} == {
        rent_one.id, rent_two.id, groceries.id, restaurants.id}
    assert all(row['amount'] > 0 for row in inflows['rows'])
    assert all(row['amount'] < 0 for row in outflows['rows'])
    rent_payments = cash_flow_details(db.session, START, END, 'operating', 'Rent',
                                     direction='outflow')
    assert rent_payments['total'] == 2
    assert {row['journal_id'] for row in rent_payments['rows']} == {rent_one.id, rent_two.id}
    past_end = cash_flow_details(db.session, START, END, 'operating', 'Rent',
                                direction='inflow', page=2)
    assert past_end['total'] == 1
    assert past_end['rows'] == []


@pytest.mark.parametrize('direction', ['', 'both', 'INFLOW', True, 1])
def test_cash_flow_invalid_direction_fails_before_database_access(direction):
    with pytest.raises(ValueError, match='direction'):
        cash_flow_details(None, START, END, 'operating', direction=direction)


def test_postgres_money_arithmetic_does_not_use_python_decimal_context(clean_ledger):
    entry('Chase Checking', 'Personal Equity', '10000000.01', START-timedelta(microseconds=1))
    entry('Chase Checking', 'Salary', '999999.99')
    entry('Rent', 'Chase Checking', '0.01')
    with localcontext() as context:
        context.prec = 6
        report = cash_flow_report(db.session, START, END)
    assert report['opening_cash'] == Decimal('10000000.01')
    assert report['closing_cash'] == Decimal('10999999.99')
    assert report['net_cash_change'] == report['operating'] == Decimal('999999.98')
    assert report['difference'] == 0


def test_only_cash_accounts_with_ledger_history_or_bank_link_are_shown(clean_ledger):
    from pacioli.models import Subaccounts

    unused = Subaccounts(name='Unused cash-flow test account', parent='Cash')
    linked = Subaccounts(name='Linked cash-flow test account', parent='Cash')
    db.session.add_all([unused, linked])
    db.session.flush()
    bank_id = db.session.execute(text("""INSERT INTO ofx.acctfrom(subclass,name)
        VALUES ('bankacctfrom', :name) RETURNING id"""), {'name': linked.name}).scalar()
    try:
        entry('Chase Checking', 'Salary', '10', START-timedelta(microseconds=1))
        report = cash_flow_report(db.session, START, END)
        accounts = {row['name']: row for row in report['accounts']}
        assert unused.name not in accounts
        assert accounts[linked.name]['closing'] == 0
        assert accounts['Chase Checking']['opening'] == accounts['Chase Checking']['closing'] == 10
    finally:
        db.session.execute(text('DELETE FROM ofx.acctfrom WHERE id=:id'), {'id': bank_id})
        db.session.delete(unused)
        db.session.delete(linked)
        db.session.flush()


def test_drilldown_descriptions_preserve_canonical_account_identity_and_splits(clean_ledger):
    from uuid import uuid4
    from pacioli.functions.source_identity import source_identity
    from pacioli.models import BankFeedAccounts

    fitid = uuid4().hex
    sources = []
    for name, amount, description, memo in (
            ('Chase Checking', '-8.50', 'RENT ', 'SEPTEMBER'),
            ('Chase Savings', '-2.00', 'OTHER ACCOUNT RENT', None)):
        account_id = db.session.execute(text("""INSERT INTO ofx.acctfrom(subclass,name)
            VALUES ('bankacctfrom', :name) RETURNING id"""), {'name': name}).scalar()
        db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
        db.session.flush()
        db.session.execute(text("""INSERT INTO ofx.stmttrn
            (fitid,acctfrom_id,trntype,dtposted,trnamt,name,memo)
            VALUES (:fitid,:account_id,'OTHER',:when,:amount,:description,:memo)"""),
            dict(fitid=fitid, account_id=account_id, when=START.replace(tzinfo=None),
                 amount=Decimal(amount), description=description, memo=memo))
        sources.append(source_identity(db.session, account_id, fitid))
    first = entry('Rent', 'Chase Checking', '3.00', transaction_source='ofx',
                  transaction_id=sources[0], allocation_key='part-a', memo='Journal note')
    second = entry('Rent', 'Chase Checking', '5.50', transaction_source='ofx',
                   transaction_id=sources[0], allocation_key='part-b')
    other = entry('Rent', 'Chase Savings', '2.00', transaction_source='ofx',
                  transaction_id=sources[1])
    manual = entry('Rent', 'Chase Checking', '1.00', memo='Manual rent note')
    details = cash_flow_details(db.session, START, END, 'operating', 'Rent')
    assert details['total'] == len(details['rows']) == 4
    descriptions = {row['journal_id']: row['source_description'] for row in details['rows']}
    assert descriptions == {first.id: 'RENT SEPTEMBER', second.id: 'RENT SEPTEMBER',
                            other.id: 'OTHER ACCOUNT RENT', manual.id: 'Manual rent note'}


@pytest.mark.parametrize('start,end', [(START.replace(tzinfo=None), END),
                                     (END, START), (START, START)])
def test_cash_flow_invalid_bounds_fail_before_database_access(start, end):
    with pytest.raises(ValueError):
        cash_flow_report(None, start, end)


@pytest.mark.parametrize('context', [
    {True: dict(section='Operating', label='Payroll', evidence_id=1)},
    {1: dict(section='unclassified', label='Payroll', evidence_id=1)},
    {1: dict(section='Operating', label='', evidence_id=1)},
    {1: dict(section='Operating', label='Payroll', evidence_id=0)},
])
def test_cash_flow_invalid_evidence_context_fails_before_database_access(context):
    with pytest.raises(ValueError):
        cash_flow_report(None, START, END, overrides=context)
