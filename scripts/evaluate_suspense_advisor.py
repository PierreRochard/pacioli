#!/usr/bin/env python
"""Serial local-GPU comparison. Gold labels are never sent to inference."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pacioli.functions.chase_local_model import LocalModel
from pacioli.secure_fields import encrypt_value
from pacioli.functions.suspense_advisor import PROFILES, POLICY, messages, schema, validate
from scripts.book_local_mappings import read_encrypted
from scripts.classify_remaining_mappings import POLICY as BASELINE

# A fixed public chart makes development/holdout comparisons reproducible and
# avoids handing a live DB connection to the benchmark.
CATEGORIES=['Air Travel','Bank Fees','Books and Publications','Cell Phone',
 'Charitable Contributions','Clothing','Domain Names','Education','Electricity',
 'Entertainment','Fitness','Gas','Groceries','Home Maintenance','Household Services',
 'Interest','Internet','Lodging','Medical Insurance','Medical Out of Pocket',
 'Parking','Personal Care','Public transportation','Rent','Restaurants','Ride Services','Salary',
 'Software Subscriptions','Vehicle Fuel','Vehicle Maintenance','Waste Disposal',
 'Water and Sewer','Web Hosting','Bookkeeping Suspense','Transfer Clearing']
MAX_RECORD_BYTES=8000
MAX_MESSAGE_BYTES=20000


def hash_json(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def preflight_record(record):
    """Reject invalid or oversized source evidence without dropping examples.

    These serialized UTF-8 byte budgets are conservative operational limits,
    not exact tokenizer proofs. Reported token counts are checked separately.
    """
    if (not isinstance(record,dict) or not isinstance(record.get('examples'),list)
        or not record['examples'] or any(
            not isinstance(example,dict)
            or not isinstance(example.get('description'),str) or not example['description'].strip()
            or not isinstance(example.get('sign'),str) or example['sign'] not in ('credit','debit')
            or not isinstance(example.get('currency'),str) or not example['currency'].strip()
            for example in record['examples'])):
        raise ValueError('invalid_source_examples')
    try:
        record_bytes=len(json.dumps(record).encode('utf-8'))
    except (TypeError,ValueError):
        raise ValueError('invalid_source_serialization') from None
    if record_bytes>MAX_RECORD_BYTES:
        raise ValueError('source_record_exceeds_byte_budget')


def context_errors(response, options):
    """Reject reported context overflows; absent metrics remain unknown."""
    counts={key:response.get(key) for key in ('prompt_eval_count','eval_count')}
    if any(value is not None and (type(value) is not int or value<0) for value in counts.values()):
        return ['invalid_context_metrics']
    prompt,generated=counts['prompt_eval_count'],counts['eval_count']
    errors=[]
    if prompt is not None and prompt>options['num_ctx']-options['num_predict']:
        errors.append('prompt_exceeds_context_budget')
    if prompt is not None and generated is not None and prompt+generated>options['num_ctx']:
        errors.append('generation_exceeds_context_budget')
    return errors


def request_for(record, profile, mode, *, think=False, temperature=0, categories=None):
    preflight_record(record)
    categories=CATEGORIES if categories is None else categories
    model=PROFILES[profile][0]
    options={'temperature':temperature,'seed':42,'num_ctx':8192,
             'num_predict':1536 if think else 256,'num_gpu':99,
             'num_thread':6,'top_k':20,'top_p':0.95 if think else 0.8,'min_p':0}
    if mode=='baseline':
        groups=[dict(index=0,merchant=record['examples'][0]['description'],count=len(record['examples']),
                     examples=[{k:v for k,v in e.items() if k in ('description','sign')} for e in record['examples']])]
        prompt=BASELINE+'\nCategories: '+json.dumps(categories)+'\nGroups: '+json.dumps(groups)
        prompt+=('\nOUTPUT PROTOCOL: Return ONLY {"categories":[category_for_index_0,...]}.'
                 ' Exactly one category string per group in index order. No reasons, explanations'
                 ' or other fields. Outgoing PAYROLL is Bookkeeping Suspense, never Salary.')
        msg=[{'role':'user','content':prompt.replace('<','\\u003c').replace('>','\\u003e')}]
        fmt={'type':'object','properties':{'categories':{'type':'array','minItems':1,'maxItems':1,
             'items':{'type':'string','enum':categories}}},'required':['categories'],'additionalProperties':False}
    else:
        msg=messages(record,categories,chatml=mode=='chatml')
        if mode=='single':
            msg=[{'role':'user','content':msg[0]['content']+'\nINPUT DATA:\n'+msg[1]['content']}]
        fmt=schema(categories)
    if len(json.dumps(msg).encode('utf-8'))>MAX_MESSAGE_BYTES:
        raise ValueError('messages_exceed_byte_budget')
    req={'model':model,'messages':msg,'format':fmt,'stream':True,'keep_alive':120,'options':options}
    if profile!='vision8': req['think']=think
    return req


def infer(model, record, profile, mode, *, think=False, temperature=0, categories=None):
    categories=CATEGORIES if categories is None else categories
    req=request_for(record,profile,mode,think=think,temperature=temperature,categories=categories)
    model.resources();model.loaded=True
    started=time.monotonic()
    response=model.api('chat',req)
    elapsed=time.monotonic()-started
    errors=[];answer=None;category=None
    try:
        answer=json.loads(response['message']['content'])
        if response.get('done_reason')=='length': raise ValueError('truncated')
        if mode=='baseline':
            if (not isinstance(answer,dict) or set(answer)!={'categories'}
                or not isinstance(answer['categories'],list) or len(answer['categories'])!=1):
                raise ValueError('shape')
            category=answer['categories'][0]
            if not isinstance(category,str) or category not in categories: raise ValueError('category')
            # Apply the same source/currency/sign/allocation guards without
            # changing the retained category-only response or claiming that
            # the model generated an explanation. This adapter is validator-only.
            errors=validate({'category':category,
                'evidence':'Baseline category-only suggestion; no explanation was generated.',
                'missing':'Baseline retained suspense; no missing fact was generated.'
                    if category=='Bookkeeping Suspense' else ''},record,categories)
        else:
            errors=validate(answer,record,categories)
            category=answer.get('category') if isinstance(answer,dict) else None
    except (KeyError,TypeError,ValueError): errors.append('invalid_or_truncated_response')
    errors.extend(context_errors(response,req['options']))
    loaded=model.api('ps').get('models',[])
    resident=next((m for m in loaded if m.get('name')==req['model']),None)
    if (resident is None or resident.get('digest')!=PROFILES[profile][1]
        or not resident.get('size') or resident.get('size_vram',0)<resident['size']):
        raise ValueError('Pinned accounting model must remain fully resident on the GPU')
    vram=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,memory.free',
        '--format=csv,noheader,nounits'],text=True,timeout=5).strip()
    model.resources()  # Refuse to continue if desktop headroom dropped below 2 GiB.
    return {'answer':answer,'category':category,'validation_errors':errors,
            'raw_content':response.get('message',{}).get('content',''),
            'request_sha256':hash_json(req),'elapsed_seconds':elapsed,
            'metrics':{k:response.get(k) for k in ('prompt_eval_count','eval_count','eval_duration',
                'prompt_eval_duration','load_duration','done_reason')},
            'loaded_models':loaded,'vram_used_free_mib':vram}


def summarize(rows):
    usable=[r for r in rows if r['expected']!='Bookkeeping Suspense']
    clearing=[r for r in usable if r['expected']=='Transfer Clearing']
    expense_income=[r for r in usable if r['expected']!='Transfer Clearing']
    proposals=[r for r in rows if r['category'] not in (None,'Bookkeeping Suspense') and not r['validation_errors']]
    correct=lambda r:not r['validation_errors'] and r['category']==r['expected']
    wrong=lambda r:r['category']!=r['expected']
    metrics={'cases':len(rows),'correct':sum(map(correct,rows)),
            'raw_category_correct':sum(r['category']==r['expected'] for r in rows),
            'supported_cases':len(usable),'supported_recovered':sum(map(correct,usable)),
            'expense_income_supported':len(expense_income),
            'expense_income_recovered':sum(map(correct,expense_income)),
            'clearing_supported':len(clearing),'clearing_recovered':sum(map(correct,clearing)),
            'proposals':len(proposals),'correct_proposals':sum(map(correct,proposals)),
            # Accepted non-hold mismatches include unsafe proposals on hold cases.
            'wrong_category_proposals':sum(map(wrong,proposals)),
            'unsafe_proposals':sum(r['expected']=='Bookkeeping Suspense' for r in proposals),
            'raw_unsafe_proposals':sum(r['expected']=='Bookkeeping Suspense'
                and r['category'] not in (None,'Bookkeeping Suspense') for r in rows),
            'invalid_responses':sum(bool(r['validation_errors']) for r in rows),
            'median_seconds':statistics.median(r['elapsed_seconds'] for r in rows) if rows else None,
            'max_observed_used_mib':max((int(r['vram_used_free_mib'].split(',')[0]) for r in rows),default=None)}
    if any('transactions' in row for row in rows):
        weight=lambda selected:sum(r.get('transactions',1) for r in selected)
        metrics['transaction_weighted']={
            'scope':'reviewed_expectations_not_live_bookings',
            'missing_transaction_count_weight':1,
            'cases_with_transaction_counts':sum('transactions' in r for r in rows),
            'transactions':weight(rows),
            'correct':weight(r for r in rows if correct(r)),
            'supported':weight(usable),'supported_recovered':weight(r for r in usable if correct(r)),
            'expense_income_supported':weight(expense_income),
            'expense_income_recovered':weight(r for r in expense_income if correct(r)),
            'clearing_supported':weight(clearing),
            'clearing_recovered':weight(r for r in clearing if correct(r)),
            'proposals':weight(proposals),
            'correct_proposals':weight(r for r in proposals if correct(r)),
            'wrong_category_proposals':weight(r for r in proposals if wrong(r)),
            'unsafe_proposals':weight(r for r in proposals if r['expected']=='Bookkeeping Suspense'),
            'invalid_responses':weight(r for r in rows if r['validation_errors']),
        }
    return metrics


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',choices=PROFILES,default='text14')
    parser.add_argument('--mode',choices=('baseline','tuned','chatml','single'),default='tuned')
    parser.add_argument('--think',action='store_true')
    parser.add_argument('--temperature',type=float,default=0)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--private',action='store_true',
                        help='Read an owner-only encrypted corpus and write an encrypted report; print aggregate progress only')
    args=parser.parse_args(argv);os.umask(0o077)
    corpus=read_encrypted(args.cases) if args.private else json.loads(args.cases.read_text())
    categories=corpus.get('categories',CATEGORIES) if isinstance(corpus,dict) else CATEGORIES
    cases=corpus.get('cases') if isinstance(corpus,dict) else corpus
    if (not isinstance(categories,list) or not categories
        or any(not isinstance(category,str) or not category for category in categories)
        or len(set(categories))!=len(categories) or 'Bookkeeping Suspense' not in categories):
        raise ValueError('Corpus categories must be unique nonempty strings including Bookkeeping Suspense')
    if not isinstance(cases,list) or not cases:
        raise ValueError('Corpus must contain a nonempty cases list')
    if args.limit is not None:
        if args.limit<1:raise ValueError('Limit must be positive')
        cases=cases[:args.limit]
    for item in cases:
        if (not isinstance(item,dict) or not isinstance(item.get('input'),dict)
            or 'id' not in item or item.get('expected') not in categories):
            raise ValueError('Every case must have id, input, and an expected category from the chart')
        if 'transactions' in item and (type(item['transactions']) is not int or item['transactions']<1):
            raise ValueError('Case transaction counts must be positive integers')
        # Preflight the complete selected corpus before opening a GPU session.
        request_for(item['input'],args.profile,args.mode,think=args.think,
                    temperature=args.temperature,categories=categories)
    name,digest,headroom=PROFILES[args.profile]
    meta={'started_at':datetime.now(timezone.utc).isoformat(),'profile':args.profile,'model':name,
          'model_digest':digest,'mode':args.mode,'think':args.think,'temperature':args.temperature,
          'private':args.private,'planned_cases':len(cases),'categories':categories,
          'evaluation_scope':'reviewed_expectations_not_live_bookings',
          'metric_definitions':{
              'wrong_category_proposals':'Validated non-hold proposals whose category differs from expected; includes unsafe_proposals.',
              'expense_income_supported':'Expected category is neither Bookkeeping Suspense nor Transfer Clearing.',
              'recovered':'Matches the reviewed expected category and passes validation; no journal is booked.',
          },
          'cases_sha256':hash_json(cases),'policy_sha256':hash_json(BASELINE if args.mode=='baseline' else POLICY)}
    # Validate encryption before reserving the output or loading the GPU. All
    # report contents, including partial results, go directly to this descriptor.
    if args.private:encrypt_value('{}')
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    rows=[]
    status='interrupted'
    try:
        with LocalModel(model=name,digest=digest,minimum_free_mib=headroom) as model:
            for item in cases:
                result=infer(model,item['input'],args.profile,args.mode,think=args.think,
                             temperature=args.temperature,categories=categories)
                result.update(case_id=item['id'],expected=item['expected'])
                if 'transactions' in item:result['transactions']=item['transactions']
                rows.append(result)
                progress={'completed':len(rows),'total':len(cases),
                    'correct':result['category']==item['expected'] and not result['validation_errors'],
                    'seconds':round(result['elapsed_seconds'],2)}
                if not args.private:progress.update(case=item['id'],category=result['category'])
                print(json.dumps(progress),flush=True)
        status='complete'
    finally:
        with os.fdopen(fd,'w') as out:
            report=dict(meta,status=status,results=rows,summary=summarize(rows))
            serialized=json.dumps(report,indent=2)
            out.write(encrypt_value(serialized) if args.private else serialized)
            out.flush();os.fsync(out.fileno())
    score=summarize(rows)
    print(json.dumps(score),flush=True)
    return 0 if score['correct']==len(cases) else 1


if __name__=='__main__':sys.exit(main())
