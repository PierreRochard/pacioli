"""Store brokerage exports as evidence and activate their canonical rows.

Format parsers (see schwab_export.py) turn provider files into canonical rows;
this module is format independent. Exact-bytes replays are no-ops. Overlapping
transaction exports must agree day by day: every day an earlier export fully
covered must contain the identical multiset of economic rows. A recent export's
last day is provisional and may grow, never shrink. A disagreeing export is kept
as evidence but activates nothing.
"""
import base64
from collections import Counter, defaultdict
from datetime import date, timedelta
from hashlib import sha256

from sqlalchemy import text

from pacioli.functions.reconciliation import digest, evidence


ECONOMIC_FIELDS = ('posted_on', 'as_of', 'action', 'symbol', 'quantity', 'price', 'fees', 'amount')
LOT_FIELDS = ('symbol', 'quantity', 'opened_on', 'closed_on', 'proceeds', 'cost_basis', 'disallowed_loss', 'term')


def economic_digest(row):
    return digest({field: str(row.get(field)) if row.get(field) is not None else None
                   for field in ECONOMIC_FIELDS})


def _existing_export(session, raw_sha):
    return session.execute(text('SELECT * FROM investments.brokerage_exports WHERE sha256=:sha'),
                           {'sha': raw_sha}).first()


def _save_export(session, account_id, kind, fmt, raw, filename, reviewer, *, window_start=None,
                 window_end=None, provisional_from=None, as_of=None, row_count=0, state='active'):
    raw_sha = sha256(raw).hexdigest()
    record = evidence('schwab_source', {'sha256': raw_sha, 'filename': filename, 'format': fmt,
                                        'base64': base64.b64encode(raw).decode('ascii')}, reviewer)
    return session.execute(text('''INSERT INTO investments.brokerage_exports
        (account_id, kind, format, evidence_id, sha256, window_start, window_end, provisional_from,
         as_of, row_count, state, reviewer)
        VALUES (:account, :kind, :format, :evidence, :sha, :start, :end, :provisional, :as_of,
                :rows, :state, :reviewer) RETURNING id'''),
        dict(account=account_id, kind=kind, format=fmt, evidence=record.id, sha=raw_sha,
             start=window_start, end=window_end, provisional=provisional_from, as_of=as_of,
             rows=row_count, state=state, reviewer=reviewer)).scalar()


def _lock(session):
    session.execute(text("SET LOCAL lock_timeout='10s'"))
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.brokerage_import'))"))


def _final_coverage(session, account_id):
    """Days fully covered by earlier active exports, and days covered only provisionally."""
    final, provisional = set(), set()
    for row in session.execute(text('''SELECT window_start, window_end, provisional_from
        FROM investments.brokerage_exports WHERE account_id=:account AND kind='transactions'
          AND state='active' AND window_start IS NOT NULL'''), {'account': account_id}):
        day = row.window_start
        while day <= row.window_end:
            (provisional if row.provisional_from and day >= row.provisional_from else final).add(day)
            day += timedelta(days=1)
    return final, provisional - final


