#!/usr/bin/env python
"""Review one saved Amazon archive against bank sources without posting."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from pacioli import create_app
from pacioli.extensions import db
from pacioli.functions.amazon_reconciliation import build_report, build_and_save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_id', type=int, help='Evidence ID of one imported Amazon archive')
    parser.add_argument('--apply', action='store_true', help='Save encrypted review evidence; never posts journals')
    args = parser.parse_args()
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        if args.apply:
            doc = build_and_save(args.archive_id, 'Owner-requested Amazon settlement review')
            body = json.loads(doc.payload)
            result = dict(review_id=doc.id, applied=True, summary=body['summary'], journals_posted=0)
            db.session.commit()
        else:
            db.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            body = build_report(db.session, args.archive_id)
            result = dict(applied=False, summary=body['summary'], journals_posted=0)
            db.session.rollback()
        print(json.dumps(result))


if __name__ == '__main__':
    main()
