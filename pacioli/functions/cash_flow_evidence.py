"""Read-only, session-scoped evidence for a direct-method cash-flow report.

No helper in this module uses the Flask/global session.  A caller can therefore
hold one repeatable-read snapshot for both report arithmetic and these checks.
Classifications are report evidence, never journal mutations or model proposals.
"""
from collections import defaultdict
import base64
import binascii
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

from sqlalchemy import or_, text

from pacioli.models import (AccountingEvidence, AccountingReviews, BankStatements,
    ChaseExportCoverage, JournalEntries, OFXFileImports, OpeningBalances,
    TransferMatchLegs, TransferMatches)
from pacioli.functions.chase_coverage import merge_ranges, missing_ranges


ZERO = Decimal('0')
CLASSIFICATION_KIND = 'cash_flow_classification'


def _utc(value):
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def _digest(value):
    return sha256(_dump(value).encode()).hexdigest()


def _finite(value):
    return isinstance(value, Decimal) and value.is_finite()


def _ids(values):
    if (not isinstance(values, (list, tuple)) or not values or
            any(type(value) is not int or value < 1 for value in values) or
            len(set(values)) != len(values)):
        raise ValueError('Distinct positive integer identifiers are required')
    return sorted(values)


def _chart(session):
    return {row.name: dict(row) for row in session.execute(text('''
        SELECT s.name,a.name AS parent,a.is_cash,a.cash_source,c.parent AS element
        FROM bookkeeping.subaccounts s JOIN bookkeeping.accounts a ON a.name=s.parent
        JOIN bookkeeping.classifications c ON c.name=a.parent'''))}


def _accounts(session):
    # Only public bookkeeping names and internal IDs; never provider numbers.
    return {row.id: dict(row) for row in session.execute(text('''
        SELECT a.id,a.name,b.currency,cc.id IS NOT NULL AS is_card
        FROM ofx.acctfrom a LEFT JOIN bookkeeping.bank_feed_accounts b ON b.ofx_account_id=a.id
        LEFT JOIN ofx.ccacctfrom cc ON cc.id=a.id'''))}


def _sources(session, identifiers):
    if not identifiers:
        return {}
    return {row.id: dict(row) for row in session.execute(text('''
        SELECT i.public_id AS id,t.dtposted AS date,t.trnamt AS amount,
               concat(t.name,t.memo) AS description,a.name AS account,
               t.acctfrom_id AS account_id,b.currency,i.source_id
        FROM bookkeeping.ofx_source_identities i
        JOIN ofx.stmttrn t ON t.acctfrom_id=i.acctfrom_id AND t.fitid=i.fitid
        JOIN ofx.acctfrom a ON a.id=t.acctfrom_id
        LEFT JOIN bookkeeping.bank_feed_accounts b ON b.ofx_account_id=t.acctfrom_id
        WHERE i.public_id=ANY(:ids)'''), {'ids': list(identifiers)})}


def _associations(session, identifiers):
    if not identifiers:
        return {}
    return dict(session.execute(text('''SELECT journal_entry_id,source_id
        FROM bookkeeping.ofx_journal_sources WHERE journal_entry_id=ANY(:ids)'''),
        {'ids': list(identifiers)}).all())


def _journal_dict(entry):
    row = {column.name: getattr(entry, column.name)
           for column in JournalEntries.__table__.columns}
    row['timestamp'] = _utc(entry.timestamp).isoformat()
    return json.loads(_dump(row))


def _snapshot_journal(row):
    value = dict(row)
    value['timestamp'] = _utc(datetime.fromisoformat(value['timestamp'])).isoformat()
    return value


def _snapshot_source(row):
    value = dict(row)
    value['date'] = _utc(datetime.fromisoformat(str(value['date']))).isoformat()
    return json.loads(_dump(value))


