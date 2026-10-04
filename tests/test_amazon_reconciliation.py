"""Native order evidence must not turn plausible coincidences into postings."""
from copy import deepcopy

import pytest

from pacioli.functions.amazon_reconciliation import match_records


def record(**updates):
    value = dict(
        key='retail:order-1', kind='retail', order_id='order-1',
        currency='USD', date='2026-09-10', amount='10.00', card_last4='7331',
        lines=[dict(kind='item', title='Example item', amount='10.00')],
        source_rows=[dict(table_id=1, row=1)], blockers=[],
    )
    value.update(updates)
    return value


def transaction(**updates):
    value = dict(
        id='bank-1', date='2026-09-12', amount='-10.00', account_id=5,
        account='Card (7331)', currency='USD', card_last4='7331',
        suspense=True, journal_state='suspense', category='Bookkeeping Suspense',
    )
    value.update(updates)
    return value


def account(**updates):
    value = dict(id=5, last4='7331', currency='USD',
                 first_date='2024-09-25', last_date='2026-09-25')
    value.update(updates)
    return value


def run(records=None, transactions=None, accounts=None, booked=None):
    return match_records(
        [record()] if records is None else records,
        [transaction()] if transactions is None else transactions,
        [account()] if accounts is None else accounts,
        [] if booked is None else booked,
    )


def by_key(result):
    return {row['key']: row for row in result['records']}


def test_amazon_reconciliation_exact_unique_evidence_is_only_a_candidate():
    result = run()
    row = result['records'][0]
    assert row['status'] == 'unique_candidate'
    assert [candidate['id'] for candidate in row['candidates']] == ['bank-1']
    assert result['summary']['status_counts']['unique_candidate'] == 1
    assert result['summary']['records'] == 1
    assert not result['unmatched_bank']
    # Matching is a proposal: this API returns no journal or committed booking.
    assert row.get('existing_bundle_id') is None


@pytest.mark.parametrize('day,expected', [
    ('2026-09-02', 'unmatched'), ('2026-09-03', 'unique_candidate'),
    ('2026-09-17', 'unique_candidate'), ('2026-09-18', 'unmatched'),
])
def test_amazon_reconciliation_date_window_has_inclusive_seven_day_bounds(day, expected):
    result = run(transactions=[transaction(date=day)])
    assert result['records'][0]['status'] == expected


@pytest.mark.parametrize('bank_amount', ['10.00', '-9.99', '-10.01'])
def test_amazon_reconciliation_requires_exact_opposite_signed_amount(bank_amount):
    result = run(transactions=[transaction(amount=bank_amount)])
    assert result['records'][0]['status'] == 'unmatched'
    assert len(result['unmatched_bank']) == 1


def test_amazon_reconciliation_fractional_cent_bank_amount_is_rejected():
    with pytest.raises(ValueError):
        run(transactions=[transaction(amount='-10.004')])


def test_amazon_reconciliation_refund_matches_only_the_bank_credit():
    result = run(records=[record(kind='refund', amount='-10.00')], transactions=[
        transaction(id='purchase', amount='-10.00'),
        transaction(id='refund', amount='10.00'),
    ])
    row = result['records'][0]
    assert row['status'] == 'unique_candidate'
    assert [candidate['id'] for candidate in row['candidates']] == ['refund']
    assert [item['id'] for item in result['unmatched_bank']] == ['purchase']


def test_amazon_reconciliation_known_card_mismatch_is_not_a_match():
    result = run(
        transactions=[transaction(account_id=4, card_last4='7251')],
        accounts=[account(), account(id=4, last4='7251')],
    )
    assert result['records'][0]['status'] == 'unmatched'
    assert not result['records'][0]['candidates']


@pytest.mark.parametrize('record_change,accounts_change', [
    ({'card_last4': None}, None),
    ({'currency': 'EUR'}, None),
    ({}, []),
    ({}, [account(currency='EUR')]),
])
def test_amazon_reconciliation_missing_or_unregistered_currency_evidence_cannot_be_unique(
        record_change, accounts_change):
    result = run(records=[record(**record_change)], accounts=accounts_change)
    assert result['records'][0]['status'] == 'needs_evidence'
    assert result['records'][0]['reasons']


def test_amazon_reconciliation_foreign_archive_and_bank_currency_need_evidence():
    result = run(
        records=[record(currency='EUR')],
        transactions=[transaction(currency='EUR')],
        accounts=[account(currency='EUR')],
    )
    assert result['records'][0]['status'] == 'needs_evidence'
    assert not result['records'][0]['candidates']
    assert result['records'][0]['reasons']


@pytest.mark.parametrize('journal_state', ['inconsistent', 'unposted', 'allocated'])
def test_amazon_reconciliation_unverified_or_classified_journal_needs_review(journal_state):
    result = run(transactions=[transaction(journal_state=journal_state, suspense=False)])
    assert result['records'][0]['status'] == 'needs_evidence'