def import_transactions(session, account_id, raw, filename, parsed, reviewer, today=None):
    """parsed: format, window_start, window_end, rows (canonical dicts), total (or None).

    Returns (summary, created). The caller commits.
    """
    _lock(session)
    old = _existing_export(session, sha256(raw).hexdigest())
    if old is not None:
        return dict(export_id=old.id, state=old.state, new_rows=0, replay=True), False
    rows, start, end = parsed['rows'], parsed['window_start'], parsed['window_end']
    if start is None or end is None or start > end:
        raise ValueError('A transactions export needs its date range')
    if any(not start <= row['posted_on'] <= end for row in rows):
        raise ValueError('Export rows fall outside its declared date range')
    if parsed.get('total') is not None:
        if sum((row['amount'] for row in rows if row['amount'] is not None), 0) != parsed['total']:
            raise ValueError('Export rows do not add up to its stated total')
    today = today or date.today()
    provisional_from = end if end >= today - timedelta(days=1) else None
    incoming = defaultdict(Counter)
    for row in rows:
        incoming[row['posted_on']][economic_digest(row)] += 1
    existing = defaultdict(Counter)
    for row in session.execute(text('''SELECT posted_on, economic_digest, count(*) AS n
        FROM investments.brokerage_activity WHERE account_id=:account
          AND posted_on BETWEEN :start AND :end GROUP BY posted_on, economic_digest'''),
            dict(account=account_id, start=start, end=end)):
        existing[row.posted_on][row.economic_digest] = row.n
    final, provisional = _final_coverage(session, account_id)
    disagreements = []
    day = start
    while day <= end:
        if day in final and incoming[day] != existing[day]:
            disagreements.append(day)
        elif day in provisional and existing[day] - incoming[day]:
            disagreements.append(day)
        elif day not in final and day not in provisional and existing[day] and existing[day] - incoming[day]:
            disagreements.append(day)
        day += timedelta(days=1)
    state = 'disagreement' if disagreements else 'active'
    export_id = _save_export(session, account_id, 'transactions', parsed['format'], raw, filename, reviewer,
                             window_start=start, window_end=end, provisional_from=provisional_from,
                             row_count=len(rows), state=state)
    if disagreements:
        return dict(export_id=export_id, state=state, new_rows=0, disagreeing_days=len(disagreements)), True
    created = 0
    by_key = defaultdict(list)
    for row in rows:
        by_key[(row['posted_on'], economic_digest(row))].append(row)
    for (posted_on, key), group in sorted(by_key.items(), key=lambda item: (item[0][0], item[0][1])):
        have = existing[posted_on][key]
        for ordinal, row in enumerate(group, 1):
            if ordinal <= have:
                continue
            source_id = session.execute(text("INSERT INTO investments.brokerage_sources(account_id, kind) "
                                             "VALUES (:account, 'activity') RETURNING source_id"),
                                        {'account': account_id}).scalar()
            session.execute(text('''INSERT INTO investments.brokerage_activity
                (source_id, account_id, posted_on, as_of, action, symbol, description, quantity, price,
                 fees, amount, economic_digest, day_ordinal, first_export_id)
                VALUES (:source, :account, :posted_on, :as_of, :action, :symbol, :description, :quantity,
                        :price, :fees, :amount, :digest, :ordinal, :export)'''),
                dict(row, source=source_id, account=account_id, digest=key, ordinal=ordinal,
                     export=export_id, description=row.get('description') or ''))
            created += 1
    session.execute(text('''INSERT INTO investments.activity_observations(export_id, source_id)
        SELECT :export, source_id FROM investments.brokerage_activity
        WHERE account_id=:account AND posted_on BETWEEN :start AND :end'''),
        dict(export=export_id, account=account_id, start=start, end=end))
    return dict(export_id=export_id, state=state, new_rows=created, rows=len(rows)), True


def lot_digest(row):
    return digest({field: str(row.get(field)) if row.get(field) is not None else None for field in LOT_FIELDS})


def import_lots(session, account_id, raw, filename, parsed, reviewer):
    """Realized lots. A lot whose basis or disallowance was revised supersedes the prior one.

    Matching is by multiset: identical lots are already known; each remaining new lot
    with the same sale identity as a remaining prior lot supersedes it. Prior lots in
    the export's range that the export no longer contains are reported, not deleted.
    """
    _lock(session)
    old = _existing_export(session, sha256(raw).hexdigest())
    if old is not None:
        return dict(export_id=old.id, replay=True, new_lots=0), False
    export_id = _save_export(session, account_id, 'realized_lots', parsed['format'], raw, filename, reviewer,
                             window_start=parsed.get('window_start'), window_end=parsed.get('window_end'),
                             row_count=len(parsed['rows']))
    active = session.execute(text('''SELECT l.* FROM investments.realized_lots l WHERE l.account_id=:account
        AND NOT EXISTS (SELECT 1 FROM investments.realized_lots n WHERE n.supersedes_id=l.id)
        ORDER BY l.id'''), {'account': account_id}).all()
    remaining_old = defaultdict(list)
    for lot in active:
        remaining_old[lot.lot_digest].append(lot)
    fresh = []
    for row in parsed['rows']:
        key = lot_digest(row)
        if remaining_old[key]:
            remaining_old[key].pop(0)
        else:
            fresh.append((key, row))
    unmatched = [lot for lots in remaining_old.values() for lot in lots]

    def identity(item):
        return (item['symbol'], item['closed_on'], item.get('opened_on'), item['quantity'], item['proceeds'])

    ordinals = Counter(lot.lot_digest for lot in active)
    created = superseded = 0
    for key, row in fresh:
        prior = next((lot for lot in unmatched if identity(lot._mapping) == identity(row)), None)
        if prior is not None:
            unmatched.remove(prior)
        ordinals[key] += 1
        session.execute(text('''INSERT INTO investments.realized_lots
            (account_id, export_id, symbol, quantity, opened_on, closed_on, proceeds, cost_basis,
             disallowed_loss, term, lot_digest, lot_ordinal, supersedes_id)
            VALUES (:account, :export, :symbol, :quantity, :opened_on, :closed_on, :proceeds, :cost_basis,
                    :disallowed_loss, :term, :digest, :ordinal, :supersedes)'''),
            dict(row, account=account_id, export=export_id, digest=key, ordinal=ordinals[key],
                 supersedes=prior.id if prior else None, disallowed_loss=row.get('disallowed_loss') or 0))
        created += 1
        superseded += prior is not None
    start, end = parsed.get('window_start'), parsed.get('window_end')
    missing = sum(1 for lot in unmatched if start is not None and end is not None and start <= lot.closed_on <= end)
    return dict(export_id=export_id, new_lots=created, superseded_lots=superseded,
                prior_lots_missing_from_export=missing), True


