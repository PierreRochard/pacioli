"""Real PostgreSQL checks for reviewed category corrections and rollback."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.accounting_report import audit_ledger
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.source_identity import source_identity
from pacioli.functions.suspense_posting import snapshot_mappings, reclassify
from pacioli.models import BankFeedAccounts, JournalEntries, JournalEntryEvents, Mappings


@pytest.fixture
def correction(clean_ledger):
    account=db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) "
        "VALUES ('bankacctfrom','Chase Checking') RETURNING id")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account,currency='USD'))
    mapping=Mappings(source='ofx',keyword='test cafe '+uuid4().hex[:12],match_mode='exact',
        positive_credit_subaccount_id='Bookkeeping Suspense',negative_debit_subaccount_id='Bookkeeping Suspense',
        review_required=True,review_note='Original unknown purpose',provenance={'original':'retained'})
    db.session.add(mapping);db.session.flush();mid=mapping.id
    for i,amount in enumerate((Decimal('-25.01'),Decimal('10.00'))):
        fitid=uuid4().hex;when=datetime(2026,1,i+1,12,tzinfo=timezone.utc)
        db.session.execute(text('''INSERT INTO ofx.stmttrn(fitid,acctfrom_id,trntype,dtposted,trnamt,name)
            VALUES(:fitid,:account,'OTHER',:when,:amount,:name)'''),
            dict(fitid=fitid,account=account,when=when,amount=amount,name=mapping.keyword))
        debit,credit=('Bookkeeping Suspense','Chase Checking') if amount<0 else ('Chase Checking','Bookkeeping Suspense')
        post_pair(timestamp=when,debit=debit,credit=credit,functional_amount=abs(amount),
            transaction_source='ofx',transaction_id=source_identity(db.session, account, fitid),
            mapping_id=mid,memo='Original record')
    db.session.commit()
    snap=snapshot_mappings([mid]);db.session.rollback()
    review={'snapshot_sha256':snap['snapshot_sha256'],'reviewer':'Independent test reviewer',
        'authorization':'Test authorization','approvals':[{'mapping_id':mid,'category':'Restaurants',
        'reason':'Reviewed food-service purpose','proposal_sha256':'fixture-proposal'}]}
    yield snap,review
    db.session.remove()
    db.session.execute(text('TRUNCATE bookkeeping.ofx_journal_sources,investments.brokerage_journal_sources,bookkeeping.opening_balances,bookkeeping.transfer_match_legs,'
                            'bookkeeping.journal_entries,bookkeeping.trial_balances,'
        'bookkeeping.journal_entry_events,bookkeeping.closed_periods RESTART IDENTITY'))
    db.session.execute(text('DELETE FROM admin.mappings WHERE id=:id'),{'id':mid})
    db.session.execute(text('DELETE FROM bookkeeping.bank_feed_accounts WHERE ofx_account_id=:id'),{'id':account})
    db.session.execute(text('DELETE FROM ofx.stmttrn WHERE acctfrom_id=:id'),{'id':account})
    db.session.execute(text('DELETE FROM ofx.acctfrom WHERE id=:id'),{'id':account})
    db.session.commit()


def test_preview_commit_and_replay_preserve_cash_and_sources(correction):
    snap,review=correction
    initial=snapshot_mappings([review['approvals'][0]['mapping_id']]);db.session.rollback()
    preview=reclassify(snap,review)
    assert preview['would_change_entries']==2 and preview['changed_entries']==0
    assert snapshot_mappings([review['approvals'][0]['mapping_id']])==initial
    db.session.rollback()
    result=reclassify(snap,review,commit=True)
    assert result['changed_entries']==2 and result['changed_mappings']==1
    entries=db.session.query(JournalEntries).order_by(JournalEntries.id).all()
    assert [(e.debit_subaccount,e.credit_subaccount,e.functional_amount) for e in entries]==[
        ('Restaurants','Chase Checking',Decimal('25.01')),('Chase Checking','Restaurants',Decimal('10.00'))]
    for row,old in zip(entries,snap['journals']):
        assert row.id==old['id'] and row.transaction_id==old['transaction_id']
        assert str(row.timestamp)==old['timestamp'] and str(row.source_amount)==old['source_amount']
    events=db.session.query(JournalEntryEvents).filter_by(action='UPDATE').all()
    assert len(events)==2 and all(e.before_row and e.after_row for e in events)
    assert audit_ledger(db.session)==[]
    count=db.session.query(JournalEntryEvents).count()
    again=reclassify(snap,review,commit=True)
    assert again['changed_entries']==again['changed_mappings']==0
    assert db.session.query(JournalEntryEvents).count()==count


@pytest.mark.parametrize('change',('source','journal','mapping','new_link'))
def test_changed_evidence_cannot_be_overwritten(correction,change):
    snap,review=correction;mid=review['approvals'][0]['mapping_id']
    if change=='source':
        db.session.execute(text("UPDATE ofx.stmttrn SET name='CHANGED PURPOSE' WHERE acctfrom_id=:id"),
            {'id':snap['sources'][0]['account_id']})
    elif change=='journal':db.session.query(JournalEntries).first().memo='Manual review'
    elif change=='mapping':db.session.get(Mappings, mid).review_note='Manual review'
    else:
        post_pair(timestamp=datetime(2026,1,3,tzinfo=timezone.utc),debit='Bookkeeping Suspense',
                  credit='Chase Checking',functional_amount='4',mapping_id=mid)
    db.session.commit()
    with pytest.raises(ValueError,match='changed'):
        reclassify(snap,review,commit=True)


def test_atomic_audit_failure_restores_journals_rules_and_trigger(correction,monkeypatch):
    snap,review=correction;mid=review['approvals'][0]['mapping_id']
    before=snapshot_mappings([mid]);events=db.session.query(JournalEntryEvents).count();db.session.rollback()
    monkeypatch.setattr('pacioli.functions.suspense_posting.audit_ledger',
                        lambda session:[{'kind':'trial_balance'}])
    with pytest.raises(ValueError,match='rolled back'):reclassify(snap,review,commit=True)
    assert snapshot_mappings([mid])==before
    assert db.session.query(JournalEntryEvents).count()==events
    assert db.session.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
        "'bookkeeping.journal_entries'::regclass AND tgname='subaccount_insert_trigger'")).scalar()=='O'


def test_outgoing_salary_and_unreviewed_scope_are_refused(correction):
    snap,review=correction
    wrong=deepcopy(review);wrong['approvals'][0]['category']='Salary'
    with pytest.raises(ValueError,match='outgoing salary'):reclassify(snap,wrong,commit=True)
    wrong=deepcopy(review);wrong['approvals'][0]['mapping_id']=-1
    with pytest.raises(ValueError,match='exactly'):reclassify(snap,wrong,commit=True)


def test_transfer_clearing_stays_provisional(correction):
    snap,review=correction;review['approvals'][0]['category']='Transfer Clearing'
    result=reclassify(snap,review,commit=True)
    assert result['changed_entries']==2
    assert db.session.get(Mappings, review['approvals'][0]['mapping_id']).review_required
    assert audit_ledger(db.session)==[{'kind':'classification_pending','journal_count':2}]


def test_independent_override_retains_original_model_evidence(correction):
    snap,review=correction
    approval=review['approvals'][0]
    approval.update(reviewer_override=True,override_reason='Exact source independently identifies cafe food service.',
                    model_category='Groceries',model_validation_errors=[])
    result=reclassify(snap,review,commit=True)
    assert result['changed_entries']==2
    provenance=db.session.get(Mappings, approval['mapping_id']).provenance['suspense_review']
    assert provenance['reviewer_override'] is True
    assert provenance['model_category']=='Groceries'
    assert provenance['override_reason']==approval['override_reason']
    assert reclassify(snap,review,commit=True)['changed_entries']==0


def test_closed_period_correction_is_refused(correction):
    snap,review=correction
    db.session.execute(text('''INSERT INTO bookkeeping.closed_periods
        (period,closed_at,journal_count,journal_digest,statement_snapshot)
        VALUES ('2026-01',now(),2,'fixture','{}')'''))
    db.session.commit()
    with pytest.raises(ValueError,match='closed period'):reclassify(snap,review,commit=True)
    assert all(j.debit_subaccount=='Bookkeeping Suspense' or j.credit_subaccount=='Bookkeeping Suspense'
               for j in db.session.query(JournalEntries))
