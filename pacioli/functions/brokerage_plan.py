"""Deterministic journal legs for brokerage sources. This module never writes.

Money always comes from the source's two-decimal Amount, never quantity x price.
Sells use the provider's realized lots (tax basis, including wash sales). A wash
disallowance is its own source dated when the replacement makes it known, so a
later lots export cannot change an earlier month. Share moves post nothing when
a symbol moves out of and back into one account on the same day (they offset)
or when they match an owner-approved memo rule. Unknown or unsupported rows
raise instead of being guessed.
"""
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import combinations

from sqlalchemy import text

from pacioli.functions.schwab_normalization import action_kind


Leg = namedtuple('Leg', 'key debit credit amount timestamp')
TRANSFER_CLEARING = 'Transfer Clearing'
OPENING_EQUITY = 'Personal Equity'
# Schwab reuses these labels for cash (amount only) and for shares (quantity only).
MOVE_ACTIONS = ('journaled shares', 'internal transfer', 'security transfer')
INCOME = {'qualified_dividend': 'Qualified Dividends', 'ordinary_dividend': 'Ordinary Dividends',
          'capital_gain_distribution': 'Capital Gain Distributions', 'interest': 'Investment Interest'}
EXPENSE = {'fee': 'Investment Fees', 'foreign_tax': 'Foreign Taxes Paid'}
GAIN = {'short': 'Realized Gain - Short Term', 'long': 'Realized Gain - Long Term'}
LOSS = {'short': 'Realized Loss - Short Term', 'long': 'Realized Loss - Long Term'}
WASH = 'Wash Sale Disallowed Loss'
RETIREMENT = {'qualified_dividend': 'Retirement Dividends', 'ordinary_dividend': 'Retirement Dividends',
              'capital_gain_distribution': 'Retirement Capital Gain Distributions', 'interest': 'Retirement Interest',
              'realized': 'Retirement Realized Gain (Loss)'}
MAX_SAME_DAY_LOTS = 12
ZERO = Decimal('0')


class NotReady(ValueError):
    """Evidence is incomplete or ambiguous; nothing is posted yet."""


class Unsupported(ValueError):
    """An activity kind this version does not book; it needs review."""


def midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def normalized_action(action):
    return ' '.join((action or '').lower().split())


def kind_of(row):
    """Canonical kind of one activity row. A move label with only an amount is
    cash; with only a signed quantity it moves shares (positive in)."""
    if normalized_action(row.action) in MOVE_ACTIONS:
        if row.symbol and row.quantity and not row.amount:
            return 'share_move'
        if row.amount and not row.quantity and not row.symbol:
            return 'transfer'
    return action_kind(row.action)


def share_moves_on(session, account_id, day):
    rows = session.execute(text('''SELECT * FROM investments.brokerage_activity
        WHERE account_id=:account AND posted_on=:day AND symbol IS NOT NULL
          AND quantity IS NOT NULL AND quantity <> 0 AND (amount IS NULL OR amount = 0)'''),
        dict(account=account_id, day=day)).all()
    return [row for row in rows if kind_of(row) == 'share_move']


def move_rule(session, row):
    return session.execute(text('''SELECT * FROM investments.share_move_rules
        WHERE action=:action AND symbol=:symbol AND description=:description AND direction=:direction'''),
        dict(action=normalized_action(row.action), symbol=row.symbol, description=row.description,
             direction='in' if row.quantity > 0 else 'out')).first()


def memo_contradicted(session, symbol):
    """A memo line never has money, a price, or a held position anywhere."""
    return session.execute(text('''SELECT EXISTS (SELECT 1 FROM investments.brokerage_activity
            WHERE symbol=:symbol AND (coalesce(amount, 0) <> 0 OR coalesce(price, 0) <> 0))
        OR EXISTS (SELECT 1 FROM investments.position_lines WHERE symbol=:symbol)'''),
        {'symbol': symbol}).scalar()


def move_resolution(session, row):
    """'memo' (an approved rule; no economic effect) or 'offsetting' (the
    symbol's moves in this account on this day sum to zero, so neither shares
    nor cost change). Anything else needs review."""
    rule = move_rule(session, row)
    if rule is not None:
        if memo_contradicted(session, row.symbol):
            raise NotReady('memo_rule_contradicted')
        return rule.resolution
    same = [move.quantity for move in share_moves_on(session, row.account_id, row.posted_on)
            if move.symbol == row.symbol and move_rule(session, move) is None]
    if len(same) > 1 and sum(same, ZERO) == 0:
        return 'offsetting'
    raise Unsupported('share_move_needs_review')


def _account(session, account_id):
    row = session.execute(text('SELECT * FROM investments.brokerage_accounts WHERE id=:id'),
                          {'id': account_id}).first()
    if row is None:
        raise NotReady('missing_account')
    return row


