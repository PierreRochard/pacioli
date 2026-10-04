#!/usr/bin/env python
"""Frozen real-transaction evaluation. Serial local GPU; no journal writes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pacioli import create_app
from pacioli.extensions import db
from pacioli.functions.accounting_review import export_gold
from pacioli.functions.reconciliation import digest, evidence
from pacioli.functions.suspense_advisor import PROFILES, POLICY
from pacioli.functions.chase_local_model import LocalModel
from scripts.evaluate_suspense_advisor import infer, summarize, CATEGORIES


def regression_passes(result):
    summaries=result['summary']
    return set(summaries)=={'development','holdout'} and all(
        s['cases']>0 and s['correct']==s['cases'] and not s['unsafe_proposals'] and not s['invalid_responses']
        for s in summaries.values())


def evaluate(cases, predict):
    seen={}
    descriptions={}
    for case in cases:
        prior=seen.setdefault(case['merchant'],case['partition'])
        if prior!=case['partition']:
            raise ValueError('Merchant leakage across partitions')
        # Same input under a different merchant alias cannot cross the boundary.
        for example in case['record']['examples']:
            key=example['description'].casefold().strip()
            if descriptions.setdefault(key,case['partition'])!=case['partition']:
                raise ValueError('Duplicate descriptor leakage across partitions')
    rows=[]
    for case in cases:
        # Deliberately pass only source evidence, never gold/evidence/reviewer.
        result=predict(json.loads(json.dumps(case['record'])))
        rows.append(dict(id=case['id'],merchant=case['merchant'],partition=case['partition'],
                         expected=case['expected'],**result))
    summaries={}
    for partition in ('development','holdout'):
        selected=[r for r in rows if r['partition']==partition]
        if selected:
            summaries[partition]=summarize(selected)
            summaries[partition]['unsafe_proposals']=sum(r['category'] not in (None,'Bookkeeping Suspense',r['expected'])
                and not r['validation_errors'] for r in selected)
    return dict(dataset_digest=digest(cases),policy_digest=hashlib.sha256(POLICY.encode()).hexdigest(),
        model=PROFILES['text14'][0],model_digest=PROFILES['text14'][1],
        evaluated_at=datetime.now(timezone.utc).isoformat(),summary=summaries,rows=rows)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true')
    parser.add_argument('--check',action='store_true',help='Return nonzero if either frozen partition regresses or misses a label')
    args=parser.parse_args()
    if not args.run:parser.error('--run explicitly starts the serial GPU evaluation')
    app=create_app('pacioli.settings.ProdConfig',env='Prod')
    with app.app_context():
        cases=export_gold()
        if not cases:raise SystemExit('Label real cases independently before evaluating')
        db.session.rollback()
        name,sha,minimum=PROFILES['text14']
        with LocalModel(model=name,digest=sha,minimum_free_mib=minimum) as model:
            def predict(record):
                result=infer(model,record,'text14','system',think=True,temperature=.6,categories=CATEGORIES)
                print(json.dumps({'completed':predict.count+1,'total':len(cases)}),flush=True)
                predict.count+=1
                return result
            predict.count=0
            result=evaluate(cases,predict)
        evidence('real_evaluation',result,'Independent real-data evaluation runner')
        db.session.commit()
        print(json.dumps(result['summary'],indent=2))
        if args.check and not regression_passes(result):
            raise SystemExit('Frozen real-data regression gate failed; no automated posting is authorized')