def import_positions(session, account_id, raw, filename, parsed, reviewer):
    """A positions snapshot: as_of, market_date, end_of_day, total, lines (canonical)."""
    _lock(session)
    old = _existing_export(session, sha256(raw).hexdigest())
    if old is not None:
        return dict(export_id=old.id, replay=True), False
    export_id = _save_export(session, account_id, 'positions', parsed['format'], raw, filename, reviewer,
                             as_of=parsed['as_of'], row_count=len(parsed['lines']))
    snapshot_id = session.execute(text('''INSERT INTO investments.position_snapshots
        (account_id, export_id, as_of, market_date, end_of_day, total_market_value)
        VALUES (:account, :export, :as_of, :market_date, :end_of_day, :total) RETURNING id'''),
        dict(account=account_id, export=export_id, as_of=parsed['as_of'], market_date=parsed['market_date'],
             end_of_day=parsed['end_of_day'], total=parsed.get('total'))).scalar()
    for line in parsed['lines']:
        session.execute(text('''INSERT INTO investments.position_lines
            (snapshot_id, symbol, description, security_type, quantity, price, market_value, cost_basis, is_cash)
            VALUES (:snapshot, :symbol, :description, :security_type, :quantity, :price, :market_value,
                    :cost_basis, :is_cash)'''), dict(line, snapshot=snapshot_id))
    return dict(export_id=export_id, snapshot_id=snapshot_id, lines=len(parsed['lines'])), True


def derive_wash_sources(session, account_id, today=None):
    """Create a wash source for each active lot with a disallowed loss.

    Dated at the sale if a replacement was bought in the 30 days before it (or on
    the sale day), else at the first replacement within 30 days after. A lot with
    no replacement in either window is reported, never guessed. A revised lot whose
    prior version already has a wash source needs review instead of a second one.
    """
    from pacioli.functions.schwab_normalization import action_kind
    today = today or date.today()
    counts = Counter()
    lots = session.execute(text('''SELECT l.* FROM investments.realized_lots l
        WHERE l.account_id=:account AND l.disallowed_loss > 0
          AND NOT EXISTS (SELECT 1 FROM investments.realized_lots n WHERE n.supersedes_id=l.id)
          AND NOT EXISTS (SELECT 1 FROM investments.wash_adjustments w WHERE w.lot_id=l.id)
        ORDER BY l.closed_on, l.id'''), {'account': account_id}).all()
    for lot in lots:
        if lot.supersedes_id and session.execute(text(
                'SELECT 1 FROM investments.wash_adjustments WHERE lot_id=:id'), {'id': lot.supersedes_id}).first():
            counts['revision_needs_review'] += 1
            continue
        buys = [row for row in session.execute(text('''SELECT source_id, posted_on, action
            FROM investments.brokerage_activity WHERE account_id=:account AND symbol=:symbol
              AND posted_on BETWEEN :start AND :end ORDER BY posted_on, source_id'''),
            dict(account=account_id, symbol=lot.symbol, start=lot.closed_on - timedelta(days=30),
                 end=lot.closed_on + timedelta(days=30))) if action_kind(row.action) == 'buy']
        before = [row for row in buys if row.posted_on <= lot.closed_on]
        after = [row for row in buys if row.posted_on > lot.closed_on]
        trigger = before[-1] if before else (after[0] if after else None)
        if trigger is None:
            counts['awaiting_replacement' if today <= lot.closed_on + timedelta(days=30)
                   else 'wash_without_replacement'] += 1
            continue
        source_id = session.execute(text("INSERT INTO investments.brokerage_sources(account_id, kind) "
                                         "VALUES (:account, 'wash') RETURNING source_id"),
                                    {'account': account_id}).scalar()
        session.execute(text('''INSERT INTO investments.wash_adjustments
            (source_id, lot_id, amount, trigger_on, trigger_source_id)
            VALUES (:source, :lot, :amount, :day, :trigger)'''),
            dict(source=source_id, lot=lot.id, amount=lot.disallowed_loss,
                 day=max(lot.closed_on, trigger.posted_on), trigger=trigger.source_id))
        counts['created'] += 1
    return dict(counts)
