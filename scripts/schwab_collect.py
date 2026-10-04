#!/usr/bin/env python
"""Collect Schwab exports through the owner's selected Firefox tab, with Qwen checks.

Sign in to Schwab in Firefox and select the tab with the Pacioli toolbar button, then:

    .venv/bin/python scripts/schwab_collect.py [--only transactions|realized_lots|positions]

Files are verified and kept privately under .local/schwab/downloads. Output is
counts and file names only; nothing is imported by this command.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli.functions.chase_local_bridge import LocalChaseError
from pacioli.functions.brokerage_accounts import tax_treatment_for
from pacioli.functions.schwab_local import (JOBS, AccountCollector, SchwabModel, account_key,
                                            schwab_bridge)


# Problems that affect every account stop the run; anything else skips one export or account.
STOP_RUN = {'authentication_required', 'bridge_unavailable', 'bridge_connection_lost', 'select_schwab_tab',
            'schwab_tab_not_active', 'local_model_busy', 'local_model_memory_pressure', 'local_model_unavailable'}


def save_manifest(store, accounts):
    """Private map from folder key to account; never printed."""
    from pacioli.functions.brokerage_accounts import tax_treatment_for
    from pacioli.functions.schwab_local import account_key
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = store / 'accounts.json'
    existing = json.loads(path.read_text()) if path.exists() else {}
    for account in accounts:
        existing[account_key(account)] = dict(account, tax_treatment=tax_treatment_for(account['type']))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as out:
        json.dump(existing, out, indent=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--only', choices=sorted({job['kind'] for job in JOBS}))
    parser.add_argument('--preview', action='store_true', help='Stop before each export click; no model, no file')
    parser.add_argument('--accounts', type=int, default=None, help='Collect only the first N listed accounts')
    args = parser.parse_args()
    bridge = schwab_bridge()
    ready = bridge.command('ping')
    if not ready.get('ready') or ready.get('provider') != 'schwab':
        raise SystemExit('Select the signed-in Schwab tab with the Pacioli toolbar button first')
    bridge.command('prepare')
    results, collector = [], None
    store = ROOT / '.local/schwab/downloads'
    jobs = [job for job in JOBS if not args.only or job['kind'] == args.only]
    try:
        model = None if args.preview else SchwabModel(report=lambda *a, **k: None)
        with (model or contextlib.nullcontext()):
            collector = AccountCollector(bridge, model, store=store)
            collector.to_history()
            accounts = collector.listed()[:args.accounts]
            save_manifest(store, accounts)
            for account in accounts:
                key = account_key(account)
                folder = store / key
                if not args.preview and (folder / '.complete').exists():
                    results.append({'account': key, 'skipped': 'already_collected'})
                    continue
                entry = {'account': key, 'files': []}
                results.append(entry)
                try:
                    collector.switch(account['digits'])
                except LocalChaseError as exc:
                    if exc.code in STOP_RUN:
                        raise
                    entry['error'] = exc.code
                    continue
                collector.store = store / key
                for job in jobs:
                    try:
                        path = collector.collect(job, preview=args.preview)
                    except LocalChaseError as exc:
                        if exc.code in STOP_RUN:
                            raise
                        entry['files'].append(dict(export=job['kind'], range=job['date_range'], skipped=exc.code,
                                                   last_steps=collector.steps[-4:]))
                        continue
                    entry['files'].append(dict(export=job['kind'], range=job['date_range'],
                                               bytes=path.stat().st_size if path else None))
                if not args.preview and all('bytes' in f or f.get('skipped') == 'page_not_available'
                                            for f in entry['files']):
                    (folder / '.complete').touch(mode=0o600)
    except LocalChaseError as exc:
        results.append({'stopped': exc.code, 'last_steps': (collector.steps if collector else [])[-6:]})
    finally:
        bridge.command('release')
    print(json.dumps(results, indent=1))


if __name__ == '__main__':
    main()
