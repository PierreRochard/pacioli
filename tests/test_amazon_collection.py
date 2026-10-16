import json
from pathlib import Path
import subprocess
import pytest
from pacioli.extensions import db
from pacioli.models import AccountingEvidence,AmazonCollectionRuns
from pacioli.functions.amazon_collection import capture,collect,order_id,page_identity
from sqlalchemy import text


def receipt():
    return dict(state='observed',url='https://www.amazon.com/gp/your-account/order-details?orderID=123-1234567-1234567',
        text='Order 123-1234567-1234567\nExample book $20.00\nOrder total $21.65\nPlaced January 2, 2026',links=[])


def test_capture_is_encrypted_idempotent_and_never_posts(clean_ledger):
    first=capture(receipt(),'owner');db.session.commit()
    assert capture(receipt(),'owner').id==first.id
    assert db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence')).scalar().startswith('fernet:')
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries')).scalar()==0
    changed=receipt();changed['text']='Order 999-1234567-1234567 wrong identity but enough text to exceed minimum'
    with pytest.raises(ValueError):capture(changed,'owner')
    with pytest.raises(ValueError):order_id('https://www.amazon.com.evil.example/gp/your-account/order-details?orderID=123-1234567-1234567')


def test_collection_replay_and_interrupted_browser_preserve_receipts(clean_ledger):
    class Bridge:
        def command(self,op,**kwargs):return receipt()
    run=AmazonCollectionRuns(reviewer='owner',progress='start',state='running');db.session.add(run);db.session.commit()
    collect(run.id,Bridge());assert run.state=='complete' and run.documents==1
    collect(run.id,Bridge());assert db.session.query(AccountingEvidence).count()==1
    from pacioli.functions.chase_local_bridge import LocalChaseError
    class Broken:
        def command(self,*a,**k):raise LocalChaseError('select_amazon_orders_tab')
    other=AmazonCollectionRuns(reviewer='owner',progress='start',state='running');db.session.add(other);db.session.commit()
    collect(other.id,Broken());assert other.state=='needs_attention'
    assert 'Extensions menu' in other.progress
    assert db.session.query(AccountingEvidence).count()==1


def test_amazon_extension_origin_and_read_only_guards():
    subprocess.run(['node','--test',str(Path(__file__).with_name('amazon_local_background.test.cjs'))],check=True)


def test_collection_waits_for_requested_document_after_navigation(clean_ledger,monkeypatch):
    import pacioli.functions.amazon_collection as module
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    invoice=receipt()
    history=dict(state='observed',url='https://www.amazon.com/gp/css/order-history?orderFilter=year-2026',
                 text='Your Orders',links=[dict(url=invoice['url'],label='View invoice')])
    class DelayedNavigation:
        def __init__(self):
            self.observations=iter([history,history,dict(state='loading'),invoice,invoice,history])
            self.ops=[]
        def command(self,op,**kwargs):
            self.ops.append(op)
            return next(self.observations) if op=='amazon_observe' else dict(state='loading')
    bridge=DelayedNavigation()
    run=AmazonCollectionRuns(reviewer='owner',progress='start',state='running')
    db.session.add(run);db.session.commit()
    collect(run.id,bridge)
    assert run.state=='complete' and run.documents==1
    assert bridge.ops.count('amazon_navigate')==1 and bridge.ops.count('amazon_back')==1
    assert db.session.query(AccountingEvidence).count()==1
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.journal_entries')).scalar()==0


def test_page_identity_retains_range_and_page():
    base='https://www.amazon.com/gp/css/order-history'
    assert page_identity(base+'?orderFilter=year-2026&ref=first#top')==page_identity(
        base+'/ref=second?ref_=third&orderFilter=year-2026')
    assert page_identity(base+'?startIndex=10')!=page_identity(base+'?startIndex=20')
    assert page_identity(base+'?orderFilter=year-2026')!=page_identity(base+'?orderFilter=year-2025')


def test_collection_post_redirects_so_progress_refresh_cannot_resubmit(clean_ledger,client,admin_user,monkeypatch):
    from pacioli.views import reconciliation_views
    launched=[]
    monkeypatch.setattr(reconciliation_views.subprocess,'Popen',lambda *args,**kwargs:launched.append(args))
    client.post('/login/',data=admin_user)
    response=client.post('/reconciliation/amazon/',data={'action':'collect'})
    assert response.status_code==302
    assert response.headers['Location'].endswith('/reconciliation/amazon/')
    for _ in range(2):
        page=client.get(response.headers['Location'])
        assert page.status_code==200 and b'Reader missing from Firefox?' in page.data
    assert len(launched)==1 and db.session.query(AmazonCollectionRuns).count()==1