def cash_flow_classification_fingerprint(session, journal_ids, review_ids):
    """Bind a report-only classification to live sources and owner review facts.

    Each selected journal must still exactly match a referenced review snapshot,
    retain its OFX source/association, and have one USD cash leg against suspense.
    The returned digest additionally binds current cash taxonomy and review facts.
    Calling this function neither creates evidence nor authorizes classification.
    """
    ids, rids = _ids(journal_ids), _ids(review_ids)
    entries = session.query(JournalEntries).filter(JournalEntries.id.in_(ids)).all()
    reviews = session.query(AccountingReviews).filter(AccountingReviews.id.in_(rids)).all()
    if len(entries) != len(ids) or len(reviews) != len(rids):
        raise ValueError('Classification references missing journals or reviews')
    chart = _chart(session)
    sources = _sources(session, {entry.transaction_id for entry in entries})
    associations = _associations(session, ids)
    review_rows, reviewed = [], defaultdict(list)
    for review in sorted(reviews, key=lambda row: row.id):
        snapshot, facts = json.loads(review.snapshot), json.loads(review.facts)
        if (not facts.get('text') or not facts.get('source') or not facts.get('verified_by') or
                _digest({k: v for k, v in snapshot.items() if k != 'snapshot_sha256'}) !=
                snapshot.get('snapshot_sha256')):
            raise ValueError('Owner review facts or snapshot evidence are incomplete')
        review_rows.append(dict(id=review.id, mapping_id=review.mapping_id,
                                facts=facts, snapshot=snapshot, reviewer=review.reviewer))
        for journal in snapshot['journals']:
            if journal['mapping_id'] == review.mapping_id:
                reviewed[journal['id']].append((journal, snapshot['sources']))
    financial, source_rows, chart_rows = [], {}, {}
    for entry in sorted(entries, key=lambda row: row.id):
        source = sources.get(entry.transaction_id)
        debit_cash = chart.get(entry.debit_subaccount, {}).get('is_cash', False)
        credit_cash = chart.get(entry.credit_subaccount, {}).get('is_cash', False)
        if (not source or entry.transaction_source != 'ofx' or debit_cash == credit_cash or
                'Bookkeeping Suspense' not in (entry.debit_subaccount, entry.credit_subaccount) or
                source['currency'] != 'USD' or entry.source_currency != 'USD' or
                entry.functional_currency != 'USD' or
                associations.get(entry.id) != source['source_id'] or
                entry.functional_amount != entry.source_amount or
                entry.functional_amount != abs(source['amount']) or
                entry.timestamp != _utc(source['date']) or
                (entry.debit_subaccount if source['amount'] > 0 else entry.credit_subaccount) != source['account']):
            raise ValueError('Classification requires an unchanged USD cash source in suspense')
        live = _journal_dict(entry)
        public_source = {k: v for k, v in source.items() if k != 'source_id'}
        expected_source = _snapshot_source(public_source)
        matching = any(_snapshot_journal(old) == live and
                       any(_snapshot_source(old_source) == expected_source
                           for old_source in old_sources if old_source['id'] == source['id'])
                       for old, old_sources in reviewed[entry.id])
        if not matching:
            raise ValueError('Classification exceeds or differs from the owner review snapshot')
        financial.append(live)
        source_rows[source['id']] = dict(expected_source, source_id=source['source_id'])
        chart_rows[source['account']] = chart[source['account']]
    return _digest(dict(version=1, journals=financial, reviews=review_rows,
                        sources=[source_rows[key] for key in sorted(source_rows)],
                        cash_accounts=[chart_rows[key] for key in sorted(chart_rows)]))


def _classifications(session, start, end):
    result, issues, conflicted = {}, [], set()
    # Only this evidence kind is decrypted; bank documents/credentials are unrelated.
    for evidence in session.query(AccountingEvidence).filter_by(kind=CLASSIFICATION_KIND):
        try:
            body = json.loads(evidence.payload)
            ids, reviews = _ids(body['journal_ids']), _ids(body['review_ids'])
            section, label = body['section'], body['label']
            if (section not in ('Operating', 'Investing', 'Financing') or
                    not isinstance(label, str) or not label.strip() or len(label) > 200 or
                    not evidence.reviewer or
                    sha256(evidence.payload.encode()).hexdigest() != evidence.sha256 or
                    cash_flow_classification_fingerprint(session, ids, reviews) != body['source_fingerprint']):
                raise ValueError('Stale or invalid classification evidence')
            relevant = session.query(JournalEntries.id).filter(JournalEntries.id.in_(ids),
                JournalEntries.timestamp >= start, JournalEntries.timestamp < end).all()
            for (identifier,) in relevant:
                value = dict(section=section, label=label, evidence_id=evidence.id,
                             review_ids=reviews)
                if identifier in conflicted:
                    continue
                if identifier in result and (result[identifier]['section'], result[identifier]['label']) != (section, label):
                    del result[identifier]
                    conflicted.add(identifier)
                    issues.append(dict(kind='conflicting_classification', journal_id=identifier))
                else:
                    result[identifier] = value
        except (KeyError, ValueError, TypeError, AttributeError):
            issues.append(dict(kind='invalid_classification_evidence', evidence_id=evidence.id))
    return result, issues


