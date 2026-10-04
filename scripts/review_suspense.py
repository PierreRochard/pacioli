#!/usr/bin/env python
"""Encrypted local Qwen second pass over already-booked suspense transactions.

Prepare/classify never change the ledger. Corrections require a separate bounded
review artifact and go through the audited reclassification function.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sqlalchemy import text
from pacioli import create_app
from pacioli.extensions import db
from pacioli.functions.chase_local_model import LocalModel
from pacioli.functions.local_mapping_posting import digest
from pacioli.functions.suspense_advisor import POLICY, PROFILES
from pacioli.functions.suspense_posting import snapshot_mappings, reclassify
from scripts.book_local_mappings import read_encrypted, write_encrypted, read_proposals, merchant_group
from scripts.evaluate_suspense_advisor import infer
from pacioli.secure_fields import encrypt_value


def prepare(directory):
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    app=create_app('pacioli.settings.ProdConfig',env='Prod')
    with app.app_context():
        db.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        ids=db.session.execute(text('''SELECT id FROM admin.mappings
            WHERE source='ofx' AND match_mode='exact' AND review_required
            AND positive_credit_subaccount_id='Bookkeeping Suspense'
            AND negative_debit_subaccount_id='Bookkeeping Suspense' ORDER BY id''')).scalars().all()
        snap=snapshot_mappings(ids)
        journal_counts=defaultdict(int)
        for j in snap['journals']:journal_counts[j['mapping_id']]+=1
        grouped=defaultdict(list)
        for mapping in snap['mappings']:
            grouped[merchant_group(mapping['keyword'])].append(mapping['id'])
        groups=[{'id':digest({'merchant':merchant,'mapping_ids':mids})[:20],
                 'merchant':merchant,'mapping_ids':mids,
                 'transactions':sum(journal_counts[mid] for mid in mids)}
                for merchant,mids in grouped.items()]
        groups.sort(key=lambda g:(-g['transactions'],g['merchant']))
        write_encrypted(directory/'snapshot.enc',snap)
        write_encrypted(directory/'groups.enc',groups)
        db.session.rollback()
    print(json.dumps({'mappings':len(ids),'journals':len(snap['journals']),'groups':len(groups)}))


def group_record(snapshot, group):
    sources={s['id']:s for s in snapshot['sources']}
    wanted=set(group['mapping_ids']);unique={}
    for j in snapshot['journals']:
        if j['mapping_id'] not in wanted:continue
        s=sources[j['transaction_id']]
        sign='credit' if j['credit_subaccount']=='Bookkeeping Suspense' else 'debit'
        key=(s['description'],sign,s['currency'])
        unique[key]={'description':s['description'],'sign':sign,'currency':s['currency']}
    # Include every distinct descriptor/sign/currency. Never silently sample a
    # large mixed group and apply its decision to the unseen members.
    return {'examples':[unique[k] for k in sorted(unique)]}


def validate_review_proposals(snapshot, bounded, review, proposals):
    """Bind approval to the same records actually supplied to the model."""
    if digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})!=snapshot['snapshot_sha256']:
        raise ValueError('Changed proposal source snapshot')
    ids={a['mapping_id'] for a in review['approvals']}
    journals=[j for j in snapshot['journals'] if j['mapping_id'] in ids]
    source_ids={j['transaction_id'] for j in journals}
    expected={'mappings':[m for m in snapshot['mappings'] if m['id'] in ids],
              'journals':journals,'sources':[s for s in snapshot['sources'] if s['id'] in source_ids],
              'chart':snapshot['chart']}
    expected['snapshot_sha256']=digest(expected)
    if bounded!=expected:
        raise ValueError('Bounded review differs from model source evidence')
    by_hash={p['proposal_sha256']:p for p in proposals}
    for approval in review['approvals']:
        p=by_hash.get(approval['proposal_sha256'])
        if (not p or digest({k:v for k,v in p.items() if k!='proposal_sha256'})!=p['proposal_sha256']
            or p['snapshot_sha256']!=snapshot['snapshot_sha256']
            or p['record_sha256']!=digest(group_record(snapshot,p))
            or approval['mapping_id'] not in p['mapping_ids']):
            raise ValueError('Approval is not backed by a valid unchanged model proposal')
        override=approval.get('reviewer_override') is True
        if override:
            if (not isinstance(approval.get('override_reason'),str)
                or len(approval['override_reason'].strip())<20
                or approval.get('model_category')!=p['result']['category']
                or approval.get('model_validation_errors')!=p['result']['validation_errors']):
                raise ValueError('Explicit independent override evidence is required')
        elif p['result']['validation_errors'] or p['result']['category']!=approval['category']:
            raise ValueError('Approval differs from the model; explicit independent override required')


def classify(directory, profile, mode, think, limit, selection):
    snap=read_encrypted(directory/'snapshot.enc');groups=read_encrypted(directory/'groups.enc')
    if digest({k:v for k,v in snap.items() if k!='snapshot_sha256'})!=snap['snapshot_sha256']:
        raise ValueError('Changed snapshot')
    if selection:
        chosen=read_encrypted(selection)
        if chosen['snapshot_sha256']!=snap['snapshot_sha256']:raise ValueError('Selection snapshot mismatch')
        ids=set(chosen['group_ids']);groups=[g for g in groups if g['id'] in ids]
        if len(groups)!=len(ids):raise ValueError('Unknown selected groups')
    name,model_digest,headroom=PROFILES[profile]
    categories=[r['name'] for r in snap['chart'] if r['element']=='Expenses'
                and r['name'] not in ('Amazon Suspense Account','Discretionary Costs')]
    categories+=['Salary','Bookkeeping Suspense','Transfer Clearing']
    settings={'profile':profile,'mode':mode,'think':think,'model_digest':model_digest,'policy_sha256':digest(POLICY)}
    settings_file=directory/'settings.enc'
    if settings_file.exists():
        if read_encrypted(settings_file)!=settings:raise ValueError('Cannot mix inference settings in one run')
    else:write_encrypted(settings_file,settings)
    output=directory/'proposals.jsonl.enc';prior=read_proposals(output)
    completed={p['group_id'] for p in prior}
    if len(completed)!=len(prior) or any(p['snapshot_sha256']!=snap['snapshot_sha256'] for p in prior):
        raise ValueError('Invalid resumed proposals')
    groups=[g for g in groups if g['id'] not in completed]
    if limit:groups=groups[:limit]
    if not groups:return
    with LocalModel(model=name,digest=model_digest,minimum_free_mib=headroom) as model:
        fd=os.open(output,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a') as out:
            for index,group in enumerate(groups,1):
                record=group_record(snap,group)
                # Bound request bytes conservatively to leave room in 8K context
                # for policy and up to 1536 generated tokens. Oversize is explicit.
                if len(json.dumps(record))>8000:
                    result={'category':'Bookkeeping Suspense','answer':None,
                            'validation_errors':['group_needs_smaller_review_scope'], 'model_not_run':True}
                else:
                    result=infer(model,record,profile,mode,think=think,temperature=0.6 if think else 0,
                                 categories=categories)
                proposal=dict(group_id=group['id'],snapshot_sha256=snap['snapshot_sha256'],
                    mapping_ids=group['mapping_ids'],transactions=group['transactions'],
                    settings=settings,result=result,record_sha256=digest(record))
                proposal['proposal_sha256']=digest(proposal)
                out.write(encrypt_value(json.dumps(proposal))+'\n');out.flush();os.fsync(out.fileno())
                print(json.dumps({'completed':index,'selected':len(groups),'transactions':group['transactions'],
                    'category':result['category'],'valid':not result['validation_errors']}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','classify','preview','post'))
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--profile',choices=PROFILES,default='text14')
    parser.add_argument('--mode',choices=('tuned','single','chatml'),default='tuned')
    parser.add_argument('--think',action='store_true')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--selection',type=Path)
    parser.add_argument('--review',type=Path)
    parser.add_argument('--snapshot',type=Path)
    args=parser.parse_args();os.umask(0o077)
    if args.action=='prepare':prepare(args.batch)
    elif args.action=='classify':classify(args.batch,args.profile,args.mode,args.think,args.limit,args.selection)
    else:
        if not args.review or not args.snapshot:parser.error('Independent review and bounded snapshot required')
        review=read_encrypted(args.review);snap=read_encrypted(args.snapshot)
        proposals=read_proposals(args.batch/'proposals.jsonl.enc')
        validate_review_proposals(read_encrypted(args.batch/'snapshot.enc'),snap,review,proposals)
        app=create_app('pacioli.settings.ProdConfig',env='Prod')
        with app.app_context():result=reclassify(snap,review,commit=args.action=='post')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        write_encrypted(args.batch/(args.action+'-'+stamp+'.enc'),result)
        print(json.dumps(result))


if __name__=='__main__':main()
