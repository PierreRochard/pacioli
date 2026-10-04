"""Opening balances for brokerage accounts older than their export window.

Schwab exports at most four years of history. An account that already held cash
or shares when the window starts gets one opening, as of the end of the day
before the window. It is rolled back from the latest end-of-day positions
export through every imported activity row:

- cash: the positions cash line minus every activity amount;
- shares per symbol: the positions quantity minus every quantity change;
- cost per symbol: Schwab's cost basis minus every change booked to securities
  (buys, the basis of sold lots, wash-sale adjustments).

A symbol with no opening shares must roll back to exactly zero cost. That check,
negative quantities, and unknown cost changes block adoption. The opening is
posted against Personal Equity, like a bank opening. Nothing here writes until
adopt() is called with the reviewed digest.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from pacioli.functions.brokerage_plan import NotReady, Unsupported, kind_of, midnight, move_resolution
from pacioli.functions.reconciliation import digest, evidence


ZERO = Decimal('0')


def window_start(session, account_id):
    return session.execute(text('''SELECT min(window_start) FROM investments.brokerage_exports
        WHERE account_id=:account AND kind='transactions' AND state='active' '''), {'account': account_id}).scalar()


def latest_snapshot(session, account_id):
    return session.execute(text('''SELECT * FROM investments.position_snapshots WHERE account_id=:account
        ORDER BY as_of DESC, id DESC LIMIT 1'''), {'account': account_id}).first()


def _sold_basis(session, account_id, symbol, day):
    """Basis of every active lot closed that day (all same-day sales), or None without lots."""
    lots = session.execute(text('''SELECT count(*), coalesce(sum(cost_basis), 0)
        FROM investments.realized_lots l
        WHERE l.account_id=:account AND l.symbol=:symbol AND l.closed_on=:day
          AND NOT EXISTS (SELECT 1 FROM investments.realized_lots newer WHERE newer.supersedes_id=l.id)'''),
        dict(account=account_id, symbol=symbol, day=day)).first()
    return lots[1] if lots[0] else None


def derive(session, account_id):
    """The opening an account needs, with blocking issues; never writes."""
    start = window_start(session, account_id)
    snapshot = latest_snapshot(session, account_id)
    if start is None or snapshot is None:
        return dict(account_id=account_id, status='blocked', issues=['missing_exports'])
    issues = []
    if not snapshot.end_of_day:
        issues.append('positions_not_end_of_day')
    lines = session.execute(text('SELECT * FROM investments.position_lines WHERE snapshot_id=:id'),
                            {'id': snapshot.id}).all()
    rows = session.execute(text('''SELECT * FROM investments.brokerage_activity
        WHERE account_id=:account AND posted_on<=:day ORDER BY posted_on, source_id'''),
        dict(account=account_id, day=snapshot.market_date)).all()
    if any(row.posted_on < start for row in rows):
        issues.append('activity_before_window')
    cash = sum((line.market_value or ZERO for line in lines if line.is_cash), ZERO)
    quantity, cost, cost_known = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO), defaultdict(lambda: True)
    sale_days = set()
    for line in lines:
        if line.is_cash or not line.symbol:
            continue
        quantity[line.symbol] += line.quantity or ZERO
        if line.cost_basis is None:
            cost_known[line.symbol] = False
        cost[line.symbol] += line.cost_basis or ZERO
    for row in rows:
        cash -= row.amount or ZERO
        if not row.symbol or not row.quantity:
            continue
        kind = kind_of(row)
        if kind in ('buy', 'split'):
            quantity[row.symbol] -= abs(row.quantity)
            if kind == 'buy':
                cost[row.symbol] += row.amount or ZERO  # A buy's amount is negative.
        elif kind == 'sell':
            quantity[row.symbol] += abs(row.quantity)
            if (row.symbol, row.posted_on) not in sale_days:
                sale_days.add((row.symbol, row.posted_on))
                basis = _sold_basis(session, account_id, row.symbol, row.posted_on)
                if basis is None:
                    cost_known[row.symbol] = False
                else:
                    cost[row.symbol] += basis
        elif kind == 'share_move':
            try:
                if move_resolution(session, row) == 'memo':
                    continue
            except (NotReady, Unsupported):
                cost_known[row.symbol] = False  # Moved cost is unknown until resolved.
            quantity[row.symbol] -= row.quantity
    # Lots are counted once per sale above; disallowed losses raised the basis Schwab reports.
    for symbol, amount in session.execute(text('''SELECT l.symbol, sum(w.amount)
        FROM investments.wash_adjustments w JOIN investments.realized_lots l ON l.id=w.lot_id
        JOIN investments.brokerage_sources s ON s.source_id=w.source_id
        WHERE s.account_id=:account AND w.trigger_on<=:day GROUP BY l.symbol'''),
            dict(account=account_id, day=snapshot.market_date)):
        cost[symbol] -= amount
    opening_lines, symbol_issues = [], defaultdict(int)
    for symbol in sorted(set(quantity) | set(cost)):
        shares, basis, known = quantity[symbol], cost[symbol], cost_known[symbol]
        if shares < 0:
            symbol_issues['negative_opening_quantity'] += 1
        elif shares == 0:
            if known and basis != 0:
                symbol_issues['cost_does_not_roll_back'] += 1
        elif not known:
            symbol_issues['opening_cost_unknown'] += 1
        elif basis < 0:
            symbol_issues['negative_opening_cost'] += 1
        else:
            opening_lines.append(dict(symbol=symbol, quantity=str(shares), cost_basis=str(basis)))
    issues.extend('{}:{}'.format(name, count) for name, count in sorted(symbol_issues.items()))
    total = sum((Decimal(line['cost_basis']) for line in opening_lines), ZERO)
    result = dict(account_id=account_id, as_of=str(start - timedelta(days=1)), snapshot_id=snapshot.id,
                  cash=str(cash), cost_basis=str(total), lines=opening_lines, issues=issues)
    result['status'] = ('blocked' if issues else
                        'not_needed' if cash == 0 and not opening_lines else 'ready')
    result['digest'] = digest(result)
    return result


def existing(session, account_id):
    return session.execute(text('SELECT * FROM investments.brokerage_openings WHERE account_id=:account'),
                           {'account': account_id}).first()


def adopt(session, account_id, expected_digest, reviewer, reason):
    """Record a ready opening as a brokerage source; the posting service books it."""
    from pacioli.functions.reconciliation import lock_ledger
    lock_ledger(reviewer, reason)
    if existing(session, account_id) is not None:
        raise ValueError('This account already has an opening')
    opening = derive(session, account_id)
    if opening['digest'] != expected_digest:
        raise ValueError('Opening evidence changed; derive it again')
    if opening['status'] != 'ready':
        raise ValueError('Only a ready opening can be adopted')
    posted_before = session.execute(text('''SELECT count(*) FROM bookkeeping.journal_entries j
        JOIN investments.brokerage_journal_sources l ON l.journal_entry_id=j.id
        JOIN investments.brokerage_sources s ON s.source_id=l.source_id
        WHERE s.account_id=:account AND j.timestamp < :start'''),
        dict(account=account_id, start=midnight(date.fromisoformat(opening['as_of']) + timedelta(days=1)))).scalar()
    if posted_before:
        raise ValueError('Journals already exist before the opening date')
    record = evidence('brokerage_opening', opening, reviewer)
    source_id = session.execute(text("INSERT INTO investments.brokerage_sources(account_id, kind) "
                                     "VALUES (:account, 'opening') RETURNING source_id"),
                                {'account': account_id}).scalar()
    session.execute(text('''INSERT INTO investments.brokerage_openings
        (source_id, account_id, as_of, cash, cost_basis, snapshot_id, evidence_id, reviewer, reason)
        VALUES (:source, :account, :as_of, :cash, :cost, :snapshot, :evidence, :reviewer, :reason)'''),
        dict(source=source_id, account=account_id, as_of=opening['as_of'], cash=opening['cash'],
             cost=opening['cost_basis'], snapshot=opening['snapshot_id'], evidence=record.id,
             reviewer=reviewer, reason=reason))
    for line in opening['lines']:
        session.execute(text('''INSERT INTO investments.brokerage_opening_lines(source_id, symbol, quantity, cost_basis)
            VALUES (:source, :symbol, :quantity, :cost)'''),
            dict(source=source_id, symbol=line['symbol'], quantity=line['quantity'], cost=line['cost_basis']))
    return source_id