def _transfers(session, start, end, chart, accounts):
    names = [name for name, value in chart.items() if value['is_cash']]
    relevant_match_ids = [row[0] for row in session.query(TransferMatchLegs.match_id)
        .join(JournalEntries, JournalEntries.id == TransferMatchLegs.journal_id)
        .filter(JournalEntries.timestamp >= start, JournalEntries.timestamp < end,
                or_(JournalEntries.debit_subaccount.in_(names),
                    JournalEntries.credit_subaccount.in_(names))).distinct()]
    if not relevant_match_ids:
        return {}, []
    links = session.query(TransferMatchLegs).filter(
        TransferMatchLegs.match_id.in_(relevant_match_ids)).all()
    by_match = defaultdict(set)
    for link in links:
        by_match[link.match_id].add(link.journal_id)
    entries = {row.id: row for row in session.query(JournalEntries).filter(
        JournalEntries.id.in_([link.journal_id for link in links]))}
    sources = _sources(session, {row.transaction_id for row in entries.values()})
    associations = _associations(session, list(entries))
    result, issues = {}, []
    for match in session.query(TransferMatches).filter(TransferMatches.id.in_(relevant_match_ids)):
        actual_ids = by_match[match.id]
        relevant = any(identifier in entries and start <= _utc(entries[identifier].timestamp) < end
                       for identifier in actual_ids)
        if not relevant:
            continue
        try:
            saved = json.loads(match.details)['legs']
            if (len(saved) != 2 or len(actual_ids) != 2 or
                    {leg['journal_id'] for leg in saved} != actual_ids):
                raise ValueError('Transfer associations differ')
            live, cash, brokerage = [], [], []
            for old in saved:
                entry = entries[old['journal_id']]
                if entry.transaction_source == 'schwab':
                    from pacioli.functions.reconciliation import brokerage_transfer_leg
                    live.append(brokerage_transfer_leg(entry, session))
                    cash.append(False)
                    brokerage.append(True)
                    continue
                brokerage.append(False)
                source = sources[entry.transaction_id]
                acct = accounts[source['account_id']]
                taxonomy = chart.get(source['account'], {})
                cash.append(bool(taxonomy.get('is_cash')))
                valid_type = taxonomy.get('element') == 'Liabilities' if acct['is_card'] else cash[-1]
                bank_side, other = ((entry.debit_subaccount, entry.credit_subaccount)
                                   if source['amount'] > 0 else (entry.credit_subaccount, entry.debit_subaccount))
                if (entry.transaction_source != 'ofx' or entry.allocation_key != '0' or
                        not valid_type or source['currency'] != 'USD' or
                        bank_side != source['account'] or other != 'Transfer Clearing' or
                        entry.functional_amount != abs(source['amount']) or
                        entry.source_amount != entry.functional_amount or
                        entry.functional_currency != 'USD' or entry.source_currency != 'USD' or
                        _utc(entry.timestamp) != _utc(source['date']) or
                        associations.get(entry.id) != source['source_id']):
                    raise ValueError('Transfer source changed')
                live.append(dict(journal_id=entry.id, transaction_id=source['id'],
                    account_id=source['account_id'], account=source['account'],
                    amount=str(source['amount']), date=_utc(source['date']).isoformat(),
                    description=source['description']))
            if (live != saved or _digest(live) != match.fingerprint or
                    live[0]['account_id'] == live[1]['account_id'] or
                    sum((Decimal(leg['amount']) for leg in live), ZERO) != ZERO or
                    abs((datetime.fromisoformat(live[0]['date']).date() -
                         datetime.fromisoformat(live[1]['date']).date()).days) > 5):
                raise ValueError('Transfer fingerprint or arithmetic differs')
            in_period = [start <= datetime.fromisoformat(leg['date']) < end for leg in live]
            if all(cash):
                kind = 'cash_transfer' if all(in_period) else 'cash_transfer_timing'
            elif any(cash) and any(brokerage):
                kind = 'investment_transfer'
            elif any(cash):
                kind = 'card_settlement'
            else:
                continue
            for leg, inside in zip(live, in_period):
                if inside:
                    result[leg['journal_id']] = dict(kind=kind, match_id=match.id)
        except (KeyError, ValueError, TypeError, AttributeError):
            issues.append(dict(kind='invalid_transfer_evidence', match_id=match.id))
    return result, issues