def active_lots(session, account_id, symbol, closed_on):
    return session.execute(text('''SELECT l.* FROM investments.realized_lots l
        WHERE l.account_id=:account AND l.symbol=:symbol AND l.closed_on=:closed
          AND NOT EXISTS (SELECT 1 FROM investments.realized_lots newer WHERE newer.supersedes_id=l.id)
        ORDER BY l.opened_on NULLS LAST, l.id'''),
        dict(account=account_id, symbol=symbol, closed=closed_on)).all()


def _matches(lots, sell):
    """Proceeds must be exact. Lot quantities are displayed rounded (for example
    to two places while the sale shows three), so quantities may differ by at
    most the rounding of each displayed lot quantity."""
    if sum((lot.quantity for lot in lots), ZERO) == abs(sell.quantity):
        return sum((lot.proceeds for lot in lots), ZERO) == sell.amount
    tolerance = sum((Decimal('0.5') * Decimal(1).scaleb(min(lot.quantity.as_tuple().exponent, 0))
                     for lot in lots), ZERO)
    return (abs(sum((lot.quantity for lot in lots), ZERO) - abs(sell.quantity)) <= tolerance and
            sum((lot.proceeds for lot in lots), ZERO) == sell.amount)


def assign_lots(session, sell):
    """Lots for one sell. Same-day sells of a symbol must partition the lots exactly."""
    lots = active_lots(session, sell.account_id, sell.symbol, sell.posted_on)
    if not lots:
        raise NotReady('awaiting_realized_lots')
    sells = session.execute(text('''SELECT * FROM investments.brokerage_activity
        WHERE account_id=:account AND symbol=:symbol AND posted_on=:day ORDER BY source_id'''),
        dict(account=sell.account_id, symbol=sell.symbol, day=sell.posted_on)).all()
    sells = [row for row in sells if action_kind(row.action) == 'sell']
    if len(sells) == 1:
        if not _matches(lots, sell):
            raise NotReady('lots_do_not_match_sale')
        return lots
    if len(lots) > MAX_SAME_DAY_LOTS:
        raise NotReady('ambiguous_lot_assignment')
    solutions = []

    def search(remaining, index, chosen):
        if len(solutions) > 1:
            return
        if index == len(sells):
            if not remaining:
                solutions.append(dict(chosen))
            return
        for size in range(1, len(remaining) + 1):
            for subset in combinations(remaining, size):
                if _matches(subset, sells[index]):
                    rest = [lot for lot in remaining if lot not in subset]
                    chosen[sells[index].source_id] = subset
                    search(rest, index + 1, chosen)
                    chosen.pop(sells[index].source_id)

    search(list(lots), 0, {})
    # Interchangeable identical lots give equivalent solutions; compare by economics.
    signatures = {tuple(sorted((lot.cost_basis, lot.proceeds, lot.term, lot.quantity)
                               for lot in solution[sell.source_id])) for solution in solutions}
    if not solutions or len(signatures) != 1:
        raise NotReady('ambiguous_lot_assignment')
    return list(solutions[0][sell.source_id])


def _check_price(row, kind):
    if row.quantity is None or row.price is None or row.amount is None:
        return
    quantity, price, fees = abs(row.quantity), abs(row.price), abs(row.fees or ZERO)
    gross = quantity * price
    expected = abs(row.amount) - fees if kind == 'buy' else abs(row.amount) + fees
    # Displayed decimals bound the rounding; a whole-share quantity is exact
    # because fractional shares are always shown with their decimals.
    quantity_places = row.quantity.as_tuple().exponent
    tolerance = ((Decimal('0.5') * Decimal(1).scaleb(quantity_places) * price if quantity_places < 0 else ZERO) +
                 Decimal('0.5') * Decimal(1).scaleb(row.price.as_tuple().exponent) * quantity +
                 Decimal('0.01'))
    if abs(gross - expected) > tolerance:
        raise NotReady('price_amount_inconsistent')


def opening_day(session, account_id):
    return session.execute(text('SELECT as_of FROM investments.brokerage_openings WHERE account_id=:account'),
                           {'account': account_id}).scalar()


def activity_legs(session, account, row):
    kind = kind_of(row)
    when = midnight(row.posted_on)
    cash, amount = account.cash_subaccount, row.amount
    opened = opening_day(session, account.id)
    if opened is not None and row.posted_on <= opened:
        raise Unsupported('before_opening')
    if kind == 'unsupported':
        raise Unsupported('unsupported_activity')
    if kind == 'share_move':
        move_resolution(session, row)
        return []
    if kind == 'split':
        if amount:
            raise Unsupported('split_with_cash')
        return []
    if amount is None or amount == 0:
        raise Unsupported('missing_amount')
    if kind == 'transfer':
        if row.quantity:
            raise Unsupported('security_transfer')
        return [Leg('cash', cash, TRANSFER_CLEARING, amount, when) if amount > 0 else
                Leg('cash', TRANSFER_CLEARING, cash, -amount, when)]
    if kind == 'buy':
        if amount > 0 or not row.quantity:
            raise Unsupported('unexpected_buy_sign')
        _check_price(row, kind)
        return [Leg('cost', account.securities_subaccount, cash, -amount, when)]
    if kind == 'sell':
        if amount < 0 or not row.quantity:
            raise Unsupported('unexpected_sell_sign')
        _check_price(row, kind)
        return sale_legs(account, assign_lots(session, row), when)
    if kind in INCOME:
        if amount < 0:
            raise Unsupported('negative_income')
        target = RETIREMENT[kind] if retirement(account) else INCOME[kind]
        return [Leg('income', cash, target, amount, when)]
    if kind in EXPENSE:
        if amount > 0:
            raise Unsupported('expense_refund')
        return [Leg('expense', EXPENSE[kind], cash, -amount, when)]
    raise Unsupported('unsupported_activity')


