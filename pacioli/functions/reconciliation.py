"""Statement anchors and explicit transfer reconciliation, using exact USD."""
import base64
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from sqlalchemy import text
from pacioli.extensions import db
from pacioli.models import (AccountingEvidence, BankStatements, OpeningBalances,
    JournalEntries, BankFeedAccounts, TransferMatches, TransferMatchLegs)
from pacioli.functions.chase_coverage import account_ranges, missing_ranges
from pacioli.functions.journal_posting import post_pair


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def money(value):
    if isinstance(value, (float, bool)):
        raise ValueError('Use decimal strings for money')
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError('A decimal USD amount is required') from None
    if not result.is_finite() or result != result.quantize(Decimal('.01')):
        raise ValueError('A finite USD amount with at most two decimals is required')
    return result


def midnight(day):
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def utc(when):
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def lock_ledger(actor, reason):
    if not actor or not reason or len(reason) > 2000:
        raise ValueError('Reviewer and a bounded evidence reason are required')
    db.session.execute(text("SET LOCAL lock_timeout='10s'"))
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
    db.session.execute(text('LOCK TABLE bookkeeping.journal_entries IN SHARE ROW EXCLUSIVE MODE'))
    db.session.execute(text("SELECT set_config('pacioli.actor', :actor, true), "
                            "set_config('pacioli.reason', :reason, true)"),
                       dict(actor=actor[:240], reason=reason))


def require_open(when):
    closed = db.session.execute(text('SELECT max(period) FROM bookkeeping.closed_periods')).scalar()
    if closed and when.strftime('%Y-%m') <= closed:
        raise ValueError('The affected month is closed')


def evidence(kind, payload, reviewer):
    encoded = dump(payload)
    sha = hashlib.sha256(encoded.encode()).hexdigest()
    old = db.session.query(AccountingEvidence).filter_by(sha256=sha).one_or_none()
    if old:
        if old.kind != kind:
            raise ValueError('Evidence kind conflicts')
        return old
    row = AccountingEvidence(kind=kind, sha256=sha, payload=encoded, reviewer=reviewer)
    db.session.add(row)
    db.session.flush()
    return row


def account(account_id):
    row = db.session.execute(text('SELECT id, name FROM ofx.acctfrom WHERE id=:id'),
                             {'id': account_id}).first()
    registered = db.session.query(BankFeedAccounts).get(account_id)
    if not row or not registered or registered.currency != 'USD':
        raise ValueError('Select a registered USD account')
    from pacioli.functions.accounting_report import chart_accounts
    element, cash = chart_accounts(db.session).get(row.name, (None, False))
    card = db.session.execute(text('SELECT id FROM ofx.ccacctfrom WHERE id=:id'),
                              {'id': account_id}).first()
    if (card and element != 'Liabilities') or (not card and not cash):
        raise ValueError('Bank chart account type is not reviewed')
    return row


def source_rows(account_id, start, end):
    return [dict(r) for r in db.session.execute(text('''SELECT id, date, amount, description
        FROM ofx.transactions WHERE account_id=:aid AND date>=:start AND date<:end ORDER BY id'''),
        dict(aid=account_id, start=midnight(start), end=midnight(end + timedelta(days=1))))]


def bank_booked(name, end):
    return db.session.execute(text('''SELECT COALESCE(SUM(
        CASE WHEN debit_subaccount=:name THEN functional_amount ELSE 0 END -
        CASE WHEN credit_subaccount=:name THEN functional_amount ELSE 0 END),0)
        FROM bookkeeping.journal_entries WHERE timestamp<:end'''), dict(name=name, end=end)).scalar()