def _statement_valid(session, anchor):
    """Check only this anchor's retained document; return no document contents."""
    try:
        document = session.query(AccountingEvidence).get(anchor.evidence_id)
        if (document is None or document.kind != 'bank_statement' or
                sha256(document.payload.encode()).hexdigest() != document.sha256):
            return False
        body, metadata = json.loads(document.payload), json.loads(anchor.details)
        pdf = base64.b64decode(body['pdf'], validate=True)
        return bool(body['metadata'] == metadata and pdf.startswith(b'%PDF-') and
            len(pdf) <= 10 * 1024 * 1024 and sha256(pdf).hexdigest() == metadata['pdf_sha256'] and
            int(metadata['account_id']) == anchor.account_id and
            date.fromisoformat(metadata['start']) == anchor.start_date and
            date.fromisoformat(metadata['end']) == anchor.end_date and
            anchor.start_date <= anchor.end_date and _finite(anchor.opening) and _finite(anchor.closing) and
            Decimal(metadata['opening']) == anchor.opening and Decimal(metadata['closing']) == anchor.closing and
            Decimal(metadata['movement']) == anchor.closing - anchor.opening and metadata.get('verification'))
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, binascii.Error):
        return False


def _opening_checks_valid(session, row, details):
    """Replay the source evidence over each original anchor's complete window.

    An anchor can postdate the report end. Its previously reviewed backcalculation
    still determines beginning cash, so that later evidence must be revalidated.
    Ordinary activity after the anchor date is intentionally outside this proof.
    """
    checks = details['checks']
    if (not isinstance(checks, list) or not checks or
            len({check['statement_id'] for check in checks}) != len(checks)):
        return False
    directly_documented = False
    for check in checks:
        anchor = session.query(BankStatements).get(check['statement_id'])
        if (anchor is None or anchor.account_id != row.account_id or
                anchor.evidence_id != check['evidence_id'] or
                anchor.end_date != date.fromisoformat(check['end']) or anchor.end_date < row.cutover or
                anchor.closing != Decimal(check['closing']) or not _statement_valid(session, anchor)):
            return False
        sources = [dict(source) for source in session.execute(text('''
            SELECT i.public_id AS id,t.dtposted AS date,t.trnamt AS amount,
                   concat(t.name,t.memo) AS description
            FROM ofx.stmttrn t LEFT JOIN bookkeeping.ofx_source_identities i
              ON i.acctfrom_id=t.acctfrom_id AND i.fitid=t.fitid
            WHERE t.acctfrom_id=:aid AND t.dtposted>=:start AND t.dtposted<:end
            ORDER BY i.public_id'''), dict(aid=row.account_id,
                start=datetime.combine(row.cutover, time.min),
                end=datetime.combine(anchor.end_date + timedelta(days=1), time.min)))]
        if (any(source['id'] is None or not _finite(source['amount']) for source in sources) or
                len(sources) != check['source_count'] or _digest(sources) != check['source_digest']):
            return False
        movement = sum((source['amount'] for source in sources), ZERO)
        if (movement != Decimal(check['movement']) or
                anchor.closing - movement != row.amount or
                Decimal(check['implied_opening']) != row.amount):
            return False
        directly_documented = directly_documented or (
            anchor.start_date == row.cutover and anchor.opening == row.amount)
    return bool((len(checks) >= 2 or directly_documented) and
                details['directly_documented'] == directly_documented)


def _opening(session, row, name):
    if row is None:
        return dict(valid=False, cutover=None, amount=None)
    try:
        details = json.loads(row.details)
        valid = (details['account'] == name and details['account_id'] == row.account_id and
                 details['cutover'] == str(row.cutover) and Decimal(details['amount']) == row.amount and
                 details['digest'] == row.evidence_digest and
                 _digest({k: v for k, v in details.items() if k != 'digest'}) == row.evidence_digest)
        entry = session.query(JournalEntries).get(row.journal_id) if row.journal_id else None
        if row.amount == 0:
            valid = valid and row.journal_id is None
        else:
            debit, credit = (name, 'Personal Equity') if row.amount > 0 else ('Personal Equity', name)
            when = datetime.combine(row.cutover, time.min, timezone.utc) - timedelta(microseconds=1)
            valid = bool(valid and entry and entry.transaction_source == 'statement_opening' and
                entry.transaction_id == str(row.account_id) and entry.functional_amount == abs(row.amount) and
                entry.source_amount == abs(row.amount) and entry.source_currency == 'USD' and
                entry.functional_currency == 'USD' and entry.debit_subaccount == debit and
                entry.credit_subaccount == credit and _utc(entry.timestamp) == when)
        valid = bool(valid and _opening_checks_valid(session, row, details))
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, binascii.Error):
        valid = False
    return dict(valid=valid, cutover=row.cutover, amount=row.amount)


