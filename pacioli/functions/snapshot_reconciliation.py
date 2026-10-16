"""Reviewed QFX cutoff evidence; never infer that every DTASOF is a day close.

An explanation is accepted only for a particular pair of retained exports and
a statement anchor. The original balance stays intact. Every audit recomputes
the proof, so a later source change or broken bank posting reopens the issue.
"""
import base64
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json

from ofxtools.ofxalchemy import OFXParser
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.models import (AccountingEvidence, BankStatements, ConnectionResponses,
                            OFXFileImports, OpeningBalances, JournalEntries)
from pacioli.functions import reconciliation as rec
from pacioli.functions.ofx_file_import import _prepare_transactions, _utc, MAX_OFX_BYTES


KIND = 'qfx_snapshot_timing'


def _export(row):
    response = db.session.get(ConnectionResponses, row.response_id)
    if not response or not isinstance(response.response, str) or response.connection_id != row.connection_id:
        raise ValueError('Original QFX response is missing or has changed')
    raw = response.response.encode('latin-1')
    if len(raw) > MAX_OFX_BYTES or sha256(raw).hexdigest() != row.sha256:
        raise ValueError('Original QFX hash does not match its import')
    parser = OFXParser()
    parser.parse(BytesIO(raw))
    expected, _ = _prepare_transactions(parser)
    nodes = parser.findall('BANKMSGSRSV1/STMTTRNRS/STMTRS') + parser.findall('CREDITCARDMSGSRSV1/CCSTMTTRNRS/CCSTMTRS')
    node = nodes[0]
    if (node.findtext('CURDEF') != 'USD' or
            rec.money(node.findtext('LEDGERBAL/BALAMT')) != row.balance_amount or
            _utc(node.findtext('LEDGERBAL/DTASOF')) != rec.utc(row.balance_asof)):
        raise ValueError('QFX balance or currency metadata has changed')
    if (_utc(node.findtext('BANKTRANLIST/DTSTART')) != _utc(row.coverage_start) or
            _utc(node.findtext('BANKTRANLIST/DTEND')) != _utc(row.coverage_end)):
        raise ValueError('QFX transaction coverage metadata has changed')
    bank = node.find('BANKACCTFROM')
    if bank is not None:
        identity = db.session.execute(text('SELECT bankid,acctid,accttype FROM ofx.bankacctfrom WHERE id=:id'), {'id': row.ofx_account_id}).first()
        expected_identity = tuple((bank.findtext(tag) or '').strip() for tag in ('BANKID', 'ACCTID', 'ACCTTYPE'))
    else:
        identity = db.session.execute(text('SELECT acctid FROM ofx.ccacctfrom WHERE id=:id'), {'id': row.ofx_account_id}).first()
        expected_identity = ((node.findtext('CCACCTFROM/ACCTID') or '').strip(),)
    if identity is None or tuple(identity) != expected_identity:
        raise ValueError('QFX account identity does not match its import')
    current = db.session.execute(text('''SELECT fitid,dtposted,trnamt,trntype,name,memo
        FROM ofx.stmttrn WHERE acctfrom_id=:aid AND fitid=ANY(:ids)'''),
        {'aid': row.ofx_account_id, 'ids': list(expected)}).all() if expected else []
    actual = {r.fitid: (rec.utc(r.dtposted), r.trnamt, r.trntype, r.name or '', r.memo or '') for r in current}
    if actual != expected:
        raise ValueError('Original QFX transactions differ from the current bank sources')
    return expected


def _sources(account_id, start, end):
    # OFX source dates are naive UTC; bind naive UTC boundaries even if the
    # PostgreSQL session uses a different timezone.
    return [dict(r._mapping) for r in db.session.execute(text('''SELECT id,date,amount,description
        FROM ofx.transactions WHERE account_id=:aid AND date>=:start AND date<:end ORDER BY id'''),
        {'aid': account_id, 'start': datetime.combine(start, datetime.min.time()),
         'end': datetime.combine(end + timedelta(days=1), datetime.min.time())})]


