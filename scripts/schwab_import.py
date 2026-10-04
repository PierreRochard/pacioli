#!/usr/bin/env python
"""Register collected Schwab accounts and import their exports as evidence. No journals.

    .venv/bin/python scripts/schwab_import.py --reviewer EMAIL [--apply]

Reads .local/schwab/downloads/accounts.json (private) and each account's folder.
Without --apply everything is rolled back. Output is counts per account key only.
"""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli import create_app
from pacioli.extensions import db

STORE = ROOT / '.local/schwab/downloads'


def label_for(account):
    words = re.sub(r'[^A-Za-z ]', ' ', account['type']).split()
    kind = words[0].title() if words else 'Account'
    return 'Schwab {} {}'.format(account['digits'], kind)[:31]


def latest(folder, pattern):
    files = sorted(folder.glob(pattern))
    return files[-1] if files else None


def import_account(key, account, reviewer):
    from pacioli.functions.brokerage_accounts import register_account
    from pacioli.functions.brokerage_import import (derive_wash_sources, import_lots, import_positions,
                                                    import_transactions)
    from pacioli.functions.schwab_export import parse_lots_csv, parse_positions_csv, parse_transactions_json
    folder = STORE / key
    summary = Counter()
    transactions = latest(folder, 'transactions-*.json')
    parsed = parse_transactions_json(transactions.read_bytes()) if transactions else None
    dates = [row['posted_on'] for row in parsed['rows']] if parsed else []
    opened_on = min(dates) if dates else (parsed['window_start'] if parsed else date.today())
    from pacioli.functions.brokerage_accounts import tax_treatment_for
    positions = latest(folder, 'positions-*.csv')
    snapshot = parse_positions_csv(positions.read_bytes(), expected_digits=account['digits']) if positions else None
    # Schwab's account name in the positions title is authoritative; menu text may be a nickname.
    treatment = tax_treatment_for(snapshot['account_name']) if snapshot else account['tax_treatment']
    account = dict(account, tax_treatment=treatment)
    account_id = register_account(db.session, provider='schwab', label=label_for(account),
                                  masked_number=account['digits'], opened_on=opened_on, reviewer=reviewer,
                                  tax_treatment=treatment)
    if parsed:
        result, created = import_transactions(db.session, account_id, transactions.read_bytes(), transactions.name,
                                              parsed, reviewer)
        summary['transactions_rows'] = len(parsed['rows'])
        summary['transactions_new'] = result.get('new_rows', 0)
        summary['transactions_state_' + result.get('state', 'replay')] += 1
    if positions:
        result, created = import_positions(db.session, account_id, positions.read_bytes(), positions.name,
                                           snapshot, reviewer)
        summary['position_lines'] = len(snapshot['lines'])
    for lots in sorted(folder.glob('realized_lots-*.csv')):
        parsed_lots = parse_lots_csv(lots.read_bytes())
        summary['lot_files'] += 1
        if not parsed_lots['rows']:
            summary['lot_files_empty'] += 1  # Schwab's no-results notice only.
            continue
        result, created = import_lots(db.session, account_id, lots.read_bytes(), lots.name, parsed_lots, reviewer)
        summary['lots_new'] += result.get('new_lots', 0)
        summary['lots_superseded'] += result.get('superseded_lots', 0)
    if account['tax_treatment'] == 'taxable':
        summary.update(derive_wash_sources(db.session, account_id))
    summary['tax_treatment'] = treatment
    return account_id, dict(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((STORE / 'accounts.json').read_text())
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    results = []
    with app.app_context():
        for key, account in sorted(manifest.items()):
            if not (STORE / key).is_dir():
                continue
            try:
                _, summary = import_account(key, account, args.reviewer)
                (db.session.commit if args.apply else db.session.rollback)()
                results.append(dict(account=key, **summary))
            except Exception as exc:
                db.session.rollback()
                # Database errors can echo parameter values: report only their type.
                detail = str(exc)[:120] if type(exc) is ValueError else type(exc).__name__
                results.append(dict(account=key, error=detail))
    print(json.dumps(dict(applied=args.apply, accounts=results), indent=1))


if __name__ == '__main__':
    main()