def import_statement(metadata, pdf, reviewer):
    """Explicit reviewed extraction; never infer balances by OCR or an LLM."""
    if not pdf.startswith(b'%PDF-') or len(pdf) > 10 * 1024 * 1024:
        raise ValueError('A PDF under 10 MiB is required')
    account(int(metadata['account_id']))
    start, end = date.fromisoformat(metadata['start']), date.fromisoformat(metadata['end'])
    opening, closing, movement = (money(metadata[k]) for k in ('opening', 'closing', 'movement'))
    if start > end or opening + movement != closing or not metadata.get('verification'):
        raise ValueError('Statement arithmetic and account identity verification are required')
    record = dict(metadata, pdf_sha256=hashlib.sha256(pdf).hexdigest())
    old = db.session.query(BankStatements).filter_by(account_id=int(metadata['account_id']), end_date=end).one_or_none()
    if old:
        if json.loads(old.details) != record:
            raise ValueError('Conflicting statement for this account and date')
        return old
    doc = evidence('bank_statement', dict(metadata=record, pdf=base64.b64encode(pdf).decode()), reviewer)
    row = BankStatements(account_id=int(metadata['account_id']), evidence_id=doc.id,
        start_date=start, end_date=end, opening=opening, closing=closing, details=dump(record))
    db.session.add(row)
    db.session.flush()
    return row


def opening_preview(account_id, cutover):
    acct = account(account_id)
    anchors = db.session.query(BankStatements).filter_by(account_id=account_id).order_by(BankStatements.end_date).all()
    if not anchors:
        raise ValueError('Import statement evidence first')
    ranges, invalid = account_ranges(account_id)
    checks = []
    for anchor in anchors:
        if anchor.end_date < cutover:
            continue
        gaps = missing_ranges(ranges, cutover, anchor.end_date)
        if invalid or gaps:
            raise ValueError('Export coverage is incomplete for the opening calculation')
        rows = source_rows(account_id, cutover, anchor.end_date)
        net = sum((r['amount'] for r in rows), Decimal(0))
        checks.append(dict(statement_id=anchor.id, evidence_id=anchor.evidence_id,
            closing=str(anchor.closing), end=str(anchor.end_date), source_digest=digest(rows),
            source_count=len(rows), movement=str(net), implied_opening=str(anchor.closing-net)))
    if not checks or len({money(c['implied_opening']) for c in checks}) != 1:
        raise ValueError('Statement anchors disagree about the opening balance')
    # One directly documented beginning balance is sufficient for a new account;
    # historical back-calculations require two separate statement anchors.
    direct = any(a.start_date == cutover and a.opening == money(checks[0]['implied_opening']) for a in anchors)
    if len(checks) < 2 and not direct:
        raise ValueError('A back-calculated opening needs two statement anchors')
    result = dict(account_id=account_id, account=acct.name, cutover=str(cutover),
        amount=checks[0]['implied_opening'], checks=checks, directly_documented=direct)
    result['digest'] = digest(result)
    return result


def post_opening(account_id, cutover, expected_digest, reviewer):
    lock_ledger(reviewer, 'Adopt reviewed statement-supported opening')
    db.session.execute(text('LOCK TABLE ofx.stmttrn, ofx.acctfrom IN SHARE MODE'))
    preview = opening_preview(account_id, cutover)
    if preview['digest'] != expected_digest:
        raise ValueError('Opening evidence changed; preview again')
    old = db.session.query(OpeningBalances).get(account_id)
    if old:
        if old.evidence_digest != expected_digest:
            raise ValueError('An opening already exists with different evidence')
        if not opening_journal_matches(old):
            raise ValueError('Previously posted opening journal has changed')
        return old
    when = midnight(cutover) - timedelta(microseconds=1)
    require_open(when)
    before = db.session.query(JournalEntries.id).filter(JournalEntries.timestamp < midnight(cutover),
        (JournalEntries.debit_subaccount == preview['account']) |
        (JournalEntries.credit_subaccount == preview['account'])).first()
    if before:
        raise ValueError('Earlier bank journals require a separate opening review')
    amount = money(preview['amount'])
    entry = None
    if amount:
        entry = post_pair(timestamp=when,
            debit=preview['account'] if amount > 0 else 'Personal Equity',
            credit='Personal Equity' if amount > 0 else preview['account'],
            functional_amount=abs(amount), transaction_source='statement_opening',
            transaction_id=str(account_id), memo='Statement opening '+expected_digest)
        db.session.flush()
    row = OpeningBalances(account_id=account_id, cutover=cutover, amount=amount,
        evidence_digest=expected_digest, details=dump(preview), journal_id=entry.id if entry else None,
        reviewer=reviewer)
    db.session.add(row)
    db.session.flush()
    for check in preview['checks']:
        if bank_booked(preview['account'], midnight(date.fromisoformat(check['end'])+timedelta(days=1))) != money(check['closing']):
            raise ValueError('Booked transactions do not reconcile to the statement')
    return row


