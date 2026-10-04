"""PostgreSQL controls for evidence, settlement identity, and independent reviews."""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from pacioli.extensions import db
from pacioli.models import (BankFeedAccounts, JournalEntries, JournalEntryEvents, Mappings,
    BankStatements, OpeningBalances, AccountingEvidence, AccountingReviews,
    TransferMatches, ClosedPeriods, AccountingEvaluationCases)
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.source_identity import source_identity
from pacioli.functions import reconciliation as r
from pacioli.functions import amazon_evidence as a
from pacioli.functions import accounting_review as q
from pacioli.functions.accounting_report import statement, source_differences, trial_balance_differences


@pytest.fixture
def bank(clean_ledger):
    ids=[]
    def make(name='Chase Checking'):
        aid=db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) VALUES('bankacctfrom',:name) RETURNING id"),{'name':name}).scalar()
        db.session.add(BankFeedAccounts(ofx_account_id=aid,currency='USD'));db.session.flush()
        ids.append(aid)
        return aid,name
    yield make
    db.session.rollback()
    # clean_ledger truncates the complete source/journal graph together, even for
    # cases that deliberately closed the fixture month. Deleting a referenced
    # source separately must now fail its financial foreign-key protection.


def transaction(acct,amount,description='AMAZON RETAIL',day=2,category='Bookkeeping Suspense',mapping=None):
    aid,name=acct;amount=Decimal(amount);fitid=uuid4().hex
    when=datetime(2026,1,day,12,tzinfo=timezone.utc)
    db.session.execute(text('''INSERT INTO ofx.stmttrn(fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES(:fitid,:aid,'OTHER',:when,:amount,:name)'''),dict(fitid=fitid,aid=aid,when=when,amount=amount,name=description))
    entry=post_pair(timestamp=when,debit=name if amount>0 else category,credit=category if amount>0 else name,
        functional_amount=abs(amount),transaction_source='ofx',transaction_id=source_identity(db.session, aid, fitid),mapping_id=mapping)
    db.session.flush();return entry


def doc(acct,end,opening,closing):
    return r.import_statement(dict(account_id=acct[0],start='2026-01-01',end=end,
        opening=opening,closing=closing,movement=str(Decimal(closing)-Decimal(opening)),
        verification='Fixture verified account and decimal arithmetic'),b'%PDF-1.4 fixture '+end.encode(),'independent reviewer')


def test_statement_opening_boundary_replay_and_encryption(bank,monkeypatch):
    acct=bank();transaction(acct,'-12.40',day=1)
    doc(acct,'2026-01-02','100','87.60');doc(acct,'2026-01-03','100','87.60')
    monkeypatch.setattr(r,'account_ranges',lambda aid: ([(date(2026,1,1),date(2026,1,31))],False))
    preview=r.opening_preview(acct[0],date(2026,1,1))
    row=r.post_opening(acct[0],date(2026,1,1),preview['digest'],'owner')
    db.session.commit()
    assert row.amount==Decimal('100')
    assert db.session.query(JournalEntries).get(row.journal_id).timestamp==datetime(2025,12,31,23,59,59,999999,tzinfo=timezone.utc)
    assert all(c['difference']==0 for c in r.statement_checks())
    assert r.post_opening(acct[0],date(2026,1,1),preview['digest'],'owner').account_id==acct[0]
    db.session.commit();assert db.session.query(JournalEntries).count()==2
    assert db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence LIMIT 1')).scalar().startswith('fernet:')
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE bookkeeping.bank_statements SET closing=999"))
    db.session.rollback()
    assert not source_differences(db.session) and not trial_balance_differences(db.session)


@pytest.mark.parametrize('failure',['gap','stale','disagree','unbooked','closed'])
def test_opening_failure_is_atomic(bank,monkeypatch,failure):
    acct=bank();entry=transaction(acct,'-12.40',day=1)
    doc(acct,'2026-01-02','100','87.60')
    doc(acct,'2026-01-03','100','87.61' if failure=='disagree' else '87.60')
    db.session.commit()
    monkeypatch.setattr(r,'account_ranges',lambda aid: ([(date(2026,1,2 if failure=='gap' else 1),date(2026,1,31))],False))
    if failure in ('gap','disagree'):
        with pytest.raises(ValueError):r.opening_preview(acct[0],date(2026,1,1))
        return
    preview=r.opening_preview(acct[0],date(2026,1,1))
    if failure=='unbooked':db.session.delete(entry)
    if failure=='closed':db.session.add(ClosedPeriods(period='2025-12',journal_count=0,journal_digest='fixture',statement_snapshot={}))
    db.session.commit()
    with pytest.raises(ValueError):r.post_opening(acct[0],date(2026,1,1),'stale' if failure=='stale' else preview['digest'],'owner')
    db.session.rollback();assert db.session.query(OpeningBalances).count()==0
    assert db.session.query(JournalEntries).filter_by(transaction_source='statement_opening').count()==0


