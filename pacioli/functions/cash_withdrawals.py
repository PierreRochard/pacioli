"""Owner policy: ATM and other cash withdrawals are spent when withdrawn.

Pacioli has no feed for physical cash, so a withdrawal parked in Transfer
Clearing can never be matched. This selects only exact provisional clearing
rules whose every linked journal is an unmatched, unsplit bank outflow described
as a cash withdrawal, and moves them through the audited reclassification.
Nothing is inferred from amounts; transfers, fees and brokerage are excluded.
"""
import re

from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.local_mapping_posting import digest
from pacioli.functions.reconciliation import evidence
from pacioli.functions.suspense_posting import reclassify, snapshot_mappings


CATEGORY = 'Cash Spending'
SOURCE_CATEGORY = 'Transfer Clearing'
POLICY = ('Owner decision 2026-09-28: ATM and other cash withdrawals are Cash Spending '
          'when withdrawn. Pacioli has no feed for physical cash, so they cannot clear '
          'as transfers.')
# Bank text uses both WITHDRAW and WITHDRAWAL, sometimes run into digits.
WITHDRAWAL = re.compile(r'WITHDRAW', re.I)
NOT_CASH = re.compile(r'TRANSFER|XFER|ONLINE|WIRE|\bACH\b|\bPPD\b|WEB ID|ZELLE|VENMO|PAYPAL|'
                      r'COINBASE|SCHWAB|VANGUARD|FIDELITY|ROBINHOOD|KRAKEN|BROKERAGE|\bFEE\b', re.I)


def is_cash_withdrawal(description):
    description = description or ''
    return bool(WITHDRAWAL.search(description)) and not NOT_CASH.search(description)


def select_mappings(session):
    """Eligible mapping IDs plus counts of withdrawal rules that were left alone."""
    rules = session.execute(text('''SELECT id, keyword FROM admin.mappings
        WHERE source='ofx' AND match_mode='exact' AND review_required
          AND positive_credit_subaccount_id=:clearing AND negative_debit_subaccount_id=:clearing
          AND positive_debit_subaccount_id IS NULL AND negative_credit_subaccount_id IS NULL
        ORDER BY id'''), {'clearing': SOURCE_CATEGORY}).all()
    ids = [rule.id for rule in rules if is_cash_withdrawal(rule.keyword)]
    journals = session.execute(text('''SELECT j.mapping_id, j.transaction_source, j.allocation_key,
            j.debit_subaccount, j.credit_subaccount, t.amount,
            EXISTS (SELECT 1 FROM bookkeeping.transfer_match_legs l WHERE l.journal_id=j.id) AS matched
        FROM bookkeeping.journal_entries j
        LEFT JOIN ofx.transactions t ON t.id=j.transaction_id AND j.transaction_source='ofx'
        WHERE j.mapping_id=ANY(:ids)'''), {'ids': ids}).all()
    reasons = {}
    for row in journals:
        reason = ('split or non-OFX journal' if row.transaction_source != 'ofx' or row.allocation_key != '0'
                  else 'already in a saved transfer match' if row.matched
                  else 'incoming amount' if row.amount is None or row.amount > 0
                  else 'journal not in Transfer Clearing'
                  if SOURCE_CATEGORY not in (row.debit_subaccount, row.credit_subaccount) else None)
        if reason:
            reasons.setdefault(row.mapping_id, reason)
    linked = {row.mapping_id for row in journals}
    for mapping_id in ids:
        if mapping_id not in linked:
            reasons.setdefault(mapping_id, 'no linked journals')
    skipped = {}
    for reason in reasons.values():
        skipped[reason] = skipped.get(reason, 0) + 1
    return [mapping_id for mapping_id in ids if mapping_id not in reasons], skipped


def ensure_category(session):
    """Add only the Cash Spending subaccount and its account; never alter existing rows."""
    from pacioli.models import Accounts, Classifications, Subaccounts
    if session.query(Subaccounts).filter_by(name=CATEGORY).first() is not None:
        return False
    classification = session.query(Classifications).filter_by(name='Discretionary Costs').one()
    if classification.parent != 'Expenses':
        raise ValueError('Discretionary Costs must be an expense classification')
    if session.query(Accounts).filter_by(name='Cash Expense').first() is None:
        session.add(Accounts(name='Cash Expense', cash_source='Operating', parent=classification.name))
        session.flush()
    session.add(Subaccounts(name=CATEGORY, parent='Cash Expense'))
    session.commit()
    return True


def reclassify_cash_withdrawals(reviewer, apply=False):
    """Preview by default. With apply, commit the change and its evidence together."""
    if not reviewer or not reviewer.strip():
        raise ValueError('A reviewer is required')
    if apply:
        ensure_category(db.session)
    ids, skipped = select_mappings(db.session)
    summary = {'category': CATEGORY, 'selected_mappings': len(ids), 'skipped_mappings': skipped}
    if not ids:
        db.session.rollback()
        return dict(summary, selected_entries=0, would_change_entries=0, changed_entries=0)
    if not apply and db.session.execute(text('SELECT 1 FROM bookkeeping.subaccounts WHERE name=:name'),
                                        {'name': CATEGORY}).first() is None:
        selected = db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries '
                                           'WHERE mapping_id=ANY(:ids)'), {'ids': ids}).scalar()
        db.session.rollback()
        return dict(summary, selected_entries=selected, chart_addition=CATEGORY + ' under Cash Expense')
    snapshot = snapshot_mappings(ids)
    db.session.rollback()
    policy_sha256 = digest({'policy': POLICY})
    review = {'snapshot_sha256': snapshot['snapshot_sha256'], 'reviewer': reviewer,
              'authorization': POLICY, 'policy_sha256': policy_sha256,
              'approvals': [{'mapping_id': mapping_id, 'category': CATEGORY, 'reason': POLICY,
                             'proposal_sha256': digest({'policy_sha256': policy_sha256,
                                                        'mapping_id': mapping_id})}
                            for mapping_id in ids]}
    result = reclassify(snapshot, review, commit=apply, finalize=False,
                        source_category=SOURCE_CATEGORY)
    if apply and result['changed_entries']:
        evidence('cash_withdrawal_policy', {'policy': POLICY, 'mapping_ids': ids,
                                            'snapshot_sha256': snapshot['snapshot_sha256'],
                                            'result': result}, reviewer)
        db.session.commit()
    return dict(summary, **result)