def statement_checks():
    result = []
    for row in db.session.query(BankStatements).order_by(BankStatements.account_id, BankStatements.end_date):
        name = account(row.account_id).name
        booked = bank_booked(name, midnight(row.end_date+timedelta(days=1)))
        ranges, invalid = account_ranges(row.account_id)
        result.append(dict(id=row.id, evidence_id=row.evidence_id, account=name, start=row.start_date, end=row.end_date,
            closing=row.closing, booked=booked, difference=booked-row.closing,
            full_coverage=not invalid and not missing_ranges(ranges,row.start_date,row.end_date),
            details=json.loads(row.details)))
    return result


def opening_journal_matches(row):
    if row.amount == 0:
        return row.journal_id is None
    entry=db.session.query(JournalEntries).get(row.journal_id)
    name=json.loads(row.details)['account']
    debit,credit=(name,'Personal Equity') if row.amount>0 else ('Personal Equity',name)
    return bool(entry and entry.transaction_source=='statement_opening' and
        entry.transaction_id==str(row.account_id) and entry.functional_amount==abs(row.amount) and
        entry.source_amount==abs(row.amount) and entry.source_currency=='USD' and
        entry.debit_subaccount==debit and entry.credit_subaccount==credit and
        entry.timestamp==midnight(row.cutover)-timedelta(microseconds=1))


def evidence_differences(session, end=None):
    result=[]
    from pacioli.functions.snapshot_reconciliation import reviewed
    for row in reviewed(session)[1]:
        if not row['active'] or row['valid']:
            continue
        try:
            affects_close = end is None or datetime.fromisoformat(row['proof']['as_of']) < end
        except (KeyError, TypeError, ValueError):
            affects_close = True  # Corrupt undated evidence cannot be silently excluded.
        if affects_close:
            result.append(dict(kind='qfx_snapshot_evidence', evidence_id=row['id']))
    resolved=resolved_transfer_ids(session)
    for match in session.query(TransferMatches):
        legs = json.loads(match.details)['legs']
        if end is not None and all(datetime.fromisoformat(leg['date']) >= end for leg in legs):
            continue
        if not all(l['journal_id'] in resolved for l in legs):
            result.append(dict(kind='transfer_evidence',match_id=match.id))
    openings={o.account_id:o for o in session.query(OpeningBalances)}
    for row in openings.values():
        if end is not None and midnight(row.cutover) - timedelta(microseconds=1) >= end:
            continue
        if not opening_journal_matches(row):
            result.append(dict(kind='opening_evidence',account_id=row.account_id))
    for row in session.query(BankStatements):
        if end is not None and midnight(row.end_date) >= end:
            continue
        if row.account_id not in openings:continue
        try:
            name=account(row.account_id).name
            booked=bank_booked(name,midnight(row.end_date+timedelta(days=1)))
            if booked!=row.closing:
                result.append(dict(kind='statement_balance',statement_id=row.id,booked=booked,bank_balance=row.closing))
        except ValueError:
            result.append(dict(kind='statement_account',statement_id=row.id))
    return result


def brokerage_transfer_leg(entry, session=None):
    """A brokerage cash transfer leg, shaped like an OFX leg with a namespaced account."""
    from pacioli.functions.brokerage_plan import kind_of, midnight
    session = session or db.session
    if not entry or entry.transaction_source != 'schwab' or entry.allocation_key != 'cash':
        raise ValueError('A brokerage transfer leg must be its cash leg')
    row = session.execute(text('''SELECT s.source_id, s.public_id, a.account_id, a.posted_on,
            a.amount, a.description, a.action, a.symbol, a.quantity, b.cash_subaccount
        FROM investments.brokerage_sources s
        JOIN investments.brokerage_activity a ON a.source_id=s.source_id
        JOIN investments.brokerage_accounts b ON b.id=a.account_id
        WHERE s.public_id=:id'''), {'id': entry.transaction_id}).first()
    if not row or kind_of(row) != 'transfer' or not row.amount:
        raise ValueError('Missing brokerage transfer source')
    linked = session.execute(text('SELECT source_id FROM investments.brokerage_journal_sources '
                                  'WHERE journal_entry_id=:id'), {'id': entry.id}).scalar()
    side, other = ('debit_subaccount', 'credit_subaccount') if row.amount > 0 else ('credit_subaccount', 'debit_subaccount')
    when = midnight(row.posted_on)
    if (linked != row.source_id or getattr(entry, side) != row.cash_subaccount
            or getattr(entry, other) != 'Transfer Clearing' or entry.functional_amount != abs(row.amount)
            or entry.source_amount != abs(row.amount) or entry.source_currency != 'USD'
            or utc(entry.timestamp) != when):
        raise ValueError('Transfer leg does not preserve its brokerage source')
    return dict(journal_id=entry.id, transaction_id=row.public_id,
                account_id='schwab:{}'.format(row.account_id), account=row.cash_subaccount,
                amount=str(row.amount), date=when.isoformat(), description=row.description,
                source='schwab')