@pytest.mark.parametrize('amount',['-100.25','0.00'])
def test_card_opening_sign_zero_and_changed_equity_leg(bank,monkeypatch,amount):
    acct=bank('Credit Card')
    db.session.execute(text('INSERT INTO ofx.ccacctfrom(id,acctid) VALUES(:id,:number)'),{'id':acct[0],'number':uuid4().hex[:12]})
    doc(acct,'2026-01-02',amount,amount)
    monkeypatch.setattr(r,'account_ranges',lambda aid: ([(date(2026,1,1),date(2026,1,31))],False))
    preview=r.opening_preview(acct[0],date(2026,1,1))
    row=r.post_opening(acct[0],date(2026,1,1),preview['digest'],'owner');db.session.commit()
    if Decimal(amount):
        entry=db.session.query(JournalEntries).get(row.journal_id)
        assert entry.debit_subaccount=='Personal Equity' and entry.credit_subaccount=='Credit Card'
        entry.debit_subaccount='Restaurants';db.session.commit()
        assert any(i['kind']=='opening_evidence' for i in r.evidence_differences(db.session))
        with pytest.raises(ValueError,match='changed'):
            r.post_opening(acct[0],date(2026,1,1),preview['digest'],'owner')
        db.session.rollback()
    else:
        assert row.journal_id is None and db.session.query(JournalEntries).count()==0
    db.session.execute(text('DELETE FROM ofx.ccacctfrom WHERE id=:id'),{'id':acct[0]});db.session.commit()


def test_transfer_match_preserves_journals_replay_and_pending(bank):
    left=transaction(bank(),'-50','CARD AUTOPAY',category='Transfer Clearing')
    right=transaction(bank('Chase Savings'),'50','PAYMENT THANK YOU',day=3,category='Transfer Clearing')
    db.session.commit();count=db.session.query(JournalEntryEvents).count()
    pair=r.transfer_candidates()[0]
    assert pair['unique'] and pair['explicit_payment']
    r.match_transfer(left.id,right.id,pair['digest'],'owner','Same owned-account transfer; two bank records')
    db.session.commit()
    assert statement(db.session,'YYYY-MM','2026-01')['suspense_entries']==0
    assert statement(db.session,'YYYY-MM-DD','2026-01-02')['suspense_entries']==1
    r.match_transfer(left.id,right.id,pair['digest'],'owner','Replay');db.session.commit()
    assert db.session.query(TransferMatches).count()==1
    assert db.session.query(JournalEntryEvents).count()==count
    assert db.session.query(JournalEntries).count()==2
    right.functional_amount=Decimal('49');right.source_amount=Decimal('49');db.session.commit()
    assert statement(db.session,'YYYY-MM','2026-01')['suspense_entries']==2
    assert any(i['kind']=='transfer_evidence' for i in r.evidence_differences(db.session))


def test_equal_amount_ambiguous_transfers_not_auto_resolved(bank):
    left=transaction(bank(),'-50','TRANSFER',category='Transfer Clearing')
    second=bank('Chase Savings')
    one=transaction(second,'50','TRANSFER',category='Transfer Clearing')
    two=transaction(second,'50','TRANSFER',category='Transfer Clearing')
    db.session.commit()
    pairs=r.transfer_candidates();assert len(pairs)==2 and not any(p['unique'] for p in pairs)
    r.match_transfer(left.id,one.id,r.digest(r.transfer_pair(left,one)),'owner','Confirmed unique reference externally');db.session.commit()
    with pytest.raises(ValueError,match='already matched'):
        r.match_transfer(left.id,two.id,r.digest(r.transfer_pair(left,two)),'owner','Cannot reuse a leg')
    db.session.rollback()


def csv_bytes(bundle='shipment-1',refund=False):
    if refund:
        return ('bundle_id,line_id,order_id,date,kind,title,amount,currency\n'+bundle+',refund-1,order-1,2026-01-02,refund,Book,-21.65,USD\n').encode()
    return ('bundle_id,line_id,order_id,date,kind,title,amount,currency\n'+
        bundle+',item-1,order-1,2026-01-02,item,Same book,10.00,USD\n'+
        bundle+',item-2,order-1,2026-01-02,item,Same book,10.00,USD\n'+
        bundle+',tax,order-1,2026-01-02,tax,Tax,1.65,USD\n').encode()


