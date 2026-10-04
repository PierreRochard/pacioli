#!/usr/bin/env python
"""Move cash-withdrawal rules and journals from Transfer Clearing to Cash Spending.

    .venv/bin/python scripts/reclassify_cash_withdrawals.py --reviewer EMAIL
    .venv/bin/python scripts/reclassify_cash_withdrawals.py --reviewer EMAIL --apply

The first command previews and rolls back. --apply first adds the Cash Spending
subaccount (under a new Cash Expense account) if it is missing. Output is counts only.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli import create_app
from pacioli.functions.cash_withdrawals import reclassify_cash_withdrawals


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--reviewer', required=True, help='Owner identity recorded in the audit trail')
    parser.add_argument('--apply', action='store_true', help='Commit the reclassification')
    args = parser.parse_args()
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        result = reclassify_cash_withdrawals(args.reviewer, apply=args.apply)
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
