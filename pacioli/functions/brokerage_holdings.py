"""Share quantities and ledger balances of a brokerage account at a date."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from pacioli.functions.brokerage_plan import NotReady, Unsupported, kind_of, midnight, move_resolution


ZERO = Decimal('0')


def quantities_as_of(session, account_id, day):
    """Shares held at the end of `day`, from the opening and activity. Only posted
    kinds move quantity: offsetting share moves count (they sum to zero), memos never do."""
    held = defaultdict(lambda: ZERO)
    for symbol, quantity in session.execute(text('''SELECT l.symbol, l.quantity
            FROM investments.brokerage_opening_lines l JOIN investments.brokerage_openings o ON o.source_id=l.source_id
            WHERE o.account_id=:account AND o.as_of<=:day'''), dict(account=account_id, day=day)):
        held[symbol] += quantity
    rows = session.execute(text('''SELECT * FROM investments.brokerage_activity
        WHERE account_id=:account AND posted_on<=:day AND symbol IS NOT NULL AND quantity IS NOT NULL'''),
        dict(account=account_id, day=day)).all()
    for row in rows:
        kind = kind_of(row)
        if kind == 'buy' or kind == 'split':
            held[row.symbol] += abs(row.quantity)
        elif kind == 'sell':
            held[row.symbol] -= abs(row.quantity)
        elif kind == 'share_move':
            try:
                if move_resolution(session, row) == 'offsetting':
                    held[row.symbol] += row.quantity
            except (NotReady, Unsupported):
                pass
    return {symbol: quantity for symbol, quantity in held.items() if quantity}


def ledger_balance(session, subaccount, day):
    """Debit-positive balance of a subaccount through the end of `day` (UTC)."""
    return session.execute(text('''SELECT coalesce(sum(CASE WHEN debit_subaccount=:name THEN functional_amount
            ELSE -functional_amount END), 0)
        FROM bookkeeping.journal_entries
        WHERE (debit_subaccount=:name OR credit_subaccount=:name) AND timestamp < :end'''),
        dict(name=subaccount, end=midnight(day + timedelta(days=1)))).scalar()