def retirement(account):
    return getattr(account, 'tax_treatment', 'taxable') == 'retirement'


def sale_legs(account, lots, when):
    basis = sum((min(lot.proceeds, lot.cost_basis) for lot in lots), ZERO)
    legs = [Leg('basis', account.cash_subaccount, account.securities_subaccount, basis, when)] if basis else []
    if retirement(account):
        # No tax terms or wash sales inside a retirement account; results are OCI.
        gain = sum((lot.proceeds - lot.cost_basis for lot in lots if lot.proceeds > lot.cost_basis), ZERO)
        loss = sum((lot.cost_basis - lot.proceeds for lot in lots if lot.cost_basis > lot.proceeds), ZERO)
        if gain:
            legs.append(Leg('gain', account.cash_subaccount, RETIREMENT['realized'], gain, when))
        if loss:
            legs.append(Leg('loss', RETIREMENT['realized'], account.securities_subaccount, loss, when))
        return legs
    for term in ('short', 'long'):
        gain = sum((lot.proceeds - lot.cost_basis for lot in lots
                    if lot.term == term and lot.proceeds > lot.cost_basis), ZERO)
        loss = sum((lot.cost_basis - lot.proceeds for lot in lots
                    if lot.term == term and lot.cost_basis > lot.proceeds), ZERO)
        if gain:
            legs.append(Leg('gain:' + term, account.cash_subaccount, GAIN[term], gain, when))
        if loss:
            legs.append(Leg('loss:' + term, LOSS[term], account.securities_subaccount, loss, when))
    return legs


def wash_legs(session, account, row):
    if retirement(account):
        raise Unsupported('wash_in_retirement_account')
    lot = session.execute(text('SELECT * FROM investments.realized_lots WHERE id=:id'),
                          {'id': row.lot_id}).first()
    if row.amount > lot.cost_basis - lot.proceeds:
        raise NotReady('wash_exceeds_loss')
    return [Leg('wash', account.securities_subaccount, WASH, row.amount, midnight(row.trigger_on))]


def valuation_legs(session, account, row):
    previous = session.execute(text('''SELECT target FROM investments.valuation_points
        WHERE account_id=:account AND valuation_date < :day ORDER BY valuation_date DESC LIMIT 1'''),
        dict(account=row.account_id, day=row.valuation_date)).scalar() or ZERO
    delta = row.target - previous
    if not delta:
        return []
    # Gains already held at the opening are opening equity, not this period's OCI.
    mva = account.valuation_subaccount
    oci = OPENING_EQUITY if opening_day(session, account.id) == row.valuation_date else account.oci_subaccount
    return [Leg('valuation', mva, oci, delta, row.valued_at) if delta > 0 else
            Leg('valuation', oci, mva, -delta, row.valued_at)]


def opening_legs(session, account, row):
    """Balances held before the export window, at the end of the opening day."""
    lines = session.execute(text('''SELECT coalesce(sum(cost_basis), 0) FROM investments.brokerage_opening_lines
        WHERE source_id=:source'''), {'source': row.source_id}).scalar()
    if lines != row.cost_basis:
        raise NotReady('opening_lines_do_not_total')
    when = midnight(row.as_of + timedelta(days=1)) - timedelta(microseconds=1)
    legs = []
    if row.cash:
        legs.append(Leg('opening:cash', account.cash_subaccount, OPENING_EQUITY, row.cash, when) if row.cash > 0
                    else Leg('opening:cash', OPENING_EQUITY, account.cash_subaccount, -row.cash, when))
    if row.cost_basis:
        legs.append(Leg('opening:cost', account.securities_subaccount, OPENING_EQUITY, row.cost_basis, when))
    return legs


def legs_for(session, source_id):
    source = session.execute(text('SELECT * FROM investments.brokerage_sources WHERE source_id=:id'),
                             {'id': source_id}).first()
    if source is None:
        raise NotReady('missing_source')
    account = _account(session, source.account_id)
    table = {'activity': 'brokerage_activity', 'wash': 'wash_adjustments',
             'valuation': 'valuation_points', 'opening': 'brokerage_openings'}[source.kind]
    row = session.execute(text('SELECT * FROM investments.{} WHERE source_id=:id'.format(table)),
                          {'id': source_id}).first()
    if row is None:
        raise NotReady('missing_source_detail')
    builder = {'activity': activity_legs, 'wash': wash_legs, 'valuation': valuation_legs,
               'opening': opening_legs}[source.kind]
    return builder(session, account, row)