def transfer_leg(entry, source=None, checked_accounts=None):
    if entry is not None and entry.transaction_source == 'schwab':
        return brokerage_transfer_leg(entry)
    if not entry or entry.transaction_source != 'ofx' or entry.allocation_key != '0':
        raise ValueError('A transfer leg must be an unsplit OFX journal')
    row = source if source is not None else db.session.execute(text('SELECT * FROM ofx.transactions WHERE id=:id'),
                             {'id': entry.transaction_id}).first()
    if not row:
        raise ValueError('Missing bank source')
    if checked_accounts is None:
        account(row.account_id)
    elif row.account_id not in checked_accounts:
        raise ValueError('Unreviewed transfer bank account')
    side = 'debit_subaccount' if row.amount > 0 else 'credit_subaccount'
    other = 'credit_subaccount' if row.amount > 0 else 'debit_subaccount'
    if (getattr(entry, side) != row.account or getattr(entry, other) != 'Transfer Clearing'
        or entry.functional_amount != abs(row.amount) or entry.source_amount != abs(row.amount)
        or entry.source_currency != 'USD' or entry.timestamp != utc(row.date)):
        raise ValueError('Transfer leg does not preserve its bank source')
    return dict(journal_id=entry.id, transaction_id=row.id, account_id=row.account_id,
        account=row.account, amount=str(row.amount), date=utc(row.date).isoformat(), description=row.description)


def internal_move(a, b):
    """Cash moved between two buckets of one brokerage account on one day (a sweep)."""
    return (a['account_id'] == b['account_id'] and a.get('source') == b.get('source') == 'schwab'
            and a['date'] == b['date'])


def transfer_pair(left, right):
    legs = sorted([transfer_leg(left), transfer_leg(right)], key=lambda r:r['journal_id'])
    if ((legs[0]['account_id'] == legs[1]['account_id'] and not internal_move(*legs)) or
        money(legs[0]['amount']) + money(legs[1]['amount']) != 0 or
        abs((datetime.fromisoformat(legs[0]['date']).date()-datetime.fromisoformat(legs[1]['date']).date()).days) > 5):
        raise ValueError('Transfer needs opposite amounts in different accounts within five days, '
                         'or a same-day move inside one brokerage account')
    return legs


def match_transfer(left_id, right_id, expected_digest, reviewer, reason):
    lock_ledger(reviewer, reason)
    legs = transfer_pair(db.session.query(JournalEntries).get(left_id), db.session.query(JournalEntries).get(right_id))
    sha = digest(legs)
    if sha != expected_digest:
        raise ValueError('Transfer changed; refresh candidates')
    old = db.session.query(TransferMatches).filter_by(fingerprint=sha).one_or_none()
    if old:
        return old
    for leg in legs:
        require_open(datetime.fromisoformat(leg['date']))
        if db.session.query(TransferMatchLegs).get(leg['journal_id']):
            raise ValueError('A transfer leg is already matched')
    row = TransferMatches(fingerprint=sha, details=dump(dict(legs=legs, reason=reason)), reviewer=reviewer)
    db.session.add(row)
    db.session.flush()
    db.session.add_all([TransferMatchLegs(journal_id=l['journal_id'], match_id=row.id) for l in legs])
    db.session.flush()
    return row