def test_amazon_reconciliation_no_nearest_date_winner_among_equal_amounts():
    result = run(transactions=[
        transaction(id='near', date='2026-09-10'),
        transaction(id='far', date='2026-09-17'),
    ])
    row = result['records'][0]
    assert row['status'] == 'ambiguous'
    assert {candidate['id'] for candidate in row['candidates']} == {'near', 'far'}


def test_amazon_reconciliation_bank_transaction_cannot_explain_two_orders():
    result = run(records=[record(), record(key='retail:order-2', order_id='order-2')])
    assert all(row['status'] == 'ambiguous' for row in result['records'])


@pytest.mark.parametrize('blocked_change', [
    {'card_last4': None},
    {'blockers': ['Mixed payment requires review']},
])
def test_amazon_reconciliation_blocked_claimant_still_prevents_false_uniqueness(blocked_change):
    result = run(records=[record(), record(key='retail:order-2', order_id='order-2', **blocked_change)])
    rows = by_key(result)
    assert rows['retail:order-1']['status'] == 'ambiguous'
    assert rows['retail:order-2']['status'] in ('needs_evidence', 'ambiguous')
    assert rows['retail:order-2']['candidates']


def test_amazon_reconciliation_card_last4_collision_needs_identity_evidence():
    result = run(accounts=[account(), account(id=9)])
    assert result['records'][0]['status'] in ('needs_evidence', 'ambiguous')


def booked(**updates):
    value = dict(transaction_id='bank-1', bundle_id=23, order_ids=['order-1'],
                 amount='10.00', valid=True)
    value.update(updates)
    return value


def test_amazon_reconciliation_existing_booking_is_corroborated_without_reallocation():
    result = run(
        transactions=[transaction(journal_state='allocated', suspense=False)],
        booked=[booked()],
    )
    row = result['records'][0]
    assert row['status'] == 'already_booked'
    assert row.get('existing_bundle_id') == 23 or any(
        candidate.get('existing_bundle_id') == 23 for candidate in row['candidates'])


@pytest.mark.parametrize('booking_change', [
    {'valid': False}, {'order_ids': ['different-order']}, {'amount': '9.99'},
])
def test_amazon_reconciliation_booking_disagreement_never_releases_bank_transaction(booking_change):
    result = run(booked=[booked(**booking_change)])
    assert result['records'][0]['status'] == 'needs_evidence'
    assert result['records'][0]['reasons']


def test_amazon_reconciliation_reserved_booking_still_competes_with_other_order():
    result = run(
        records=[record(), record(key='retail:order-2', order_id='order-2')],
        transactions=[transaction(journal_state='allocated', suspense=False)],
        booked=[booked()],
    )
    rows = by_key(result)
    assert rows['retail:order-1']['status'] == 'already_booked'
    assert rows['retail:order-2']['status'] in ('needs_evidence', 'ambiguous')


def test_amazon_reconciliation_bank_history_is_specific_to_payment_account():
    result = run(
        records=[record(date='2024-08-01')], transactions=[],
        accounts=[account(), account(id=4, last4='7251', first_date='2020-01-01')],
    )
    assert result['records'][0]['status'] == 'outside_bank_history'


def test_amazon_reconciliation_missing_card_or_empty_history_cannot_assert_outside_coverage():
    for records, accounts in [
        ([record(date='2010-01-01', card_last4=None)], [account()]),
        ([record()], [account(first_date=None, last_date=None)]),
    ]:
        result = run(records=records, transactions=[], accounts=accounts)
        assert result['records'][0]['status'] == 'needs_evidence'


def test_amazon_reconciliation_does_not_guess_split_charge_subsets():
    result = run(transactions=[
        transaction(id='part-one', amount='-4.00'),
        transaction(id='part-two', amount='-6.00'),
    ])
    assert result['records'][0]['status'] == 'unmatched'
    assert len(result['unmatched_bank']) == 2


@pytest.mark.parametrize('source', ['records', 'transactions'])
def test_amazon_reconciliation_duplicate_source_identities_are_rejected(source):
    inputs = dict(
        records=[record(), record(order_id='conflicting-order', amount='20.00')]
        if source == 'records' else [record()],
        transactions=[transaction(), transaction(amount='-20.00')]
        if source == 'transactions' else [transaction()],
    )
    with pytest.raises(ValueError):
        run(**inputs)


def test_amazon_reconciliation_replay_is_deterministic_and_does_not_mutate_inputs():
    inputs = ([record(), record(key='retail:order-2', order_id='order-2', amount='20.00')],
              [transaction(), transaction(id='bank-2', amount='-20.00')],
              [account()], [])
    before = deepcopy(inputs)
    first = match_records(*inputs)
    second = match_records(*inputs)
    assert first == second
    assert inputs == before
    reversed_result = match_records(list(reversed(inputs[0])), list(reversed(inputs[1])), inputs[2], inputs[3])
    assert by_key(first) == by_key(reversed_result)