def preview(original_id, witness_id, statement_id):
    original = db.session.get(OFXFileImports, original_id)
    witness = db.session.get(OFXFileImports, witness_id)
    anchor = db.session.get(BankStatements, statement_id)
    if not original or not witness or not anchor or not (original.ofx_account_id == witness.ofx_account_id == anchor.account_id):
        raise ValueError('Select original and later QFX exports and a statement for the same account')
    acct = rec.account(original.ofx_account_id)
    asof, later = rec.utc(original.balance_asof), rec.utc(witness.balance_asof)
    day, last_day = asof.date(), later.date()
    if anchor.end_date >= day or last_day <= day or witness.imported_at <= original.imported_at:
        raise ValueError('A prior statement and a later observed balance are required')
    opening = db.session.get(OpeningBalances, acct.id)
    if not opening or opening.cutover > anchor.end_date or not rec.opening_journal_matches(opening):
        raise ValueError('A valid statement-supported opening is required')
    current_balance = db.session.execute(text('SELECT balamt FROM ofx.ledgerbal WHERE acctfrom_id=:id AND dtasof=:asof'),
        {'id': acct.id, 'asof': asof.replace(tzinfo=None)}).scalar()
    if current_balance != original.balance_amount:
        raise ValueError('The reported snapshot was revised; review the current balance')
    current_later = db.session.execute(text('SELECT balamt FROM ofx.ledgerbal WHERE acctfrom_id=:id AND dtasof=:asof'),
        {'id': acct.id, 'asof': later.replace(tzinfo=None)}).scalar()
    if current_later != witness.balance_amount:
        raise ValueError('The later snapshot was revised; review the current balance')
    document = db.session.get(AccountingEvidence, anchor.evidence_id)
    if not document or document.kind != 'bank_statement' or sha256(document.payload.encode()).hexdigest() != document.sha256:
        raise ValueError('Statement evidence is missing or has changed')
    body = json.loads(document.payload)
    metadata = json.loads(anchor.details)
    if (body['metadata'] != metadata or sha256(base64.b64decode(body['pdf'], validate=True)).hexdigest() != metadata['pdf_sha256'] or
            rec.bank_booked(acct.name, rec.midnight(anchor.end_date + timedelta(days=1))) != anchor.closing):
        raise ValueError('The statement anchor no longer reconciles')
    ranges, invalid = rec.account_ranges(acct.id)
    if invalid or rec.missing_ranges(ranges, anchor.end_date + timedelta(days=1), last_day - timedelta(days=1)):
        raise ValueError('Export coverage between statement and later balance is incomplete')
    old_rows, new_rows = _export(original), _export(witness)
    if any(value[0].date() >= day for value in old_rows.values()):
        raise ValueError('The original export already includes activity on or after its balance date')
    if any(value[0].date() >= last_day for value in new_rows.values()):
        raise ValueError('The later export includes activity on or after its proposed cutoff')
    # Both reviewed exports reflect balances before activity on their labeled
    # dates. Freeze [old cutoff, later cutoff), so an ordinary future import on
    # the later date cannot retroactively reopen this earlier reconciliation.
    sources = _sources(acct.id, anchor.end_date + timedelta(days=1), last_day - timedelta(days=1))
    before = [s for s in sources if s['date'].date() < day]
    bridge = [s for s in sources if s['date'].date() >= day]
    same_day = [s for s in bridge if s['date'].date() == day]
    if not same_day or sum((s['amount'] for s in same_day), Decimal(0)) == 0:
        raise ValueError('There is no nonzero posting-date difference to explain')
    new_ids = {row[0] for row in db.session.execute(text('''
        SELECT public_id FROM bookkeeping.ofx_source_identities
        WHERE acctfrom_id=:account AND fitid=ANY(:fitids)
    '''), {'account': acct.id, 'fitids': list(new_rows)})}
    if not all(s['id'] in new_ids for s in bridge):
        raise ValueError('The later original export does not contain every intervening source transaction')
    # The old file must reach the last actual posting before the proposed
    # cutoff. A deliberately old date-range export alone cannot establish it.
    if before and (not original.coverage_end or rec.utc(original.coverage_end).date() < max(s['date'].date() for s in before)):
        raise ValueError('Original transaction range does not reach the proposed cutoff')
    bank_postings = {}
    for entry in db.session.query(JournalEntries).filter(JournalEntries.timestamp >= rec.midnight(anchor.end_date + timedelta(days=1)),
            JournalEntries.timestamp < rec.midnight(last_day),
            (JournalEntries.debit_subaccount == acct.name) | (JournalEntries.credit_subaccount == acct.name)):
        if entry.transaction_source != 'ofx' or entry.functional_currency != 'USD' or entry.source_currency != 'USD' or entry.source_amount != entry.functional_amount:
            raise ValueError('The balance interval contains an unsupported bank journal')
        key = (entry.transaction_id, rec.utc(entry.timestamp))
        signed = entry.functional_amount * (int(entry.debit_subaccount == acct.name) - int(entry.credit_subaccount == acct.name))
        bank_postings[key] = bank_postings.get(key, Decimal(0)) + signed
    expected_postings = {(s['id'], rec.utc(s['date'])): s['amount'] for s in sources}
    if bank_postings != expected_postings:
        raise ValueError('Bank journals do not preserve every source amount and posting time')
    before_balance = anchor.closing + sum((s['amount'] for s in before), Decimal(0))
    end_balance = before_balance + sum((s['amount'] for s in same_day), Decimal(0))
    later_balance = before_balance + sum((s['amount'] for s in bridge), Decimal(0))
    if before_balance != original.balance_amount or later_balance != witness.balance_amount:
        raise ValueError('The exact statement-to-snapshot roll-forward does not agree')
    if rec.bank_booked(acct.name, rec.midnight(day)) != before_balance or rec.bank_booked(acct.name, rec.midnight(last_day)) != later_balance:
        raise ValueError('The booked balance does not agree with the reviewed cutoff')
    result = dict(version=1, account_id=acct.id, account=acct.name, as_of=asof.isoformat(),
        original_id=original.id, original_sha256=original.sha256,
        witness_id=witness.id, witness_sha256=witness.sha256, witness_as_of=later.isoformat(), witness_cutoff=rec.midnight(last_day).isoformat(),
        statement_id=anchor.id, statement_sha256=document.sha256, statement_end=str(anchor.end_date), statement_balance=str(anchor.closing),
        opening_digest=opening.evidence_digest, cutoff=rec.midnight(day).isoformat(),
        bank_balance=str(before_balance), end_of_day_booked=str(end_balance), later_balance=str(later_balance),
        same_day_count=len(same_day), same_day_movement=str(end_balance-before_balance),
        intervening_count=len(bridge), source_digest=rec.digest(sources))
    result['digest'] = rec.digest(result)
    return result


