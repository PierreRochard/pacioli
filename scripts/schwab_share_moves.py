#!/usr/bin/env python
"""Ask the local Qwen model about Schwab share moves that code cannot resolve.

    .venv/bin/python scripts/schwab_share_moves.py status
    .venv/bin/python scripts/schwab_share_moves.py propose [--apply]

Proposals are advice: the owner approves memo rules on the reconciliation
workbench (Schwab share moves tab). Output is counts only, never descriptions.
Without --apply proposals are rolled back. If Ollama is unavailable this stops.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli import create_app
from pacioli.extensions import db


def status():
    from pacioli.functions.share_move_advisor import pending
    groups = pending(db.session)
    return dict(groups=len(groups), rows=sum(group['facts']['row_count'] for group in groups),
                proposals=dict(Counter((group['proposal'].classification or 'invalid') if group['proposal']
                                       else 'none' for group in groups)),
                stale=sum(1 for group in groups if group['stale']),
                approvable=sum(1 for group in groups if group['approvable']))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('status', 'propose'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        if args.command == 'status':
            print(json.dumps(status(), indent=1))
            return
        from pacioli.functions.chase_local_model import LocalModel
        from pacioli.functions.share_move_advisor import PROFILE, propose
        from pacioli.functions.suspense_advisor import PROFILES
        name, digest, headroom = PROFILES[PROFILE]
        try:
            with LocalModel(model=name, digest=digest, minimum_free_mib=headroom) as model:
                counts = propose(db.session, model)
            summary = status()
            (db.session.commit if args.apply else db.session.rollback)()
        except Exception as exc:
            db.session.rollback()
            # Model and database errors can echo inputs: report only their type or code.
            print(json.dumps(dict(error=type(exc).__name__, code=str(exc)[:60] if type(exc).__name__ ==
                                  'LocalChaseError' else None)))
            sys.exit(1)
        print(json.dumps(dict(applied=args.apply, **counts, **summary), indent=1))


if __name__ == '__main__':
    main()
