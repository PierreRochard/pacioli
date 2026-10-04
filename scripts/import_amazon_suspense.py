#!/usr/bin/env python
"""Import an owner-only encrypted item triage into the local review workbench.

This stages source-linked review evidence. It cannot run Qwen or post journals.
The source must be created with scripts.book_local_mappings.write_encrypted.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--triage', type=Path, required=True)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--prepare', action='store_true',
                        help='Also prepare a current-policy source-review run; no inference or posting')
    args = parser.parse_args(argv)
    if not args.reviewer.strip() or len(args.reviewer) > 240:
        parser.error('Supply a reviewer of 1 to 240 characters')

    from scripts.book_local_mappings import read_encrypted
    document = read_encrypted(args.triage)
    from pacioli import create_app
    from pacioli.extensions import db
    from pacioli.database.workbench import install_amazon_suspense
    from pacioli.functions.amazon_item_workflow import install_item_categories
    from pacioli.functions.amazon_suspense import import_triage, prepare_new_review_run

    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        install_amazon_suspense(db.engine)
        try:
            created = install_item_categories(db.session)
            plan = import_triage(document, args.reviewer.strip())
            run = prepare_new_review_run(plan.id, args.reviewer.strip()) if args.prepare else None
            plan_id, run_id = plan.id, run.id if run else plan.review_run_id
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        print(json.dumps(dict(plan_id=plan_id, review_run_id=run_id,
                              categories_created=len(created), journals_written=False,
                              model_started=False,
                              path='/reconciliation/amazon/suspense/?plan_id=' + str(plan_id))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