def candidate(account_id, asof, amount):
    """Return a review candidate, never an automatic exception to the audit."""
    originals = db.session.query(OFXFileImports).filter_by(ofx_account_id=account_id, balance_asof=asof, balance_amount=amount).order_by(OFXFileImports.coverage_end.desc(), OFXFileImports.id.desc()).all()
    anchor = db.session.query(BankStatements).filter(BankStatements.account_id == account_id,
        BankStatements.end_date < rec.utc(asof).date()).order_by(BankStatements.end_date.desc()).first()
    witnesses = db.session.query(OFXFileImports).filter(OFXFileImports.ofx_account_id == account_id,
        OFXFileImports.balance_asof > asof).order_by(OFXFileImports.balance_asof.desc(), OFXFileImports.id.desc()).all()
    if anchor:
        for original in originals:
            for witness in witnesses:
                try:
                    return preview(original.id, witness.id, anchor.id)
                except (ValueError, KeyError, TypeError, UnicodeError):
                    continue
    return None


def approve(original_id, witness_id, statement_id, expected_digest, reviewer, reason):
    if not reason.strip():
        raise ValueError('Explain the independently reviewed export cutoff')
    rec.lock_ledger(reviewer, reason)
    # Same ordering as opening review. Freeze the retained files and source rows
    # while the proof and immutable approval are recorded together.
    db.session.execute(text('LOCK TABLE ofx.stmttrn, ofx.acctfrom, ofx.ledgerbal, admin.ofx_file_imports, admin.connection_responses IN SHARE MODE'))
    proof = preview(original_id, witness_id, statement_id)
    if proof['digest'] != expected_digest:
        raise ValueError('Snapshot evidence changed; preview again')
    previous = None
    for row in db.session.query(AccountingEvidence).filter_by(kind=KIND).order_by(AccountingEvidence.id.desc()):
        saved = json.loads(row.payload)['proof']
        if (saved['account_id'], saved['as_of']) == (proof['account_id'], proof['as_of']):
            if saved == proof:
                return row
            previous = row.id
            break
    rec.require_open(rec.midnight(datetime.fromisoformat(proof['as_of']).date()))
    return rec.evidence(KIND, dict(proof=proof, reason=reason, supersedes=previous), reviewer)


def reviewed(session):
    """Valid keys and visible review statuses; never trust a saved 'approved' flag."""
    keys, statuses, superseded = set(), [], {}
    for row in session.query(AccountingEvidence).filter_by(kind=KIND).order_by(AccountingEvidence.id):
        proof = {}
        try:
            if sha256(row.payload.encode()).hexdigest() != row.sha256:
                raise ValueError('Review evidence hash changed')
            body = json.loads(row.payload)
            proof = body['proof']
            previous = body.get('supersedes')
            if previous is not None:
                prior = next((s for s in statuses if s['id'] == previous), None)
                if not prior or (prior['proof'].get('account_id'), prior['proof'].get('as_of')) != (proof['account_id'], proof['as_of']):
                    raise ValueError('Superseded review does not describe this snapshot')
                superseded[previous] = row.id
            current = preview(proof['original_id'], proof['witness_id'], proof['statement_id'])
            if current != proof:
                raise ValueError('Reviewed sources or bank postings have changed')
            problem = None
        except (ValueError, KeyError, TypeError, UnicodeError):
            problem = 'Saved snapshot evidence no longer validates; review its sources and postings'
        statuses.append(dict(id=row.id, proof=proof, valid=problem is None, problem=problem, reviewer=row.reviewer))
    for status in statuses:
        status['superseded_by'] = superseded.get(status['id'])
        status['active'] = not status['superseded_by']
        if status['active'] and status['valid']:
            proof = status['proof']
            keys.add((proof['account_id'], datetime.fromisoformat(proof['as_of']), rec.money(proof['bank_balance'])))
    return keys, statuses
