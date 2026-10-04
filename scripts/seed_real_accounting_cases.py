#!/usr/bin/env python
"""Freeze descriptor/policy labels independently; never read model predictions."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from pacioli import create_app
from pacioli.extensions import db
from pacioli.functions.accounting_review import add_gold_case,export_gold
from pacioli.functions.reconciliation import digest

# These are reviewed regression cases, not executable merchant posting rules.
# Merchant families intentionally stay on one side of the partition boundary.


def seed(labels,apply=False):
    added=[]
    for merchant,partition,description,sign,category,reason in labels:
        row=db.session.execute(text('''SELECT id FROM ofx.transactions WHERE description=:description
            AND CASE WHEN :sign='debit' THEN amount<0 ELSE amount>0 END ORDER BY date,id LIMIT 1'''),
            dict(description=description,sign=sign)).first()
        if not row:raise ValueError('A frozen source descriptor/sign is absent; review seed inputs')
        case=add_gold_case(row.id,merchant,partition,category,reason,
            'Codex independent descriptor and owner-policy review; no model labels consulted')
        added.append(case.id)
    cases=export_gold();result=dict(cases=len(cases),merchants=len({c['merchant'] for c in cases}),dataset_digest=digest(cases))
    if apply:db.session.commit()
    else:db.session.rollback()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('labels',type=Path);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    app=create_app('pacioli.settings.ProdConfig',env='Prod')
    from scripts.book_local_mappings import read_encrypted
    with app.app_context():print(json.dumps(seed(read_encrypted(args.labels),args.apply)))