def resolved_transfer_ids(session, end=None):
    """Validate both sides on every report; a net-zero clearing account is insufficient."""
    result = set()
    entries = {e.id:e for e in session.query(JournalEntries).filter(
        JournalEntries.id.in_(session.query(TransferMatchLegs.journal_id)))}
    if not entries:
        return result
    sources = {row.id:row for row in session.execute(text(
        'SELECT * FROM ofx.transactions WHERE id=ANY(:ids)'),
        {'ids':[e.transaction_id for e in entries.values()]})}
    checked=set()
    for aid in {s.account_id for s in sources.values()}:
        try:
            account(aid);checked.add(aid)
        except ValueError:
            pass
    for match in session.query(TransferMatches):
        legs = json.loads(match.details)['legs']
        try:
            live = [transfer_leg(entries.get(l['journal_id']),
                source=sources.get(l['transaction_id']),checked_accounts=checked) for l in legs]
        except (ValueError, TypeError):
            continue
        if digest(live) == match.fingerprint and (end is None or all(datetime.fromisoformat(l['date']) < end for l in live)):
            result.update(l['journal_id'] for l in live)
    return result


def transfer_candidates():
    used = {r[0] for r in db.session.query(TransferMatchLegs.journal_id)}
    legs = []
    for entry in db.session.query(JournalEntries).filter(
        (JournalEntries.debit_subaccount=='Transfer Clearing') | (JournalEntries.credit_subaccount=='Transfer Clearing')):
        if entry.id in used:
            continue
        try:
            legs.append((entry, transfer_leg(entry)))
        except ValueError:
            continue
    pairs = []
    counts = {}
    for i, (left, a) in enumerate(legs):
        for right, b in legs[i+1:]:
            if (money(a['amount'])+money(b['amount']) != 0 or
                    (a['account_id'] == b['account_id'] and not internal_move(a, b))):
                continue
            try:
                pair = transfer_pair(left,right)
            except ValueError:
                continue
            pairs.append(dict(legs=pair, digest=digest(pair)))
            for leg in pair:
                counts[leg['journal_id']] = counts.get(leg['journal_id'],0)+1
    for pair in pairs:
        pair['unique'] = all(counts[l['journal_id']]==1 for l in pair['legs'])
        pair['internal_move'] = internal_move(*pair['legs'])
        # Candidate only. Even unique equal amounts need explicit payment evidence.
        pair['explicit_payment'] = all(re.search(r'PAYMENT|AUTOPAY|TRANSFER|CREDIT CRD|MONEYLINK|WIRE', l['description'] or '', re.I) for l in pair['legs'])
    return pairs


def unresolved_transfers(pairs=None):
    """Show every unresolved clearing leg, including those with no candidate."""
    if pairs is None:
        pairs = transfer_candidates()
    counts = {}
    for pair in pairs:
        for leg in pair['legs']:
            counts[leg['journal_id']] = counts.get(leg['journal_id'], 0) + 1
    resolved = resolved_transfer_ids(db.session)
    saved = {r[0] for r in db.session.query(TransferMatchLegs.journal_id)}
    result = []
    entries = db.session.query(JournalEntries).filter(
        (JournalEntries.debit_subaccount == 'Transfer Clearing') |
        (JournalEntries.credit_subaccount == 'Transfer Clearing')).order_by(JournalEntries.timestamp, JournalEntries.id)
    for entry in entries:
        if entry.id in resolved:
            continue
        try:
            leg = transfer_leg(entry)
            count = counts.get(entry.id, 0)
            status = ('Saved match no longer validates' if entry.id in saved else
                      'No counterpart in imported accounts within five days' if count == 0 else
                      'Multiple possible counterparts; review required' if count > 1 else
                      'One possible counterpart; evidence required')
            result.append(dict(leg, candidate_count=count, status=status))
        except ValueError:
            result.append(dict(journal_id=entry.id, transaction_id=entry.transaction_id,
                account=entry.credit_subaccount if entry.debit_subaccount == 'Transfer Clearing' else entry.debit_subaccount,
                date=utc(entry.timestamp).isoformat(), amount=str(entry.functional_amount *
                    (-1 if entry.debit_subaccount == 'Transfer Clearing' else 1)),
                description=entry.memo or '', candidate_count=0,
                status='Bank source or posting needs repair before matching'))
    return result