def cash_flow_context(session, start, end):
    """Return transfer, coverage, classification and source integrity evidence.

    Boundaries are UTC-aware inclusive/exclusive instants. Coverage is inclusive
    calendar-date coverage, with opening-supported inception shown separately.
    Returned names are bookkeeping account names; no bank account numbers,
    credentials, raw documents or owner facts are returned.
    """
    start, end = _utc(start), _utc(end)
    if start >= end:
        raise ValueError('Cash-flow end must follow start')
    chart, accounts = _chart(session), _accounts(session)
    cash_names = {name for name, data in chart.items() if data['is_cash']}
    journal_rows = session.query(JournalEntries).filter(JournalEntries.timestamp < end,
        or_(JournalEntries.debit_subaccount.in_(cash_names),
            JournalEntries.credit_subaccount.in_(cash_names))).all()
    active = {name for entry in journal_rows for name in
              (entry.debit_subaccount, entry.credit_subaccount) if name in cash_names}
    source_rows = [dict(row) for row in session.execute(text('''
        SELECT i.public_id AS id,i.source_id,t.acctfrom_id AS account_id,
               t.dtposted AS date,t.trnamt AS amount,a.name AS account,b.currency
        FROM ofx.stmttrn t JOIN ofx.acctfrom a ON a.id=t.acctfrom_id
        LEFT JOIN bookkeeping.ofx_source_identities i
          ON i.acctfrom_id=t.acctfrom_id AND i.fitid=t.fitid
        LEFT JOIN bookkeeping.bank_feed_accounts b ON b.ofx_account_id=t.acctfrom_id
        WHERE a.name=ANY(:names) AND (t.dtposted<:end OR t.dtposted IS NULL)'''),
        {'names': list(cash_names), 'end': end.replace(tzinfo=None)})]
    active.update(row['account'] for row in source_rows)
    # A registered cash feed with no imported rows is an empty coverage claim,
    # not evidence that the household had no cash activity in that account.
    active.update(acct['name'] for acct in accounts.values()
                  if acct['name'] in cash_names and acct['currency'] is not None)
    active_accounts = {aid: acct for aid, acct in accounts.items() if acct['name'] in active}
    coverage, openings, source_checks, statement_checks, issues = [], [], [], [], []
    transfers, transfer_issues = _transfers(session, start, end, chart, accounts)
    classifications, classification_issues = _classifications(session, start, end)
    issues.extend(transfer_issues + classification_issues)
    for name in sorted(active - {acct['name'] for acct in active_accounts.values()}):
        coverage.append(dict(account=name, account_id=None, currency=None, ranges=[],
            gaps=[(start.date(), (end - timedelta(microseconds=1)).date())],
            invalid=True, complete=False, reason='Cash account has no registered OFX feed'))
        issues.append(dict(kind='cash_account_without_feed', account=name))
    source_ids = {row['id'] for row in source_rows if row['id']}
    # Include wrongly dated and wrongly directed journal allocations, even if the
    # journal no longer names its source cash account or falls outside the period.
    allocations = defaultdict(list)
    for entry in session.query(JournalEntries).filter(JournalEntries.transaction_source == 'ofx',
            JournalEntries.transaction_id.in_(source_ids)):
        allocations[entry.transaction_id].append(entry)
    associations = _associations(session, [entry.id for group in allocations.values() for entry in group])
    cash_ofx_entries = [entry for entry in journal_rows if entry.transaction_source == 'ofx']
    journal_sources = _sources(session, {entry.transaction_id for entry in cash_ofx_entries})
    associations.update(_associations(session, [entry.id for entry in cash_ofx_entries]))
    imports = defaultdict(list)
    for row in session.query(OFXFileImports).filter(OFXFileImports.ofx_account_id.in_(active_accounts)):
        imports[row.ofx_account_id].append(row)
    verified = defaultdict(list)
    import_ids = [row.id for group in imports.values() for row in group]
    for row in session.query(ChaseExportCoverage).filter(ChaseExportCoverage.file_import_id.in_(import_ids)):
        verified[row.file_import_id].append((row.start, row.end))
    for aid, acct in sorted(active_accounts.items()):
        name = acct['name']
        opening = _opening(session, session.query(OpeningBalances).get(aid), name)
        opening.update(account_id=aid, account=name,
                       covers_start=bool(opening['valid'] and opening['cutover'] <= start.date()))
        openings.append(opening)
        intervals, invalid = [], False
        for source in imports[aid]:
            if source.id in verified:
                intervals.extend(verified[source.id])
            elif source.coverage_start and source.coverage_end and source.coverage_start <= source.coverage_end:
                intervals.append((_utc(source.coverage_start).date(), _utc(source.coverage_end).date()))
            else:
                invalid = True
        try:
            ranges = merge_ranges(intervals)
        except ValueError:
            ranges, invalid = [], True
        gaps = missing_ranges(ranges, start.date(), (end - timedelta(microseconds=1)).date())
        beginning_gaps = (missing_ranges(ranges, opening['cutover'],
                                        (start - timedelta(microseconds=1)).date())
                          if opening['valid'] and opening['cutover'] <= start.date() else [])
        opening['coverage_gaps'] = beginning_gaps
        opening['covers_start'] = bool(opening['covers_start'] and not invalid and not beginning_gaps)
        if beginning_gaps or (opening['valid'] and invalid):
            issues.append(dict(kind='opening_coverage', account_id=aid))
        account_type_valid = not acct['is_card'] and chart[name]['element'] == 'Assets'
        coverage.append(dict(account_id=aid, account=name, currency=acct['currency'],
            ranges=ranges, gaps=gaps, invalid=invalid,
            complete=not invalid and not gaps and acct['currency'] == 'USD' and account_type_valid,
            is_card=acct['is_card'], account_type_valid=account_type_valid,
            latest_statement_date=None))
        selected = [row for row in source_rows if row['account_id'] == aid and
                    row['date'] is not None and start <= _utc(row['date']) < end]
        unposted, invalid_sources, invalid_journals = [], [], set()
        for source in selected:
            linked = allocations[source['id']]
            if not linked:
                unposted.append(source['id'])
            if (not source['id'] or not _finite(source['amount']) or not source['amount'] or
                    source['currency'] != 'USD' or
                    any(not _finite(row.functional_amount) for row in linked) or
                    sum((row.functional_amount for row in linked), ZERO) != abs(source['amount'])):
                invalid_sources.append(source['id'])
                invalid_journals.update(row.id for row in linked)
            if not _finite(source['amount']):
                continue
            for entry in linked:
                bank_side, other = ((entry.debit_subaccount, entry.credit_subaccount)
                                   if source['amount'] > 0 else (entry.credit_subaccount, entry.debit_subaccount))
                if (bank_side != name or other == name or
                        entry.functional_currency != 'USD' or entry.source_currency != 'USD' or
                        entry.source_amount != entry.functional_amount or
                        _utc(entry.timestamp) != _utc(source['date']) or
                        associations.get(entry.id) != source['source_id']):
                    invalid_journals.add(entry.id)
        invalid_sources.extend(row['id'] for row in source_rows if row['account_id'] == aid and row['date'] is None)
        booked_entries = [entry for entry in journal_rows if entry.transaction_source == 'ofx' and
                          start <= _utc(entry.timestamp) < end and name in
                          (entry.debit_subaccount, entry.credit_subaccount)]
        for entry in booked_entries:
            source = journal_sources.get(entry.transaction_id)
            if (not source or source['account'] != name or source['date'] is None or
                    _utc(source['date']) != _utc(entry.timestamp) or
                    not _finite(entry.functional_amount) or
                    associations.get(entry.id) != source['source_id']):
                invalid_journals.add(entry.id)
        if (any(not _finite(row['amount']) for row in selected) or
                any(not _finite(entry.functional_amount) for entry in booked_entries)):
            raise ValueError('Cash-flow arithmetic requires finite source and journal amounts')
        booked = sum((entry.functional_amount * ((entry.debit_subaccount == name) -
                     (entry.credit_subaccount == name)) for entry in booked_entries), ZERO)
        source_movement = sum((row['amount'] for row in selected), ZERO)
        source_checks.append(dict(account_id=aid, account=name,
            source_movement=source_movement, booked_ofx_movement=booked,
            difference=booked - source_movement, source_count=len(selected),
            journal_count=len(booked_entries), unposted_sources=unposted,
            invalid_source_ids=invalid_sources, invalid_journal_ids=sorted(invalid_journals)))
        if unposted or invalid_sources or invalid_journals or booked != source_movement:
            issues.append(dict(kind='cash_source_integrity', account_id=aid))
        if opening['valid'] and opening['cutover'] <= start.date():
            cutover = datetime.combine(opening['cutover'], time.min, timezone.utc)
            historical = [row for row in source_rows if row['account_id'] == aid and
                          row['date'] is not None and cutover <= _utc(row['date']) < start]
            if any(not _finite(row['amount']) for row in historical):
                raise ValueError('Beginning cash requires finite source amounts')
            historical_movement = sum((row['amount'] for row in historical), ZERO)
            booked_beginning = sum((entry.functional_amount * ((entry.debit_subaccount == name) -
                (entry.credit_subaccount == name)) for entry in journal_rows
                if _utc(entry.timestamp) < start), ZERO)
            opening['source_movement_before_period'] = historical_movement
            opening['source_supported_beginning'] = opening['amount'] + historical_movement
            opening['booked_beginning'] = booked_beginning
            opening['beginning_difference'] = booked_beginning - opening['source_supported_beginning']
            historical_unposted, historical_invalid, historical_journals = [], [], set()
            for source in historical:
                linked = allocations[source['id']]
                if not linked:
                    historical_unposted.append(source['id'])
                source_invalid = (not source['id'] or source['currency'] != 'USD' or
                    any(not _finite(entry.functional_amount) for entry in linked) or
                    sum((entry.functional_amount for entry in linked), ZERO) != abs(source['amount']))
                if source_invalid:
                    historical_invalid.append(source['id'])
                    historical_journals.update(entry.id for entry in linked)
                for entry in linked:
                    bank_side, other = ((entry.debit_subaccount, entry.credit_subaccount)
                        if source['amount'] > 0 else (entry.credit_subaccount, entry.debit_subaccount))
                    if (bank_side != name or other == name or entry.functional_currency != 'USD' or
                            entry.source_currency != 'USD' or entry.source_amount != entry.functional_amount or
                            _utc(entry.timestamp) != _utc(source['date']) or
                            associations.get(entry.id) != source['source_id']):
                        historical_journals.add(entry.id)
            opening['unposted_source_ids'] = historical_unposted
            opening['invalid_source_ids'] = historical_invalid
            opening['invalid_journal_ids'] = sorted(historical_journals)
            if historical_unposted or historical_invalid or historical_journals:
                opening['covers_start'] = False
                issues.append(dict(kind='opening_source_integrity', account_id=aid))
            if opening['beginning_difference']:
                opening['covers_start'] = False
                issues.append(dict(kind='opening_source_movement', account_id=aid))
        for anchor in session.query(BankStatements).filter(BankStatements.account_id == aid,
                BankStatements.end_date < end.date()):
            if opening['valid'] and anchor.end_date < opening['cutover']:
                issues.append(dict(kind='statement_before_ledger_cutover',
                                   account_id=aid, statement_id=anchor.id))
                continue
            if not _statement_valid(session, anchor):
                issues.append(dict(kind='invalid_statement_evidence',
                                   account_id=aid, statement_id=anchor.id))
                continue
            cutoff = datetime.combine(anchor.end_date + timedelta(days=1), time.min, timezone.utc)
            booked_balance = sum((entry.functional_amount * ((entry.debit_subaccount == name) -
                (entry.credit_subaccount == name)) for entry in journal_rows
                if _utc(entry.timestamp) < cutoff), ZERO)
            if booked_balance != anchor.closing:
                statement_checks.append(dict(statement_id=anchor.id, account_id=aid,
                    account=name, end_date=anchor.end_date, statement_balance=anchor.closing,
                    booked_balance=booked_balance, difference=booked_balance - anchor.closing))
            elif (coverage[-1]['latest_statement_date'] is None or
                  anchor.end_date > coverage[-1]['latest_statement_date']):
                coverage[-1]['latest_statement_date'] = anchor.end_date
    return dict(transfer_kinds=transfers, classifications=classifications,
        coverage=coverage, opening_checks=openings, source_checks=source_checks,
        statement_disagreements=statement_checks, issues=issues)
