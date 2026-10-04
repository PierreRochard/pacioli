#!/usr/bin/env python
"""Adopt the previously reviewed local Chase statement evidence, without rewriting it."""
import argparse
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from pacioli import create_app
from pacioli.extensions import db
from pacioli.functions import reconciliation as r


def adopt(directory,apply=False):
    archive=json.loads((directory/'reconciliation.json').read_text())
    matches={v['file']:v for v in json.loads((directory/'transaction-match-details.json').read_text())}
    checks={v['file']:v for v in json.loads((directory/'transaction-checks.json').read_text())}
    rows=[]
    for account in archive:
        aid=account['account_id'];r.account(aid)
        last4=db.session.execute(text('''SELECT right(COALESCE(b.acctid,c.acctid),4)
            FROM ofx.acctfrom a LEFT JOIN ofx.bankacctfrom b ON b.id=a.id
            LEFT JOIN ofx.ccacctfrom c ON c.id=a.id WHERE a.id=:id'''),{'id':aid}).scalar()
        if last4!=account['last4']:raise ValueError('Archived account identity no longer matches the bank feed')
        first=min(account['statements'],key=lambda s:s['start'])
        cutover=date.fromisoformat(account['cutover'])
        direct_zero=(Decimal(first['opening'])==0 and date.fromisoformat(first['start'])>cutover)
        if direct_zero:cutover=date.fromisoformat(first['start'])
        for s in account['statements']:
            filename=s['file']
            if Path(filename).name!=filename:raise ValueError('Unsafe evidence filename')
            pdf=(directory/filename).read_bytes()
            if hashlib.sha256(pdf).hexdigest()!=s['sha256']:raise ValueError('Statement PDF changed')
            extracted=subprocess.check_output(['pdftotext','-layout',str(directory/filename),'-'],timeout=15).decode()
            if extracted.split()!=(directory/filename).with_suffix('.txt').read_text().split():
                raise ValueError('Archived extraction does not match its PDF')
            if matches[filename]['unmatched_imports'] or not checks[filename]['all_imported_rows_matched']:
                raise ValueError('Archived statement has unmatched imported rows')
            current=db.session.execute(text('''SELECT dtposted::date AS date,trnamt AS amount,name,memo
                FROM ofx.stmttrn WHERE acctfrom_id=:aid AND dtposted>=:start AND dtposted<:end
                ORDER BY dtposted,fitid'''),
                dict(aid=aid,start=r.midnight(date.fromisoformat(s['start'])),end=r.midnight(date.fromisoformat(s['end'])+timedelta(days=1)))).all()
            key=lambda row:(str(row['date']),Decimal(row['amount']),row['name'] or '',row['memo'] or '')
            archived=Counter(key(m['imported']) for m in matches[filename]['matches'])
            if Counter(key(dict(v)) for v in current)!=archived:
                raise ValueError('Imported statement-period transactions changed since independent matching')
            verification='PDF hash and extraction rechecked; statement arithmetic and all imported rows independently matched by amount, date and description.'
            if account['last4']!=s['pdf_account_last4']:
                verification+=' Historical PDF suffix {} was downloaded from account {} document listing; this association is retained explicitly.'.format(s['pdf_account_last4'],account['last4'])
            if direct_zero:
                verification+=' Direct zero-balance evidence starts {}; no earlier opening is asserted.'.format(cutover)
            metadata=dict(account_id=aid,start=s['start'],end=s['end'],opening=s['opening'],closing=s['closing'],
                movement=str(Decimal(s['closing'])-Decimal(s['opening'])),verification=verification,
                transaction_checks=checks[filename],matched_source_digest=r.digest([dict(v) for v in current]))
            row=r.import_statement(metadata,pdf,'Codex: reverified prior independent statement evidence')
            r.evidence('statement_row_reconciliation',dict(statement_id=row.id,details=matches[filename]),'Independent Decimal and one-to-one row checks')
        preview=r.opening_preview(aid,cutover)
        if Decimal(preview['amount'])!=Decimal(account['opening_candidates'][0]):
            raise ValueError('Statement-supported opening changed')
        r.post_opening(aid,cutover,preview['digest'],'Owner authorization: implement all five accounting improvements')
        rows.append(dict(account_id=aid,cutover=str(cutover),opening=preview['amount'],anchors=len(preview['checks'])))
    if any(c['difference'] for c in r.statement_checks()):raise ValueError('Statement reconciliation failed')
    if apply:db.session.commit()
    else:db.session.rollback()
    return dict(applied=apply,accounts=rows,statements=sum(len(a['statements']) for a in archive))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();app=create_app('pacioli.settings.ProdConfig',env='Prod')
    with app.app_context():
        print(json.dumps(adopt(args.directory,args.apply),indent=2))