@pytest.mark.parametrize('refund',[False,True])
def test_amazon_split_or_refund_preserves_cash_and_source_identity(bank,refund):
    entry=transaction(bank(),'21.65' if refund else '-21.65')
    original_id=entry.id
    bundle=a.import_items_csv(csv_bytes(refund=refund),'reviewer')[0]
    db.session.commit()
    assert len(json.loads(bundle.details)['lines'])==(1 if refund else 3)
    assert a.import_items_csv(csv_bytes(refund=refund),'reviewer')[0].id==bundle.id
    preview=a.allocation_preview(bundle.id,entry.transaction_id,[dict(category='Books and Publications',amount='20'),dict(category='Restaurants',amount='1.65')])
    a.book_allocation(preview,'owner','Fixture allocation of all-in item costs');db.session.commit()
    assert not source_differences(db.session) and not trial_balance_differences(db.session)
    entries=db.session.query(JournalEntries).all()
    assert len(entries)==2 and sum(e.functional_amount for e in entries)==Decimal('21.65')
    assert all((e.debit_subaccount if refund else e.credit_subaccount)=='Chase Checking' for e in entries)
    assert db.session.query(JournalEntryEvents).filter_by(journal_entry_id=original_id,action='DELETE').one().before_row
    count=db.session.query(JournalEntryEvents).count()
    a.book_allocation(preview,'owner','Replay');db.session.commit()
    assert db.session.query(JournalEntryEvents).count()==count
    assert db.session.query(JournalEntries).filter_by(transaction_source='amazon').count()==0


@pytest.mark.parametrize('failure',['total','duplicate','identity','stale','closed'])
def test_amazon_rejects_incomplete_or_conflicting_evidence(bank,failure):
    entry=transaction(bank(),'-21.65')
    bundle=a.import_items_csv(csv_bytes(),'reviewer')[0];db.session.commit()
    if failure=='duplicate':
        with pytest.raises(ValueError):a.import_items_csv(csv_bytes().replace(b'item-2',b'item-1'),'reviewer')
    elif failure=='identity':
        with pytest.raises(ValueError):a.import_items_csv(csv_bytes().replace(b'Same book',b'Changed purpose'),'reviewer')
    elif failure=='total':
        with pytest.raises(ValueError):a.allocation_preview(bundle.id,entry.transaction_id,[dict(category='Restaurants',amount='20')])
    else:
        preview=a.allocation_preview(bundle.id,entry.transaction_id,[dict(category='Books and Publications',amount='21.65')])
        if failure=='stale':
            preview['source']['description']='Tampered'
        else:
            db.session.add(ClosedPeriods(period='2026-01',journal_count=1,journal_digest='fixture',statement_snapshot={}));db.session.commit()
        with pytest.raises(ValueError):a.book_allocation(preview,'owner','Test failure')
    db.session.rollback();assert db.session.query(JournalEntries).count()==1


def test_legacy_amazon_posting_cannot_duplicate_a_reconciled_order(bank,clean_amazon_orders):
    from pacioli.models import AmazonItems,AmazonOrders
    from pacioli.functions.amazon_functions import apply_single_amazon_mapping
    entry=transaction(bank(),'-21.65')
    bundle=a.import_items_csv(csv_bytes(),'reviewer')[0]
    preview=a.allocation_preview(bundle.id,entry.transaction_id,[dict(category='Books and Publications',amount='21.65')])
    a.book_allocation(preview,'owner','Reviewed receipt');db.session.commit()
    db.session.add(AmazonOrders(id='order-1',order_date=date(2026,1,1)));db.session.flush()
    db.session.add(AmazonItems(order_id='order-1',title='duplicate fixture',item_total=Decimal('21.65'),currency='USD',shipment_date=date(2026,1,2)))
    mapping=Mappings(source='amazon',keyword='duplicate fixture',positive_debit_subaccount_id='Books and Publications',positive_credit_subaccount_id='Amazon Payable')
    db.session.add(mapping);db.session.commit()
    with pytest.raises(ValueError,match='duplicate'):apply_single_amazon_mapping(mapping.id)
    assert db.session.query(JournalEntries).count()==1
    db.session.delete(db.session.query(Mappings).get(mapping.id));db.session.commit()


