"""On-demand, read-only receipt capture through the selected Firefox tab."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, parse_qs, parse_qsl
from pacioli.extensions import db
from pacioli.models import AmazonCollectionRuns, AccountingEvidence
from pacioli.functions.reconciliation import dump, evidence
from pacioli.functions.chase_local_bridge import FirefoxBridge, LocalChaseError


def order_id(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='www.amazon.com':
        raise ValueError('Receipt must come from Amazon')
    query=parse_qs(parsed.query)
    value=next((query[k][0] for k in ('orderID','orderId','orderid') if query.get(k)),None)
    if not value or not re.fullmatch(r'\d{3}-\d{7}-\d{7}',value):
        raise ValueError('Receipt link lacks a valid order identity')
    if not re.fullmatch(r'/gp/(?:your-account/order-details(?:\.html)?|css/summary/print\.html)(?:/ref=[A-Za-z0-9_-]+)?/?',parsed.path):
        raise ValueError('Only order details and printable receipts are captured')
    return value


def capture(snapshot, reviewer):
    if snapshot.get('state')!='observed':raise ValueError('Receipt is not ready')
    identifier=order_id(snapshot['url'])
    body=snapshot.get('text','')
    if not isinstance(body,str) or not 50<len(body)<200001 or identifier not in body:
        raise ValueError('Visible receipt must confirm its order identity')
    if re.search(r'enter the characters you see|solve this puzzle|sorry.{0,8}something went wrong',body,re.I):
        raise ValueError('Amazon requires attention in Firefox')
    return evidence('amazon_receipt',dict(order_id=identifier,text=body,
        source='https://www.amazon.com'+urlsplit(snapshot['url']).path),reviewer)


def page_identity(url):
    """Ignore fragments/tracking while retaining history filters and pagination."""
    parsed=urlsplit(url)
    path=re.sub(r'/ref=[A-Za-z0-9_-]+/?$', '', parsed.path).rstrip('/')
    return parsed.scheme,parsed.netloc,path,tuple(sorted(
        (key,value) for key,value in parse_qsl(parsed.query) if key not in ('ref','ref_')))


def collect(run_id, bridge=None):
    run=db.session.get(AmazonCollectionRuns, run_id)
    if not run or run.state!='running':return
    bridge=bridge or FirefoxBridge()
    bridge.path=Path('/run/user')/str(os.getuid())/'pacioli-amazon-extension.sock'
    checkpoint=json.loads(run.checkpoint);seen=set(checkpoint.get('orders',[]))
    pages=set();captured=[]
    def progress(message):
        run.progress=message;run.updated_at=datetime.now(timezone.utc)
        run.checkpoint=dump(dict(orders=sorted(seen),evidence_ids=captured))
        db.session.commit()
    def observe(expected_url=None):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            result=bridge.command('amazon_observe')
            # tabs.update can resolve before Firefox changes tab.status to loading.
            # Never accept the preceding document as the result of a navigation.
            if result.get('state')=='observed' and (expected_url is None or
                    page_identity(result.get('url',''))==page_identity(expected_url)):
                return result
            time.sleep(.25)
        raise ValueError('Amazon page did not finish loading')
    try:
        progress('Reading the selected Amazon orders page')
        current=observe()
        for page in range(10):
            if current['url'] in pages:break
            pages.add(current['url'])
            receipt_links=[];orders_on_page=set()
            for link in current.get('links',[]):
                try:oid=order_id(link['url'])
                except ValueError:continue
                # A printable invoice can be selected directly; otherwise order details preserve evidence.
                if oid not in orders_on_page:
                    orders_on_page.add(oid);receipt_links.append((oid,link['url']))
            if not receipt_links:
                # The user may explicitly select a receipt instead of history.
                try:
                    doc=capture(current,run.reviewer);db.session.commit()
                    captured.append(doc.id);run.documents+=1
                except ValueError:
                    raise ValueError('No recognized order links. Open Your Orders, or an order receipt, and retry')
                break
            for oid,url in receipt_links:
                if oid in seen:continue
                if run.documents>=100:
                    run.state='needs_attention';progress('Captured 100 receipts. Review this batch before continuing');return
                progress('Opening receipt {} on history page {}'.format(run.documents+1,page+1))
                history_url=current['url']
                bridge.command('amazon_navigate',url=url)
                snapshot=observe(url)
                if order_id(snapshot['url'])!=oid:raise ValueError('Amazon opened a different order')
                doc=capture(snapshot,run.reviewer);db.session.commit()
                captured.append(doc.id);seen.add(oid);run.documents+=1
                progress('Saved {} encrypted receipts; returning to order history'.format(run.documents))
                bridge.command('amazon_back');current=observe(history_url)
            next_links=[l for l in current.get('links',[]) if re.fullmatch(r'\s*(?:Next|Next page|Next\s*→)\s*',l.get('label',''),re.I)]
            if len(next_links)!=1:break
            next_url=next_links[0]['url']
            bridge.command('amazon_navigate',url=next_url);current=observe(next_url)
        else:
            run.state='needs_attention';progress('Reached the ten-page batch limit; earlier history remains');return
        run.state='complete';progress('Captured {} receipts from the selected history range. Item/settlement review remains required'.format(run.documents))
    except (ValueError,LocalChaseError,KeyError) as exc:
        db.session.rollback();run=db.session.get(AmazonCollectionRuns, run_id)
        run.state='needs_attention'
        messages={
            'bridge_unavailable':'Connect the Amazon reader in Firefox, then collect again. Saved receipts are retained.',
            'select_amazon_orders_tab':'Open Amazon Your Orders and select Pacioli Local Amazon Reader from Firefox’s Extensions menu.',
            'amazon_sign_in_or_orders_needed':'Sign in to Amazon and open Your Orders in the selected Firefox tab, then collect again.',
            'unobserved_amazon_link':'Amazon changed the available order links. Open Your Orders and collect again.',
            'bridge_connection_lost':'The Firefox reader disconnected. Reconnect it and collect again; saved receipts are retained.',
        }
        run.progress=(messages.get(exc.code,'The Amazon reader needs attention in Firefox. Saved receipts are retained.')
            if isinstance(exc,LocalChaseError) else str(exc) if isinstance(exc,ValueError) else 'Unrecognized Amazon page')[:500]
        run.updated_at=datetime.now(timezone.utc);db.session.commit()
