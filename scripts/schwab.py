#!/usr/bin/env python
"""Schwab brokerage bookkeeping. Every command prints counts only.

    schwab.py register LABEL LAST_DIGITS OPENED_ON --reviewer EMAIL [--apply]
    schwab.py preview
    schwab.py post --reviewer EMAIL [--apply]
    schwab.py opening [--adopt --reviewer EMAIL] [--apply]
    schwab.py value --reviewer EMAIL [--fetch] [--apply]

`value --fetch` is the only command that contacts the network: it requests
closing prices from Yahoo for the fixed decoy universe plus held symbols
(owner-approved; see AGENTS.md "Approved disclosures").
"""
import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli import create_app
from pacioli.extensions import db


def emit(value):
    print(json.dumps(value, default=str, indent=2, sort_keys=True))


def register(args):
    from pacioli.functions.brokerage_accounts import register_account
    account_id = register_account(db.session, provider='schwab', label=args.label,
                                  masked_number=args.last_digits,
                                  opened_on=date.fromisoformat(args.opened_on), reviewer=args.reviewer)
    (db.session.commit if args.apply else db.session.rollback)()
    emit({'account_id': account_id, 'applied': args.apply})


def show_preview(args):
    from pacioli.functions.brokerage_posting import preview
    summary, _ = preview(db.session)
    db.session.rollback()
    emit(summary)


def post_ready(args):
    from pacioli.functions.brokerage_posting import post, preview
    summary, _ = preview(db.session)
    db.session.rollback()
    if not args.apply:
        emit(dict(summary, applied=False))
        return
    emit(post(summary['digest'], args.reviewer, 'Owner-requested Schwab posting'))


def opening(args):
    """Derive (and with --adopt record) openings for accounts older than their exports."""
    from pacioli.functions.brokerage_accounts import accounts
    from pacioli.functions import brokerage_opening as bo
    result = []
    for row in accounts(db.session):
        if bo.existing(db.session, row.id) is not None:
            result.append(dict(account_id=row.id, status='adopted'))
            continue
        derived = bo.derive(db.session, row.id)
        entry = dict(account_id=row.id, status=derived['status'], issues=derived['issues'],
                     opening_symbols=len(derived.get('lines', [])))
        if args.adopt and derived['status'] == 'ready':
            bo.adopt(db.session, row.id, derived['digest'], args.reviewer,
                     'Owner-requested opening rolled back from the positions export')
            entry['status'] = 'adopted_now'
        result.append(entry)
    (db.session.commit if args.adopt and args.apply else db.session.rollback)()
    emit(dict(accounts=result, by_status=dict(Counter(entry['status'] for entry in result)),
              applied=bool(args.adopt and args.apply)))


def value(args):
    from pacioli.functions import market_prices as mp
    from pacioli.functions.brokerage_accounts import accounts
    from pacioli.functions.brokerage_valuation import adopt, month_end_valuation, month_ends
    from sqlalchemy import text
    result = Counter()
    rows = accounts(db.session)
    symbols = {row[0] for row in db.session.execute(text(
        'SELECT DISTINCT symbol FROM investments.brokerage_activity WHERE symbol IS NOT NULL '
        'UNION SELECT symbol FROM investments.brokerage_opening_lines '
        # Approved memo symbols are Schwab-internal notices, not tradable tickers.
        "EXCEPT SELECT symbol FROM investments.share_move_rules WHERE resolution='memo'"))}
    if not args.fetch:
        universe = set(mp.request_universe(db.session)) | {mp.market_ticker(db.session, s) for s in symbols}
        db.session.rollback()
        emit({'held_symbols': len(symbols), 'request_universe': len(universe),
              'fetched': False, 'note': 'pass --fetch to request closes from Yahoo'})
        return
    # Held symbols join the permanent request universe before the request is made.
    mp.record_held(db.session, sorted(symbols))
    db.session.commit()
    from pacioli.functions.brokerage_plan import opening_day
    first = [opening_day(db.session, row.id) or row.opened_on for row in rows]
    start = min(first + [row.opened_on for row in rows]) - timedelta(days=10)
    closes = mp.fetch_closes(db.session, start)
    for row in rows:
        opened = opening_day(db.session, row.id)
        # The opening day is valued too, so gains held before the window stay opening equity.
        for day in ([opened] if opened else []) + month_ends(row.opened_on, date.today()):
            try:
                valuation = month_end_valuation(db.session, row, day, closes)
            except ValueError as exc:
                result['blocked: ' + str(exc).split(' on or before')[0]] += 1
                continue
            adopt(db.session, row, valuation, args.reviewer)
            result['valued'] += 1
    (db.session.commit if args.apply else db.session.rollback)()
    emit(dict(result, tickers_priced=len(closes), applied=args.apply))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('register')
    command.add_argument('label')
    command.add_argument('last_digits')
    command.add_argument('opened_on')
    command.add_argument('--reviewer', required=True)
    command.add_argument('--apply', action='store_true')
    command.set_defaults(handler=register)
    commands.add_parser('preview').set_defaults(handler=show_preview)
    command = commands.add_parser('post')
    command.add_argument('--reviewer', required=True)
    command.add_argument('--apply', action='store_true')
    command.set_defaults(handler=post_ready)
    command = commands.add_parser('opening')
    command.add_argument('--adopt', action='store_true')
    command.add_argument('--reviewer')
    command.add_argument('--apply', action='store_true')
    command.set_defaults(handler=opening)
    command = commands.add_parser('value')
    command.add_argument('--reviewer', required=True)
    command.add_argument('--fetch', action='store_true', help='Request closes from Yahoo (network)')
    command.add_argument('--apply', action='store_true')
    command.set_defaults(handler=value)
    args = parser.parse_args()
    if getattr(args, 'adopt', False) and not args.reviewer:
        parser.error('--adopt requires --reviewer')
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        args.handler(args)


if __name__ == '__main__':
    main()
