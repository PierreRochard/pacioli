"""Real PostgreSQL checks for bounded, reviewed local-model posting batches."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.accounting_report import audit_ledger
from pacioli.functions.local_mapping_posting import (
    CHART_SQL, SOURCE_SQL, digest, post_reviewed_batch, reviewed_selection,
)
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.source_identity import source_identity
from pacioli.models import BankFeedAccounts, JournalEntries, JournalEntryEvents, Mappings
from pacioli.models import ClosedPeriods


def test_private_artifacts_reject_plaintext_public_files_and_symlinks(tmp_path):
    from scripts.book_local_mappings import read_encrypted,write_encrypted
    artifact = tmp_path/'snapshot.enc'
    value = {'private_description':'Private fixture merchant'}
    write_encrypted(artifact,value)
    assert 'Private fixture merchant' not in artifact.read_text()
    assert read_encrypted(artifact)==value
    with pytest.raises(FileExistsError):write_encrypted(artifact,value)
    artifact.chmod(0o644)
    with pytest.raises(ValueError,match='private'):read_encrypted(artifact)
    artifact.chmod(0o600)
    link = tmp_path/'link.enc';link.symlink_to(artifact)
    with pytest.raises(OSError):read_encrypted(link)
    artifact.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='encrypted'):read_encrypted(artifact)


@pytest.fixture
def batch(clean_ledger):
    prior_mappings = {row.id for row in db.session.query(Mappings)}
    account = db.session.execute(text("INSERT INTO ofx.acctfrom (subclass,name) "
        "VALUES ('bankacctfrom','Chase Checking') RETURNING id")).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account,currency='USD'))
    ids = []
    for index,amount in enumerate(('-25.01','10.00','-12.34')):
        fitid = uuid4().hex
        db.session.execute(text('''INSERT INTO ofx.stmttrn
            (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
            VALUES (:fitid,:account,'OTHER',:date,:amount,'PINE CAFE')'''),
            {'fitid':fitid,'account':account,'date':datetime(2026,1,index+1,12),
             'amount':Decimal(amount)})
        ids.append(source_identity(db.session, account, fitid))
    db.session.commit()
    chart = [dict(row) for row in db.session.execute(text(CHART_SQL))]
    rows = [dict(row) for row in db.session.execute(text(SOURCE_SQL),{'ids':ids})]
    snapshot = json.loads(json.dumps({'rows':rows,'chart':chart,
        'groups':[{'id':'group-1','source_ids':ids,'merchant':'PINE CAFE'}]},default=str))
    snapshot['snapshot_sha256'] = digest(snapshot)
    proposal = {'group_id':'group-1','snapshot_sha256':snapshot['snapshot_sha256'],
                'draft':{'decision':'propose_mapping','keyword':'PINE CAFE','category':'Restaurants',
                         'reason':'Restaurant meals and refund'},'validation_failures':None}
    proposal['proposal_sha256'] = digest(proposal)
    review = {'snapshot_sha256':snapshot['snapshot_sha256'],'reviewer':'test reviewer',
              'authorization':'Test authorization','approvals':[{
                  'group_id':'group-1','proposal_sha256':proposal['proposal_sha256'],
                  'category':'Restaurants','reason':'Reviewed restaurant purpose',
                  'source_ids':ids[:2]}]}
    db.session.rollback()
    yield snapshot,[proposal],review,account
    db.session.remove()
    db.session.execute(text('TRUNCATE bookkeeping.ofx_journal_sources,investments.brokerage_journal_sources,bookkeeping.opening_balances,bookkeeping.transfer_match_legs,'
                            'bookkeeping.journal_entries,bookkeeping.trial_balances,'
                            'bookkeeping.journal_entry_events,bookkeeping.closed_periods RESTART IDENTITY'))
    for mapping in db.session.query(Mappings).all():
        if mapping.id not in prior_mappings:
            db.session.delete(mapping)
    db.session.execute(text('DELETE FROM bookkeeping.bank_feed_accounts WHERE ofx_account_id=:id'),{'id':account})
    db.session.execute(text('DELETE FROM ofx.stmttrn WHERE acctfrom_id=:id'),{'id':account})
    db.session.execute(text('DELETE FROM ofx.acctfrom WHERE id=:id'),{'id':account})
    db.session.commit()


def test_preview_posts_nothing_and_commit_is_scoped_audited_and_idempotent(batch):
    snapshot,proposals,review,_ = batch
    mapping_count = db.session.query(Mappings).count()
    preview = post_reviewed_batch(snapshot,proposals,review)
    assert preview['would_post']==2 and preview['posted']==0
    assert db.session.query(JournalEntries).count()==0
    first = post_reviewed_batch(snapshot,proposals,review,commit=True)
    assert first['posted']==2 and first['gross_amount']=='35.01'
    entries = db.session.query(JournalEntries).order_by(JournalEntries.timestamp).all()
    assert (entries[0].debit_subaccount,entries[0].credit_subaccount,entries[0].functional_amount)==(
        'Restaurants','Chase Checking',Decimal('25.01'))
    assert (entries[1].debit_subaccount,entries[1].credit_subaccount,entries[1].functional_amount)==(
        'Chase Checking','Restaurants',Decimal('10.00'))
    assert all(first['batch_sha256'] in entry.memo for entry in entries)
    assert {entry.actor for entry in db.session.query(JournalEntryEvents)}=={'test reviewer'}
    assert db.session.query(Mappings).count()==mapping_count+1
    mapping = db.session.query(Mappings).filter_by(keyword='pine cafe', source='ofx').one()
    assert mapping.match_mode=='exact' and not mapping.review_required
    assert {entry.mapping_id for entry in entries}=={mapping.id}
    assert_unselected_source_is_reported(snapshot)
    assert first['audit_issue_counts'] == {'source_amount': 1}
    # Same merchant, but the third source was not explicitly selected.
    assert db.session.query(JournalEntries).filter_by(transaction_id=snapshot['rows'][2]['id']).count()==0
    repeated = post_reviewed_batch(snapshot,proposals,review,commit=True)
    assert repeated['posted']==0 and repeated['already_posted']==2
    assert db.session.query(JournalEntryEvents).count()==2


def assert_unselected_source_is_reported(snapshot, additional=None):
    """A successful bounded batch must not pretend its backlog was booked."""
    expected = {'kind': 'source_amount', 'source': 'ofx',
                'problem': 'unposted_source',
                'transaction_id': snapshot['rows'][2]['id'], 'booked': Decimal('0'),
                'source_amount': Decimal('12.34')}
    assert audit_ledger(db.session) == (additional or []) + [expected]


@pytest.mark.parametrize('defect', ['partial_allocation', 'wrong_bank_direction'])
def test_unselected_malformed_allocation_blocks_and_rolls_back_reviewed_batch(batch, defect):
    snapshot, proposals, review, _ = batch
    unselected = snapshot['rows'][2]
    when = datetime.fromisoformat(unselected['date']).replace(tzinfo=timezone.utc)
    entry = post_pair(timestamp=when, debit='Groceries', credit='Chase Checking',
        functional_amount='1.00' if defect == 'partial_allocation' else '12.34',
        transaction_source='ofx', transaction_id=unselected['id'])
    db.session.flush()
    if defect == 'wrong_bank_direction':
        # Bypass the service to exercise the independent audit of an unrelated
        # preexisting journal; normal posting now refuses this direction.
        entry.debit_subaccount = 'Chase Checking'
        entry.credit_subaccount = 'Groceries'
    db.session.commit()
    existing_id = entry.id
    with pytest.raises(ValueError, match='reconciliation failed'):
        post_reviewed_batch(snapshot, proposals, review, commit=True)
    assert [entry.id for entry in db.session.query(JournalEntries)] == [existing_id]


def test_selected_unposted_sources_are_never_exempted_from_batch_validation(batch):
    from pacioli.functions.local_mapping_posting import blocking_batch_issues
    snapshot, _, review, _ = batch
    selected = {('ofx', identifier) for identifier in review['approvals'][0]['source_ids']}
    blocked = blocking_batch_issues(db.session, audit_ledger(db.session), selected)
    assert {(issue['source'], issue['transaction_id']) for issue in blocked} == selected


@pytest.mark.parametrize('change', ['amount','description','account','currency','chart'])
def test_changed_source_or_chart_requires_new_review(batch,change):
    snapshot,proposals,review,account = batch
    if change=='amount':
        db.session.execute(text('UPDATE ofx.stmttrn SET trnamt=trnamt-1 WHERE acctfrom_id=:id'),{'id':account})
    elif change=='description':
        db.session.execute(text("UPDATE ofx.stmttrn SET name='UNKNOWN SHOP' WHERE acctfrom_id=:id"),{'id':account})
    elif change=='account':
        db.session.execute(text("UPDATE ofx.acctfrom SET name='Chase Savings' WHERE id=:id"),{'id':account})
    elif change=='currency':
        db.session.execute(text("UPDATE bookkeeping.bank_feed_accounts SET currency='EUR' WHERE ofx_account_id=:id"),{'id':account})
    else:
        db.session.execute(text("UPDATE bookkeeping.subaccounts SET parent='Entertainment Expense' WHERE name='Restaurants'"))
    db.session.commit()
    try:
        with pytest.raises(ValueError,match='changed'):
            post_reviewed_batch(snapshot,proposals,review,commit=True)
        assert db.session.query(JournalEntries).count()==0
    finally:
        if change=='chart':
            db.session.execute(text("UPDATE bookkeeping.subaccounts SET parent='Restaurant Expense' WHERE name='Restaurants'"))
            db.session.commit()


def test_existing_conflicting_posting_blocks_entire_batch(batch):
    snapshot,proposals,review,_ = batch
    post_pair(timestamp=datetime(2026,1,2,12,tzinfo=timezone.utc),debit='Chase Checking',
              credit='Salary',functional_amount='10.00',transaction_source='ofx',
              transaction_id=snapshot['rows'][1]['id'])
    db.session.commit()
    with pytest.raises(ValueError,match='different posting'):
        post_reviewed_batch(snapshot,proposals,review,commit=True)
    assert db.session.query(JournalEntries).count()==1


def test_ledger_failure_rolls_back_journals_balances_and_audit_events(batch,monkeypatch):
    snapshot,proposals,review,_ = batch
    mapping_count = db.session.query(Mappings).count()
    monkeypatch.setattr('pacioli.functions.local_mapping_posting.audit_ledger',
                        lambda session:[{'kind':'source_amount'}])
    with pytest.raises(ValueError,match='reconciliation failed'):
        post_reviewed_batch(snapshot,proposals,review,commit=True)
    assert db.session.query(JournalEntries).count()==0
    assert db.session.query(JournalEntryEvents).count()==0
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.trial_balances')).scalar()==0
    assert db.session.query(Mappings).count()==mapping_count


@pytest.mark.parametrize('change', ['snapshot','proposal','category','decision','scope','overlap','reviewer'])
def test_review_cannot_be_stale_expand_scope_or_self_approve(batch,change):
    snapshot,proposals,review,_ = deepcopy(batch)
    if change=='snapshot':snapshot['rows'][0]['amount']='-1'
    elif change=='proposal':proposals[0]['draft']['keyword']='CAFE'
    elif change=='category':review['approvals'][0]['category']='Salary'
    elif change=='decision':
        proposals[0]['draft']['decision']='needs_review'
        proposals[0]['proposal_sha256']=digest({k:v for k,v in proposals[0].items() if k!='proposal_sha256'})
        review['approvals'][0]['proposal_sha256']=proposals[0]['proposal_sha256']
    elif change=='scope':review['approvals'][0]['source_ids'].append('outside-source')
    elif change=='overlap':review['approvals'].append(deepcopy(review['approvals'][0]))
    else:review['reviewer']=''
    with pytest.raises(ValueError):reviewed_selection(snapshot,proposals,review)


def test_outgoing_payroll_cannot_be_posted_as_salary(batch):
    snapshot,proposals,review,_ = batch
    proposals[0]['draft']['category']='Salary'
    proposals[0]['proposal_sha256']=digest({k:v for k,v in proposals[0].items() if k!='proposal_sha256'})
    review['approvals'][0].update(category='Salary',proposal_sha256=proposals[0]['proposal_sha256'])
    with pytest.raises(ValueError,match='Outgoing payroll'):
        post_reviewed_batch(snapshot,proposals,review,commit=True)


def test_closed_period_blocks_new_postings(batch):
    snapshot,proposals,review,_ = batch
    db.session.add(ClosedPeriods(period='2026-01',journal_count=0,journal_digest='test-close',
                                 statement_snapshot={}))
    db.session.commit()
    with pytest.raises(ValueError,match='closed period'):
        post_reviewed_batch(snapshot,proposals,review,commit=True)
    assert db.session.query(JournalEntries).count()==0


def test_two_concurrent_replays_create_only_one_set_of_postings(batch,app):
    from threading import Barrier,Thread

    snapshot,proposals,review,_ = batch
    ready = Barrier(2)
    results = [];errors = []

    def worker():
        with app.app_context():
            try:
                ready.wait(timeout=10)
                results.append(post_reviewed_batch(snapshot,proposals,review,commit=True))
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                db.session.remove()

    workers = [Thread(target=worker) for _ in range(2)]
    for worker_thread in workers:worker_thread.start()
    for worker_thread in workers:worker_thread.join(timeout=20)
    assert not any(worker_thread.is_alive() for worker_thread in workers)
    assert errors==[]
    assert sorted(result['posted'] for result in results)==[0,2]
    db.session.expire_all()
    assert db.session.query(JournalEntries).count()==2
    assert db.session.query(JournalEntryEvents).count()==2


def test_backfill_links_existing_journals_without_changing_money(batch):
    snapshot,proposals,review,_ = batch
    first=post_reviewed_batch(snapshot,proposals,review,commit=True)
    original=[(e.id,e.timestamp,e.functional_amount,e.debit_subaccount,e.credit_subaccount)
              for e in db.session.query(JournalEntries).order_by(JournalEntries.id)]
    db.session.query(JournalEntries).update({'mapping_id':None})
    db.session.commit()
    before=db.session.query(JournalEntryEvents).count()
    result=post_reviewed_batch(snapshot,proposals,review,commit=True,bulk=True)
    assert result['posted']==0 and result['linked_existing']==2 and result['new_mappings']==0
    assert [(e.id,e.timestamp,e.functional_amount,e.debit_subaccount,e.credit_subaccount)
            for e in db.session.query(JournalEntries).order_by(JournalEntries.id)]==original
    assert db.session.query(JournalEntryEvents).count()==before+2
    assert all(e.mapping_id for e in db.session.query(JournalEntries))
    balance_ids=list(db.session.execute(text('SELECT id FROM bookkeeping.trial_balances ORDER BY id')))
    repeated=post_reviewed_batch(snapshot,proposals,review,commit=True,bulk=True)
    assert repeated['linked_existing']==repeated['posted']==repeated['new_mappings']==0
    assert list(db.session.execute(text('SELECT id FROM bookkeeping.trial_balances ORDER BY id')))==balance_ids
    assert_unselected_source_is_reported(snapshot)


def test_exact_rule_reuses_future_description_without_prefix_collision(batch):
    from pacioli.functions.ofx_functions import apply_single_ofx_mapping
    snapshot,proposals,review,account=batch
    post_reviewed_batch(snapshot,proposals,review,commit=True)
    mapping=db.session.query(Mappings).filter_by(source='ofx',keyword='pine cafe').one()
    fitid=uuid4().hex
    db.session.execute(text('''INSERT INTO ofx.stmttrn
        (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES (:fitid,:account,'OTHER','2026-01-04 12:00:00',-99,'PINE CAFETERIA')'''),
        {'fitid':fitid,'account':account})
    db.session.commit()
    apply_single_ofx_mapping(mapping.id)
    assert db.session.query(JournalEntries).count()==3
    assert {e.mapping_id for e in db.session.query(JournalEntries)}=={mapping.id}
    assert db.session.query(JournalEntries).filter_by(transaction_id=source_identity(db.session, account, fitid)).count()==0
    apply_single_ofx_mapping(mapping.id)
    assert db.session.query(JournalEntries).count()==3


def test_bulk_backdated_posting_rebuilds_balances_and_keeps_audit_active(batch):
    snapshot,proposals,review,_=batch
    post_pair(timestamp=datetime(2025,1,1,tzinfo=timezone.utc),debit='Chase Checking',
              credit='Salary',functional_amount='100.00')
    post_pair(timestamp=datetime(2027,6,1,tzinfo=timezone.utc),debit='Rent',
              credit='Chase Checking',functional_amount='10.00')
    db.session.commit()
    result=post_reviewed_batch(snapshot,proposals,review,commit=True,bulk=True)
    assert result['posted']==2
    assert db.session.query(JournalEntryEvents).count()==4
    assert_unselected_source_is_reported(snapshot)
    assert db.session.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
        "'bookkeeping.journal_entries'::regclass AND tgname='subaccount_insert_trigger'")).scalar()=='O'


def test_bulk_failure_restores_trigger_mappings_journals_and_balances(batch,monkeypatch):
    snapshot,proposals,review,_=batch
    count=db.session.query(Mappings).count()
    monkeypatch.setattr('pacioli.functions.local_mapping_posting.audit_ledger',
                        lambda session:[{'kind':'trial_balance'}])
    with pytest.raises(ValueError,match='reconciliation failed'):
        post_reviewed_batch(snapshot,proposals,review,commit=True,bulk=True)
    assert db.session.query(Mappings).count()==count
    assert db.session.query(JournalEntries).count()==0
    assert db.session.query(JournalEntryEvents).count()==0
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.trial_balances')).scalar()==0
    assert db.session.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
        "'bookkeeping.journal_entries'::regclass AND tgname='subaccount_insert_trigger'")).scalar()=='O'


def test_conflicting_persisted_mapping_rolls_back_batch(batch):
    snapshot,proposals,review,_=batch
    db.session.add(Mappings(source='ofx',keyword='pine cafe',match_mode='exact',
                           positive_credit_subaccount_id='Groceries',negative_debit_subaccount_id='Groceries'))
    db.session.commit()
    with pytest.raises(ValueError,match='mapping conflicts'):
        post_reviewed_batch(snapshot,proposals,review,commit=True)
    assert db.session.query(JournalEntries).count()==0


def test_suspense_requires_owner_policy_and_does_not_create_income_or_expense(batch,client,admin_user):
    from pacioli.functions.accounting_report import statement
    snapshot,proposals,review,_=batch
    proposals[0]['draft']['category']='Bookkeeping Suspense'
    proposals[0]['proposal_sha256']=digest({k:v for k,v in proposals[0].items() if k!='proposal_sha256'})
    review['approvals'][0].update(category='Bookkeeping Suspense',proposal_sha256=proposals[0]['proposal_sha256'])
    with pytest.raises(ValueError,match='owner policy'):
        reviewed_selection(snapshot,proposals,review)
    snapshot['suspense_authorized']=True
    snapshot['snapshot_sha256']=digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
    proposals[0]['snapshot_sha256']=snapshot['snapshot_sha256']
    proposals[0]['proposal_sha256']=digest({k:v for k,v in proposals[0].items() if k!='proposal_sha256'})
    review['snapshot_sha256']=snapshot['snapshot_sha256']
    review['approvals'][0]['proposal_sha256']=proposals[0]['proposal_sha256']
    post_reviewed_batch(snapshot,proposals,review,commit=True,bulk=True)
    report=statement(db.session,'YYYY-MM','2026-01')
    assert report['current_income']==0 and report['equation_difference']==0
    assert report['suspense_entries']==2 and report['suspense_balance']==Decimal('15.01')
    assert_unselected_source_is_reported(snapshot, [{'kind':'classification_pending','journal_count':2}])
    assert db.session.query(Mappings).filter_by(keyword='pine cafe').one().review_required
    client.post('/login/',data=admin_user)
    page=client.get('/balance-sheet/YYYY-MM/2026-01/')
    assert page.status_code==200 and b'Provisional statement' in page.data
    page=client.get('/mappings/')
    assert page.status_code==200 and b'Bookkeeping Suspense' in page.data and b'Needs review' in page.data
    mapping=db.session.query(Mappings).filter_by(keyword='pine cafe').one()
    page=client.get('/mappings/details/',query_string={'id':mapping.id})
    assert page.status_code==200 and b'proposal_sha256' in page.data


def test_exact_descriptions_treat_sql_wildcards_as_literal_text(batch):
    from pacioli.functions.ofx_functions import apply_single_ofx_mapping
    snapshot,proposals,review,account=batch
    description='SHOP_100% SPECIAL'
    db.session.execute(text('UPDATE ofx.stmttrn SET name=:name WHERE acctfrom_id=:id'),
                       {'name':description,'id':account})
    db.session.commit()
    for row in snapshot['rows']:row['description']=description
    snapshot['mapping_match_mode']='exact'
    snapshot['snapshot_sha256']=digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
    proposals[0]['snapshot_sha256']=snapshot['snapshot_sha256']
    proposals[0]['draft']['keyword']=description
    proposals[0]['proposal_sha256']=digest({k:v for k,v in proposals[0].items() if k!='proposal_sha256'})
    review['snapshot_sha256']=snapshot['snapshot_sha256']
    review['approvals'][0]['proposal_sha256']=proposals[0]['proposal_sha256']
    post_reviewed_batch(snapshot,proposals,review,commit=True)
    mapping=db.session.query(Mappings).filter_by(keyword=description.lower()).one()
    fitid=uuid4().hex
    db.session.execute(text('''INSERT INTO ofx.stmttrn (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES (:fitid,:account,'OTHER','2026-01-04 12:00:00',-99,'SHOPX100 EXTRA SPECIAL')'''),
        {'fitid':fitid,'account':account})
    db.session.commit()
    apply_single_ofx_mapping(mapping.id)
    assert db.session.query(JournalEntries).count()==3
    assert db.session.query(JournalEntries).filter_by(transaction_id=source_identity(db.session, account, fitid)).count()==0
