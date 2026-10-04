"""Cash withdrawals leave Transfer Clearing for Cash Spending, with full audit."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.accounting_report import audit_ledger
from pacioli.functions.cash_withdrawals import (CATEGORY, is_cash_withdrawal,
                                                reclassify_cash_withdrawals, select_mappings)
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.source_identity import source_identity
from pacioli.functions.suspense_advisor import POLICY
from pacioli.functions.suspense_posting import reclassify, snapshot_mappings
from pacioli.models import (AccountingEvidence, BankFeedAccounts, JournalEntries,
                            JournalEntryEvents, Mappings, TransferMatches, TransferMatchLegs)


def test_cash_withdrawal_wording():
    assert is_cash_withdrawal('ATM WITHDRAWAL 000123 09/01 MAIN ST')
    assert is_cash_withdrawal('WITHDRAWAL 09/14')
    assert is_cash_withdrawal('ATM WITHDRAWAL0001234 09/01')
    assert is_cash_withdrawal('NON-CHASE ATM WITHDRAW 123')
    assert not is_cash_withdrawal('ONLINE TRANSFER WITHDRAWAL TO SAV')
    assert not is_cash_withdrawal('NON-BANK ATM WITHDRAWAL FEE')
    assert not is_cash_withdrawal('COINBASE WITHDRAWAL')
    assert not is_cash_withdrawal('ATM CASH DEPOSIT')


@pytest.fixture
def withdrawals(clean_ledger):
    account = db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) "
                                      "VALUES ('bankacctfrom','Chase Checking') RETURNING id")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account, currency='USD'))
    tag = uuid4().hex[:4].upper()
    mapping_ids, journal_ids = {}, {}
    for key, description, amounts in (
            ('atm', 'ATM WITHDRAWAL00' + tag + ' MAIN ST', ('-60.00', '-100.00')),
            ('nonbank', 'NON-CHASE ATM WITHDRAW ' + tag, ('-40.00',)),
            ('plain', 'WITHDRAWAL ' + tag, ('-45.00',)),
            ('transfer', 'ONLINE TRANSFER WITHDRAWAL ' + tag, ('-500.00',)),
            ('reversal', 'ATM WITHDRAWAL ADJUSTMENT ' + tag, ('60.00',)),
            ('matched', 'ATM WITHDRAWAL MATCHED ' + tag, ('-80.00',))):
        mapping = Mappings(source='ofx', keyword=description.lower(), match_mode='exact',
                           positive_credit_subaccount_id='Transfer Clearing',
                           negative_debit_subaccount_id='Transfer Clearing',
                           review_required=True, review_note='Provisional clearing')
        db.session.add(mapping)
        db.session.flush()
        mapping_ids[key] = mapping.id
        for day, amount in enumerate(amounts, 1):
            amount = Decimal(amount)
            fitid = uuid4().hex
            when = datetime(2026, 2, day, 12, tzinfo=timezone.utc)
            db.session.execute(text('''INSERT INTO ofx.stmttrn(fitid,acctfrom_id,trntype,dtposted,trnamt,name)
                VALUES(:fitid,:account,'OTHER',:when,:amount,:name)'''),
                dict(fitid=fitid, account=account, when=when, amount=amount, name=description))
            debit, credit = (('Transfer Clearing', 'Chase Checking') if amount < 0
                             else ('Chase Checking', 'Transfer Clearing'))
            entry = post_pair(timestamp=when, debit=debit, credit=credit, functional_amount=abs(amount),
                              transaction_source='ofx', mapping_id=mapping.id, memo='Original record',
                              transaction_id=source_identity(db.session, account, fitid))
            db.session.flush()
            journal_ids.setdefault(key, []).append(entry.id)
    match = TransferMatches(fingerprint=uuid4().hex * 2, details=json.dumps({'legs': []}),
                            reviewer='fixture')
    db.session.add(match)
    db.session.flush()
    db.session.add(TransferMatchLegs(journal_id=journal_ids['matched'][0], match_id=match.id))
    db.session.commit()
    yield mapping_ids, journal_ids
    db.session.remove()
    db.session.execute(text('TRUNCATE bookkeeping.ofx_journal_sources, investments.brokerage_journal_sources, bookkeeping.opening_balances, '
                            'bookkeeping.transfer_match_legs, bookkeeping.transfer_matches, '
                            'bookkeeping.journal_entries, bookkeeping.trial_balances, '
                            'bookkeeping.journal_entry_events RESTART IDENTITY'))
    db.session.execute(text('DELETE FROM admin.mappings WHERE id=ANY(:ids)'), {'ids': list(mapping_ids.values())})
    db.session.execute(text('DELETE FROM bookkeeping.bank_feed_accounts WHERE ofx_account_id=:id'), {'id': account})
    db.session.execute(text('DELETE FROM ofx.stmttrn WHERE acctfrom_id=:id'), {'id': account})
    db.session.execute(text('DELETE FROM ofx.acctfrom WHERE id=:id'), {'id': account})
    db.session.commit()


def categories(journal_ids):
    db.session.expire_all()
    return [(entry.debit_subaccount, entry.credit_subaccount) for entry in
            db.session.query(JournalEntries).filter(JournalEntries.id.in_(journal_ids)).order_by(JournalEntries.id)]


def test_selection_is_limited_to_unmatched_cash_outflows(withdrawals):
    mapping_ids, _ = withdrawals
    ids, skipped = select_mappings(db.session)
    assert ids == [mapping_ids['atm'], mapping_ids['nonbank'], mapping_ids['plain']]
    assert skipped == {'incoming amount': 1, 'already in a saved transfer match': 1}


def test_preview_then_apply_moves_only_selected_journals(withdrawals):
    mapping_ids, journal_ids = withdrawals
    preview = reclassify_cash_withdrawals('owner@example.com')
    assert preview['selected_entries'] == preview['would_change_entries'] == 4
    assert preview['changed_entries'] == 0
    assert categories(journal_ids['atm']) == [('Transfer Clearing', 'Chase Checking')] * 2

    result = reclassify_cash_withdrawals('owner@example.com', apply=True)
    assert result['changed_entries'] == 4 and result['changed_mappings'] == 3
    assert categories(journal_ids['atm'] + journal_ids['nonbank'] + journal_ids['plain']) == [(CATEGORY, 'Chase Checking')] * 4
    for key in ('transfer', 'reversal', 'matched'):
        assert all('Transfer Clearing' in pair for pair in categories(journal_ids[key]))
    mapping = db.session.query(Mappings).get(mapping_ids['atm'])
    assert (mapping.negative_debit_subaccount_id, mapping.review_required) == (CATEGORY, False)
    assert mapping.provenance['suspense_review']['previous_category'] == 'Transfer Clearing'
    assert db.session.query(JournalEntryEvents).filter_by(action='UPDATE').count() == 4
    assert db.session.query(AccountingEvidence).filter_by(kind='cash_withdrawal_policy').count() == 1
    assert not [issue for issue in audit_ledger(db.session)
                if issue['kind'] not in ('classification_pending', 'bank_balance')]

    again = reclassify_cash_withdrawals('owner@example.com', apply=True)
    assert again['selected_mappings'] == 0 and again['changed_entries'] == 0


def test_matched_transfer_legs_cannot_be_reclassified(withdrawals):
    mapping_ids, _ = withdrawals
    snapshot = snapshot_mappings([mapping_ids['matched']])
    db.session.rollback()
    review = {'snapshot_sha256': snapshot['snapshot_sha256'], 'reviewer': 'owner@example.com',
              'authorization': 'Test', 'approvals': [{'mapping_id': mapping_ids['matched'],
              'category': CATEGORY, 'reason': 'Test', 'proposal_sha256': 'fixture'}]}
    with pytest.raises(ValueError, match='saved transfer match'):
        reclassify(snapshot, review, commit=True, source_category='Transfer Clearing')
    with pytest.raises(ValueError, match='provisional'):
        reclassify(snapshot, review, source_category='Chase Checking')


def test_advisor_policy_books_cash_withdrawals_as_spending():
    assert 'cash withdrawals -> Cash Spending' in POLICY