def test_review_fact_model_separation_atomic_approval_and_stale_guard(bank):
    keyword='cafe '+uuid4().hex[:12]
    m=Mappings(source='ofx',keyword=keyword,match_mode='exact',review_required=True,
        positive_credit_subaccount_id='Bookkeeping Suspense',negative_debit_subaccount_id='Bookkeeping Suspense')
    db.session.add(m);db.session.flush()
    entry=transaction(bank(),'-5',keyword,mapping=m.id);db.session.commit()
    row=q.create_review(m.id,'Prepared food cafe','Owner receipt','owner');db.session.commit()
    assert q.create_review(m.id,'Prepared food cafe','Owner receipt','owner').id==row.id
    row.state='queued';db.session.commit()
    seen=[]
    q.run_advisor(row.id,lambda record,categories: seen.append(record) or {'answer':{'category':'Restaurants','evidence':'Cafe','missing':''},'validation_errors':[]})
    assert 'expected' not in seen[0] and seen[0]['verified_merchant_facts'][0]['text']=='Prepared food cafe'
    assert entry.debit_subaccount=='Bookkeeping Suspense'
    q.approve_review(row.id,'Restaurants','Prepared food confirmed by receipt','owner');db.session.commit()
    assert entry.debit_subaccount=='Restaurants' and not m.review_required
    q.approve_review(row.id,'Restaurants','Prepared food confirmed by receipt','owner');db.session.commit()
    assert db.session.query(JournalEntryEvents).filter_by(action='UPDATE').count()==1


def test_gold_merchant_holdout_and_labels_never_sent_to_model(bank):
    first=transaction(bank(),'-5','KNOWN CAFE')
    second=transaction(bank('Chase Savings'),'-10','UNKNOWN PEER PAYMENT')
    q.add_gold_case(first.transaction_id,'Cafe family','development','Restaurants','Explicit prepared-food service','independent reviewer')
    with pytest.raises(ValueError,match='boundary'):
        q.add_gold_case(second.transaction_id,'Cafe family','holdout','Bookkeeping Suspense','Purpose absent','reviewer')
    q.add_gold_case(second.transaction_id,'Peer family','holdout','Bookkeeping Suspense','Purpose absent','independent reviewer')
    db.session.commit()
    from scripts.evaluate_real_accounting import evaluate,regression_passes
    seen=[]
    def predict(record):
        seen.append(record)
        return dict(category='Bookkeeping Suspense',validation_errors=[],elapsed_seconds=1,vram_used_free_mib='10000, 6000')
    result=evaluate(q.export_gold(),predict)
    assert len(seen)==2 and all(set(record)=={'examples'} for record in seen)
    assert result['summary']['holdout']['correct']==1
    assert not regression_passes(result)
    leaked=q.export_gold();leaked[1]['merchant']='cafe family'
    with pytest.raises(ValueError,match='leakage'):evaluate(leaked,predict)


def test_workbench_authentication_pages_and_csrf(clean_ledger,client,admin_user,app):
    assert client.get('/reconciliation/').status_code==302
    client.post('/login/',data=admin_user)
    for path in ('','transfers/','amazon/','reviews/','evaluation/'):
        response=client.get('/reconciliation/'+path)
        assert response.status_code==200,response.data
        assert response.headers['Cache-Control']=='no-store'
    app.config['WTF_CSRF_ENABLED']=True
    try:
        assert client.post('/reconciliation/opening/',data={}).status_code==400
        assert client.post('/reconciliation/amazon/',data={}).status_code==400
        assert client.get('/reconciliation/opening/').status_code==405
    finally:app.config['WTF_CSRF_ENABLED']=False


def test_transfer_review_queue_keeps_unmatched_ambiguous_and_damaged_legs(bank):
    a,b = bank(),bank('Chase Checking - Deposits in Transit')
    unmatched=transaction(a,'-31',description='TRANSFER OTHER BANK',category='Transfer Clearing')
    left=transaction(a,'-50',description='TRANSFER',category='Transfer Clearing')
    right=transaction(b,'50',description='TRANSFER',category='Transfer Clearing')
    alternative=transaction(b,'50',description='TRANSFER',category='Transfer Clearing')
    db.session.commit()
    queue={l['journal_id']:l for l in r.unresolved_transfers()}
    assert len(queue)==4 and queue[left.id]['candidate_count']==2
    assert queue[unmatched.id]['candidate_count']==0
    assert 'No counterpart' in queue[unmatched.id]['status']
    legs=r.transfer_pair(left,right)
    r.match_transfer(left.id,right.id,r.digest(legs),'reviewer','Verified bank reference')
    db.session.commit()
    queue={l['journal_id']:l for l in r.unresolved_transfers()}
    assert set(queue)=={unmatched.id,alternative.id}
    left.timestamp=left.timestamp.replace(day=3)
    db.session.flush()
    queue={l['journal_id']:l for l in r.unresolved_transfers()}
    assert len(queue)==4
    assert 'repair' in queue[left.id]['status']
    assert 'no longer validates' in queue[right.id]['status']
    db.session.rollback()
