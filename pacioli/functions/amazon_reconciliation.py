"""Evidence-only Amazon settlement matching; never writes financial records."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonEvidenceBundles
from pacioli.functions.amazon_csv import csv_bytes
from pacioli.functions.amazon_normalization import normalize_tables
from pacioli.functions.reconciliation import digest, dump, evidence, money, utc


VERSION = 'amazon-settlement-review-v1'
STATUSES = (
    ('unique_candidate', 'Unique candidate — review required'),
    ('already_booked', 'Already booked'),
    ('ambiguous', 'Ambiguous match'),
    ('needs_evidence', 'More evidence needed'),
    ('unmatched', 'No matching bank transaction'),
    ('outside_bank_history', 'Outside recorded bank dates'),
)
PAGE_SIZE = 25


def match_records(records, transactions, accounts, booked):
    """Exact cents/sign, bounded dates, known account, and mutual uniqueness.

    A candidate is not an approved financial match. Blocked records still
    compete for transactions so missing evidence cannot create false uniqueness.
    Previously allocated transactions remain reserved throughout matching.
    """
    result = deepcopy(records)
    txs = {str(t['id']): deepcopy(t) for t in transactions}
    if len(txs) != len(transactions) or len({r['key'] for r in records}) != len(records):
        raise ValueError('Duplicate source identities in reconciliation inputs')
    account_map = {a['id']: a for a in accounts}
    reservations = defaultdict(list)
    for bundle in booked:
        reservations[str(bundle['transaction_id'])].append(bundle)
    by_amount = defaultdict(list)
    for t in txs.values():
        t['id'] = str(t['id'])
        t['date'] = str(t['date'])[:10]
        t['existing_bundle_id'] = (reservations[t['id']][0]['bundle_id']
                                   if reservations[t['id']] else None)
        if t.get('currency') == 'USD':
            by_amount[money(t['amount'])].append(t)
    claimants = defaultdict(set)
    for r in result:
        day = date.fromisoformat(r['date'])
        card = r.get('card_last4')
        candidates = []
        if r['currency'] == 'USD':
            for t in by_amount[-money(r['amount'])]:
                if card and t.get('card_last4') != card:
                    continue
                if abs((date.fromisoformat(t['date']) - day).days) <= 7:
                    candidates.append(t)
                    claimants[t['id']].add(r['key'])
        r['candidates'] = sorted(candidates, key=lambda t: (t['date'], t['id']))
    for r in result:
        reasons = [code.replace('_', ' ').capitalize() for code in r.get('blockers', [])]
        card = r.get('card_last4')
        matching_accounts = [a for a in accounts if a.get('currency') == 'USD'
                             and (not card or a.get('last4') == card)]
        candidates = r['candidates']
        reserved = [(t, reservations[t['id']]) for t in candidates if reservations[t['id']]]
        if r['currency'] != 'USD':
            r['status'] = 'needs_evidence'
            reasons.append('Source currency is not supported by this USD settlement review')
        elif reserved:
            exact = []
            for t, bundles in reserved:
                if len(bundles) == 1:
                    b = bundles[0]
                    if (b.get('valid') and set(b['order_ids']) == {r['order_id']}
                            and money(b['amount']) == money(r['amount'])
                            and t.get('journal_state') == 'allocated'):
                        exact.append(t)
            if len(exact) == 1 and len(candidates) == 1:
                r['status'] = 'already_booked'
                reasons.append('Existing reviewed allocation agrees with this order and bank settlement')
            else:
                r['status'] = 'needs_evidence'
                reasons.append('A possible bank settlement is already reserved by reviewed Amazon evidence')
        elif len(candidates) > 1 or any(len(claimants[t['id']]) > 1 for t in candidates):
            r['status'] = 'ambiguous'
            reasons.append('Multiple bank transactions or source records compete for this match')
        elif card and len(matching_accounts) > 1:
            r['status'] = 'ambiguous'
            reasons.append('The payment-card suffix identifies more than one imported account')
        elif not matching_accounts:
            r['status'] = 'needs_evidence'
            reasons.append('Payment account or currency is not registered in the imported bank history')
        elif reasons or not card:
            r['status'] = 'needs_evidence'
            if not card:
                reasons.append('Source export does not establish one payment-card account')
        elif not candidates:
            bounds = [(date.fromisoformat(a['first_date']), date.fromisoformat(a['last_date']))
                      for a in matching_accounts if a.get('first_date') and a.get('last_date')]
            day = date.fromisoformat(r['date'])
            outside = bounds and all(day < begin - timedelta(days=7) or day > end + timedelta(days=7)
                                     for begin, end in bounds)
            r['status'] = 'outside_bank_history' if outside else 'unmatched' if bounds else 'needs_evidence'
            reasons.append('No exact signed-amount match within seven days in the recorded accounts'
                           if bounds else 'No bank transaction dates are available for this account')
        else:
            candidate = candidates[0]
            account = account_map.get(candidate['account_id'])
            if (not account or account.get('currency') != 'USD'
                    or account.get('last4') != card or not candidate.get('suspense')
                    or candidate.get('journal_state') != 'suspense'):
                r['status'] = 'needs_evidence'
                reasons.append('Current bank journal is unposted, already classified, or inconsistent with its source')
            elif candidate.get('merchant_warning'):
                r['status'] = 'needs_evidence'
                reasons.append('Bank description may represent a different Amazon service or payment type')
            else:
                r['status'] = 'unique_candidate'
                reasons.append('Exact signed amount, matching payment card, bounded date and no competing source record')
        r['reasons'] = reasons
    unmatched = [txs[key] for key in sorted(txs) if not claimants[key] and not reservations[key]]
    return dict(records=result, unmatched_bank=unmatched,
                summary=dict(records=len(result), status_counts=dict(Counter(r['status'] for r in result)),
                             unmatched_bank=len(unmatched)))


def _source_tables(session, identifier):
    doc = session.query(AccountingEvidence).get(identifier)
    if not doc or doc.kind != 'amazon_csv_import':
        raise ValueError('Select a saved Amazon source archive')
    manifest = json.loads(doc.payload)
    if digest(manifest) != doc.sha256:
        raise ValueError('Archive manifest evidence changed')
    tables = []
    for entry in manifest['tables']:
        source = session.query(AccountingEvidence).get(entry['evidence_id'])
        if not source or source.kind != 'amazon_csv_table' or digest(json.loads(source.payload)) != source.sha256:
            raise ValueError('Saved source table evidence is missing or changed')
        body = json.loads(source.payload)
        if (len(body['rows']) != entry['rows'] or body['headers'] != entry['columns']
                or body.get('source_sha256') != entry.get('source_sha256')):
            raise ValueError('Source table differs from its archive manifest')
        tables.append(dict(evidence_id=source.id, filename=entry['filename'],
                           headers=body['headers'], rows=body['rows']))
    return manifest, tables


def _bank_inputs(session):
    accounts = [dict(row) for row in session.execute(text('''
        SELECT a.id, a.name, f.currency,
            right(regexp_replace(COALESCE(c.acctid,b.acctid,''),'[^0-9]','','g'),4) AS last4,
            min(t.date)::date::text AS first_date, max(t.date)::date::text AS last_date
        FROM ofx.acctfrom a LEFT JOIN ofx.ccacctfrom c ON c.id=a.id
        LEFT JOIN ofx.bankacctfrom b ON b.id=a.id
        LEFT JOIN bookkeeping.bank_feed_accounts f ON f.ofx_account_id=a.id
        LEFT JOIN ofx.transactions t ON t.account_id=a.id
        GROUP BY a.id,a.name,f.currency,c.acctid,b.acctid ORDER BY a.id'''))]
    account_map = {a['id']: a for a in accounts}
    transactions = [dict(row) for row in session.execute(text('''
        SELECT id,date,amount,account,account_id,description FROM ofx.transactions
        WHERE description ILIKE '%AMAZON%' OR description ILIKE '%AMZN%' ORDER BY id'''))]
    journals = defaultdict(list)
    for row in session.execute(text('''SELECT j.* FROM bookkeeping.journal_entries j
        WHERE j.transaction_source='ofx' AND j.transaction_id IN (
          SELECT id FROM ofx.transactions WHERE description ILIKE '%AMAZON%' OR description ILIKE '%AMZN%')
        ORDER BY j.id''')):
        journals[row.transaction_id].append(dict(row))
    for t in transactions:
        a = account_map[t['account_id']]
        t['currency'], t['card_last4'] = a['currency'], a['last4'] or None
        entries = journals[t['id']]
        debit = money(t['amount']) > 0
        categories = [j['credit_subaccount'] if debit else j['debit_subaccount'] for j in entries]
        consistent = (bool(entries) and a['currency'] == 'USD'
            and sum((money(j['functional_amount']) for j in entries), money('0')) == abs(money(t['amount']))
            and all((j['debit_subaccount'] if debit else j['credit_subaccount']) == t['account']
                    and j['source_currency'] == 'USD' and j['source_amount'] == j['functional_amount']
                    and utc(j['timestamp']) == utc(t['date']) for j in entries))
        suspense = (consistent and len(entries) == 1 and categories == ['Bookkeeping Suspense']
                    and entries[0]['allocation_key'] == '0')
        t['suspense'] = suspense
        t['journal_state'] = ('unposted' if not entries else 'inconsistent' if not consistent
                              else 'suspense' if suspense else 'allocated')
        t['category'] = ', '.join(sorted(set(categories))) or 'Unposted'
        t['merchant_warning'] = bool(re.search(r'AMAZON (?:WEB|PAY|STORE CARD)|GIFT CARD|\bAWS\b',
                                              t['description'] or '', re.I))
        t['source_timestamp'] = utc(t['date']).isoformat()
        t['date'], t['amount'] = t['date'].date().isoformat(), str(t['amount'])
    tx_map = {t['id']: t for t in transactions}
    allocation_proofs = defaultdict(list)
    for proof in session.query(AccountingEvidence).filter_by(kind='amazon_allocation'):
        payload = json.loads(proof.payload)
        if digest(payload) == proof.sha256:
            preview = payload.get('preview', {})
            allocation_proofs[preview.get('bundle_id')].append(preview)
    booked = []
    for b in session.query(AmazonEvidenceBundles).filter(AmazonEvidenceBundles.transaction_id.isnot(None)):
        body = json.loads(b.details)
        total = sum((money(line['amount']) for line in body['lines']), money('0'))
        t = tx_map.get(b.transaction_id)
        proven = False
        for preview in allocation_proofs[b.id]:
            source = preview.get('source', {})
            allocations = preview.get('allocations', [])
            entries = sorted(journals[b.transaction_id], key=lambda j: j['allocation_key'])
            if (not t or not b.posting_digest or preview.get('digest') != b.posting_digest
                    or digest({k: v for k, v in preview.items() if k != 'digest'}) != b.posting_digest
                    or preview.get('evidence_digest') != digest(body)
                    or source.get('id') != b.transaction_id or money(source.get('amount', '0')) != -total
                    or source.get('account') != t['account'] or source.get('account_id') != t['account_id']
                    or utc(datetime.fromisoformat(source.get('date', ''))).isoformat() != t['source_timestamp']
                    or source.get('description') != t['description']
                    or len(entries) != len(allocations) or not entries):
                continue
            debit = money(t['amount']) > 0
            if all(j['allocation_key'] == 'amazon:{:03d}'.format(index)
                   and (j['credit_subaccount'] if debit else j['debit_subaccount']) == allocation['category']
                   and money(j['functional_amount']) == money(allocation['amount'])
                   for index, (j, allocation) in enumerate(zip(entries, allocations))):
                proven = True
        booked.append(dict(transaction_id=b.transaction_id, bundle_id=b.id,
            order_ids=sorted({line['order_id'] for line in body['lines']}), amount=str(total),
            valid=bool(proven and t and t['journal_state'] == 'allocated'
                       and money(t['amount']) == -total)))
    return accounts, transactions, booked, journals


def build_report(session, identifier):
    manifest, tables = _source_tables(session, identifier)
    normalized = normalize_tables(tables)
    if not normalized['records'] and not any(row.get('kind') for row in normalized['exclusions']):
        raise ValueError('This export has no supported purchase or refund tables')
    accounts, transactions, booked, journals = _bank_inputs(session)
    report = match_records(normalized['records'], transactions, accounts, booked)
    excluded_rows = Counter()
    for row in normalized['exclusions']:
        excluded_rows[row['reason']] += len(row['source_rows'])
    report['summary']['exclusion_counts'] = dict(excluded_rows)
    report.update(version=VERSION, normalization_version=normalized['version'], archive_id=identifier,
                  archive_filename=manifest['filename'], archive_sha256=manifest['source_sha256'],
                  normalization_summary=normalized['summary'], exclusions=normalized['exclusions'],
                  accounts=accounts, bank_input_digest=digest(dict(accounts=accounts, transactions=transactions,
                                                                booked=booked, journals=journals)))
    return report


def build_and_save(identifier, reviewer):
    identifier = _positive_integer(identifier, 'archive')
    # Authentication may already own db.session's transaction. Use a dedicated
    # PostgreSQL snapshot for the consistent read, then save only its evidence.
    with db.engine.connect() as connection:
        connection = connection.execution_options(isolation_level='REPEATABLE READ')
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False)
        try:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout='15000ms'"))
            report = build_report(session, identifier)
        finally:
            session.close()
            transaction.rollback()
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_reconciliation'))"))
    return evidence('amazon_reconciliation', report, reviewer)


def _positive_integer(value, label):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError('Select a valid ' + label) from None
    if isinstance(value, bool) or str(parsed) != str(value) or not 1 <= parsed <= 2147483647:
        raise ValueError('Select a valid ' + label)
    return parsed


def _saved_report(identifier):
    doc = db.session.query(AccountingEvidence).get(_positive_integer(identifier, 'reconciliation report'))
    if not doc or doc.kind != 'amazon_reconciliation':
        raise ValueError('Select a saved Amazon reconciliation report')
    body = json.loads(doc.payload)
    if digest(body) != doc.sha256:
        raise ValueError('Reconciliation evidence changed')
    return dict(id=doc.id, body=body)


def review_page(identifier=None, status='', page='1'):
    page = _positive_integer(page, 'page')
    if status not in dict(STATUSES) and status != '':
        raise ValueError('Select a valid match status')
    if identifier is None:
        latest = db.session.query(AccountingEvidence.id).filter_by(kind='amazon_reconciliation').order_by(
            AccountingEvidence.id.desc()).first()
        identifier = latest.id if latest else None
    report = _saved_report(identifier) if identifier is not None else None
    rows = report['body']['records'] if report else []
    if status:
        rows = [row for row in rows if row['status'] == status]
    priority = {key: index for index, (key, _) in enumerate(STATUSES)}
    rows = sorted(rows, key=lambda r: (priority[r['status']], -date.fromisoformat(r['date']).toordinal(), r['key']))
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        raise ValueError('Select a page within the reconciliation results')
    return dict(report=report, rows=rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], statuses=STATUSES,
                selected_status=status, page=page, pages=pages, total=len(rows))


def report_csv(identifier, part='records'):
    report = _saved_report(identifier)['body']
    if part == 'unmatched_bank':
        return csv_bytes(['Bank transaction', 'Date', 'Account', 'Currency', 'Signed bank amount',
                          'Description', 'Current allocation'],
                         [[t['id'], t['date'], t['account'], t['currency'], t['amount'],
                           t.get('description', ''), t['category']] for t in report['unmatched_bank']],
                         numeric=('Signed bank amount',))
    if part == 'exclusions':
        return csv_bytes(['Kind', 'Order ID', 'Reason', 'Source table:row'],
                         [[r.get('kind') or '', r.get('order_id') or '', r['reason'].replace('_', ' '),
                           '; '.join('{}:{}'.format(ref['table_id'], ref['row_number'])
                                     for ref in r['source_rows'])] for r in report['exclusions']])
    if part != 'records':
        raise ValueError('Select a supported reconciliation export')
    rows = []
    for r in report['records']:
        rows.append([r['key'], r['kind'], r['order_id'], r['date'], r['currency'], r['amount'],
                     r.get('card_last4') or '', dict(STATUSES)[r['status']],
                     '; '.join(str(t['id']) for t in r['candidates']), '; '.join(r['reasons']),
                     '; '.join('{}:{}'.format(ref['table_id'], ref['row_number']) for ref in r['source_rows'])])
    return csv_bytes(['Record', 'Kind', 'Order ID', 'Date', 'Currency', 'Purchase/refund amount',
                      'Card ending', 'Review status', 'Candidate bank IDs', 'Reasons', 'Source table:row'],
                     rows, numeric=('Purchase/refund amount',))
