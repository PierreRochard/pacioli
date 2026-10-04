#!/usr/bin/env python
"""Prepare and classify encrypted, source-bounded local bookkeeping batches.

Classification never writes journals. Separate reviewed source selections are
required by the posting command. No bank actions or external model calls occur.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from pacioli.settings import database_url
from pacioli.secure_fields import encrypt_value, decrypt_value, PREFIX
from pacioli.functions.chase_local_model import LocalModel, MODEL, DIGEST
from scripts.evaluate_local_mappings import (
    TUNED_POLICY, TUNED_CLARIFICATION, build_messages, compact_schema,
    expand_proposal, keyword_match_context, matches, mechanical_feedback,
)

LIVE_RULES = '''
LIVE BOOKKEEPING CONTEXT:
There are no itemized receipts supplied unless evidence explicitly says so.
Do not invent receipts, original refund links, insurance coverage or loan splits.
An outgoing payroll payment is NOT salary income or a payroll reversal just
because PAYROLL is in its description. Salary requires an actual incoming
employer wage payment, or evidence linking a real reversal to it.
In this chart Gas belongs to Utilities Expense: it means natural gas service,
NOT vehicle fuel or gas-station merchandise. Crowdfunding for medical costs is
not insurance. Generic payment processors and peer payments do not prove purpose.
No generic catch-all is permitted for missing household, vehicle, software,
donation, tax, tuition or business-expense categories. Do not invent categories.
Amazon Web Services is cloud hosting, distinct from Amazon retail settlements.
Phone/broadband, electricity/natural gas, medical care, restaurant meals, clear
clothing and entertainment may use matching existing expense categories when
their purpose is clear. Otherwise defer. These are suggestions for a bounded
reviewed source batch, not authorization for automatic future postings.
A missing receipt alone does not prevent a clear service classification:
restaurant merchant charges are Restaurants; cellular-provider service is
Cell Phone; Netflix/Spotify subscriptions are Entertainment. Clear bank text
is evidence. No receipt is needed for explicit utilities, hosting or payroll
income. Do not call a described merchant unknown merely because no receipt is
attached. Broad retailers and ambiguous purposes still require review.
'''
OPTIONS = {'temperature':0,'seed':42,'num_ctx':4096,'num_predict':384,'num_gpu':99}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def private_text(path):
    descriptor = os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'r') as source:
        details = os.fstat(source.fileno())
        if (not stat.S_ISREG(details.st_mode) or details.st_uid!=os.geteuid()
                or stat.S_IMODE(details.st_mode)&0o077):
            raise ValueError('Batch artifacts must be private owner-only regular files')
        return source.read()


def decode_encrypted(value):
    if not value.startswith(PREFIX):
        raise ValueError('Batch artifacts must be encrypted')
    return json.loads(decrypt_value(value))


def read_encrypted(path):
    return decode_encrypted(private_text(path))


def write_encrypted(path, value):
    path = Path(path)
    descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w') as out:
        out.write(encrypt_value(json.dumps(value,default=str)))


def read_proposals(path):
    if not Path(path).exists():
        return []
    return [decode_encrypted(line) for line in private_text(path).splitlines() if line]


def merchant_group(value):
    """Conservatively batch similar descriptions, never define posting scope.

    Digits can identify a merchant, service, recipient or destination account.
    Keep them unless an explicit suffix identifies a reference/store number.
    In particular, an Amazon brand or payment processor is not a shared purpose.
    Callers must still provide the original examples for each group to the model.
    """
    value = re.sub(r'\s+', ' ', html.unescape(value).upper()).strip()
    original = value
    # Remove only the final labelled token, not everything after an ACH ID.
    # Requiring punctuation and a digit avoids treating ordinary text such as
    # "REFERENCE BOOKS" or "ORDER 66" as processor metadata.
    reference = re.compile(
        r'\s+(?:(?:PPD|CCD|WEB|TEL) ID|'
        r'(?:TRACE|TRANSACTION|CONFIRMATION|REFERENCE|REF|SEQ)(?: (?:ID|NO))?)'
        r'\s*[:#]\s*(?=[A-Z0-9-]{1,64}$)(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)*$')
    while True:
        normalized = reference.sub('', value).strip()
        if normalized == value:
            break
        value = normalized
    # Only the known retail descriptor bases permit an unlabelled *reference.
    # Keep AMZN*..., PAYPAL*..., and service names verbatim: their suffix may
    # identify the actual merchant/product rather than a transaction reference.
    retail_reference = re.fullmatch(
        r'(AMAZON\.COM|AMAZON MKTPL|AMZN MKTP(?: US)?)\*'
        r'(?=[A-Z0-9]{8,20}$)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]+', value)
    if retail_reference:
        value = retail_reference.group(1)
    # A bare "STORE #123" has no merchant identity apart from the number.
    value = re.sub(r'(?<=\s)(STORE|LOCATION|BRANCH)\s*#\s*\d{1,8}$', r'\1', value)
    return value.strip() or original or 'UNKNOWN'


def prepare(directory, supermarket_policy):
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    with create_engine(database_url()).begin() as connection:
        connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        rows = [dict(row) for row in connection.execute(text('''
            SELECT t.id,t.date,t.amount,t.description,t.type::text,t.account,t.account_id,b.currency
            FROM ofx.transactions t LEFT JOIN bookkeeping.bank_feed_accounts b
              ON b.ofx_account_id=t.account_id
            WHERE t.journal_entry_id IS NULL ORDER BY t.date,t.id'''))]
        chart = [dict(row) for row in connection.execute(text('''
            SELECT s.name,a.name account,c.name classification,e.name element
            FROM bookkeeping.subaccounts s JOIN bookkeeping.accounts a ON a.name=s.parent
            JOIN bookkeeping.classifications c ON c.name=a.parent
            JOIN bookkeeping.elements e ON e.name=c.parent ORDER BY e.name,s.name'''))]
        amazon_count = connection.execute(text('SELECT count(*) FROM amazon.items')).scalar()
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Source IDs are not unique')
    # Canonical strings persist exact decimal quantities and timezone semantics.
    rows = json.loads(json.dumps(rows,default=str))
    grouped = defaultdict(list)
    for row in rows:
        grouped[merchant_group(row['description'])].append(row['id'])
    groups = [{'id':fingerprint({'merchant':key,'ids':ids})[:20],
               'merchant':key,'source_ids':ids} for key,ids in grouped.items()]
    groups.sort(key=lambda group:(-len(group['source_ids']),group['merchant']))
    policy = TUNED_POLICY+LIVE_RULES+(
        '\nOwner policy: supermarket purchases may be booked in full to Groceries without receipts.\n'
        if supermarket_policy == 'groceries' else
        '\nSupermarket and broad-retailer purchases without itemized receipts require review; do not assume food-only receipts.\n')
    snapshot = {'created_at':datetime.now(timezone.utc).isoformat(),'rows':rows,
                'chart':chart,'groups':groups,'amazon_item_count':amazon_count,
                'supermarket_policy':supermarket_policy,'policy':policy,
                'clarification':TUNED_CLARIFICATION,'model':MODEL,'model_digest':DIGEST,
                'options':OPTIONS,'prompt_sha256':fingerprint([policy,TUNED_CLARIFICATION]),
                'source_sha256':fingerprint(rows)}
    snapshot['snapshot_sha256'] = fingerprint(snapshot)
    write_encrypted(directory/'snapshot.enc',snapshot)
    print(json.dumps({'event':'prepared','transactions':len(rows),'groups':len(groups),
                      'supermarket_policy':supermarket_policy}),flush=True)


def group_input(snapshot, group):
    ids = set(group['source_ids'])
    rows = [row for row in snapshot['rows'] if row['id'] in ids]
    examples = [{'description':row['description'],'amount':row['amount'],
                 'account':row['account'],'currency':row['currency']} for row in rows]
    records = {'source':'ofx','examples':examples,'evidence':{
        'itemized_receipts_available':False,
        'amazon_item_records_in_database':snapshot['amazon_item_count'],
        'source_record_count':len(rows),
        'chart_Gas_account':'Utilities Expense, not motor fuel'},'do_not_match':[]}
    # These are source-ID-bounded postings, not newly activated global rules.
    # Similar merchants outside this group are not fabricated negative labels.
    # Select distinct descriptions/accounts/signs. Every source still undergoes
    # complete mechanical coverage checks; omitted records are not auto-approved.
    distinct = {}
    for row in examples:
        key = (row['description'],row['account'],Decimal(row['amount']) > 0)
        distinct.setdefault(key,row)
    ordered = sorted(distinct.values(),key=lambda row:(row['description'],row['account'],row['amount']))
    if len(ordered) > 6:
        selected = [ordered[round(index*(len(ordered)-1)/5)] for index in range(6)]
    else:
        selected = ordered
    display = dict(records,examples=selected)
    display['evidence'] = dict(records['evidence'],distinct_examples=len(ordered),
                               sampled=len(ordered)>len(selected))
    return records,display


def classify(directory, limit=None, evidence_file=None):
    snapshot = read_encrypted(directory/'snapshot.enc')
    saved_hash = snapshot['snapshot_sha256']
    if fingerprint({key:value for key,value in snapshot.items() if key!='snapshot_sha256'}) != saved_hash:
        raise ValueError('Snapshot fingerprint mismatch')
    evidence = read_encrypted(evidence_file) if evidence_file else None
    if evidence and evidence.get('snapshot_sha256')!=saved_hash:
        raise ValueError('Merchant evidence belongs to another snapshot')
    facts = evidence['groups'] if evidence else {}
    proposals_path = directory/('enriched-proposals.jsonl.enc' if evidence else 'proposals.jsonl.enc')
    existing = read_proposals(proposals_path)
    if any(row['snapshot_sha256'] != saved_hash for row in existing):
        raise ValueError('Proposals belong to another snapshot')
    completed = {row['group_id'] for row in existing}
    if len(completed) != len(existing):
        raise ValueError('Duplicate proposals')
    grouped_chart = defaultdict(list)
    for row in snapshot['chart']:
        if row['name'] != 'Amazon Suspense Account':
            grouped_chart[row['element']].append(row['name'])
    names = [name for group in grouped_chart.values() for name in group]
    schema = compact_schema(names)
    pending = [group for group in snapshot['groups'] if group['id'] not in completed]
    if evidence:
        unknown = set(facts)-{group['id'] for group in snapshot['groups']}
        if unknown:
            raise ValueError('Merchant evidence names unknown groups')
        pending = [group for group in pending if group['id'] in facts]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        print(json.dumps({'event':'nothing_to_classify','completed':len(completed)}));return
    with LocalModel() as model:
        descriptor = os.open(proposals_path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(descriptor,'a') as out:
            for index,group in enumerate(pending,1):
                full,display = group_input(snapshot,group)
                clarification = snapshot['clarification']
                if evidence:
                    display['evidence']['merchant_review'] = facts[group['id']]
                    clarification += ('\nUse the reviewed merchant activity supplied in evidence to draft the'
                        ' category. That evidence does not require an additional receipt for a clear'
                        ' service. Empty do_not_match means no exclusions were supplied; never invent'
                        ' exclusions. Keywords must be literal bank-description text.')
                attempts = [];repair = None; started = time.monotonic()
                for attempt in range(3):
                    messages = build_messages(display,grouped_chart,snapshot['policy'],clarification,
                                               match_context=True,repair=repair)
                    model.resources();model.loaded = True
                    response = model.api('chat',{'model':MODEL,'messages':messages,'format':schema,
                        'stream':True,'keep_alive':120,'options':snapshot['options']})
                    try:
                        draft = json.loads(response['message']['content'])
                        expand_proposal(draft,'ofx')
                        if draft['decision'] not in ('propose_mapping','needs_review'):
                            raise ValueError('Invalid decision')
                        if draft['decision']=='propose_mapping' and draft['category'] not in names:
                            raise ValueError('Unknown category')
                        if response.get('done_reason')=='length':
                            raise ValueError('Truncated response')
                        failures = mechanical_feedback(full,draft)
                    except (ValueError,KeyError,TypeError):
                        draft = None;failures = {'invalid_response':True}
                    attempts.append({'draft':draft,'validation_failures':failures,
                                     'messages_sha256':fingerprint(messages),
                                     'input_tokens':response.get('prompt_eval_count'),
                                     'output_tokens':response.get('eval_count')})
                    if not failures or draft is None:
                        break
                    repair = {'rejected_draft':draft,'validation_failures':failures,
                              'calculated_common_candidate':keyword_match_context(full)}
                proposal = {'group_id':group['id'],'snapshot_sha256':saved_hash,
                            'source_count':len(group['source_ids']),'attempts':attempts,
                            'draft':draft,'validation_failures':failures,
                            'merchant_review':facts.get(group['id']),
                            'effective_prompt_sha256':fingerprint([snapshot['policy'],clarification]),
                            'seconds':round(time.monotonic()-started,3)}
                proposal['proposal_sha256'] = fingerprint(proposal)
                out.write(encrypt_value(json.dumps(proposal))+'\n');out.flush();os.fsync(out.fileno())
                print(json.dumps({'event':'classified','completed':len(completed)+index,
                    'total':len(snapshot['groups']),'sources':len(group['source_ids']),
                    'decision':draft['decision'] if draft else 'invalid_response',
                    'mechanically_valid':not bool(failures),'seconds':proposal['seconds']}),flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','classify','preview','post'))
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--supermarket-policy',choices=('review','groceries'),default='review')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--review',type=Path,
                        help='Encrypted independently reviewed source selection; required to preview/post')
    parser.add_argument('--evidence',type=Path,help='Encrypted reviewed merchant facts for a second classification pass')
    parser.add_argument('--proposals',type=Path,help='Explicit encrypted JSONL proposal selection for preview/post')
    parser.add_argument('--bulk',action='store_true',help='Rebuild derived balances once within the atomic posting transaction')
    args = parser.parse_args()
    if args.action=='prepare':
        prepare(args.batch,args.supermarket_policy)
    elif args.action=='classify':
        classify(args.batch,args.limit,args.evidence)
    else:
        if not args.review:
            parser.error('--review is required; model suggestions cannot approve themselves')
        from pacioli import create_app
        from pacioli.functions.local_mapping_posting import post_reviewed_batch
        snapshot = read_encrypted(args.batch/'snapshot.enc')
        proposals = read_proposals(args.proposals or args.batch/'proposals.jsonl.enc')
        review = read_encrypted(args.review)
        app = create_app('pacioli.settings.ProdConfig',env='Prod')
        with app.app_context():
            summary = post_reviewed_batch(snapshot,proposals,review,commit=args.action=='post',bulk=args.bulk,
                progress=lambda done,total:print(json.dumps({'event':'staged','count':done,'total':total}),flush=True))
        output = args.batch/('{}-{}.enc'.format(args.action,time.time_ns()))
        write_encrypted(output,summary)
        print(json.dumps({key:value for key,value in summary.items() if key!='journal_ids'}),flush=True)


if __name__=='__main__':
    main()
