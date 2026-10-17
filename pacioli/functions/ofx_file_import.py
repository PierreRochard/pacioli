"""One validated financial import boundary for downloaded and direct OFX."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from pacioli.extensions import db
from pacioli.models import (BankFeedAccounts, ConnectionResponses, Connections,
                            OFXFileImports, Subaccounts, ChaseExportCoverage)
from pacioli.functions import ofx_document
from pacioli.functions.chase_coverage import account_ranges, missing_ranges


MAX_OFX_BYTES = 10 * 1024 * 1024
CHASE_FEE_NAMES = frozenset(('FOREIGN TRANSACTION FEE', 'TRANSACTION FEE'))
CHASE_FEE_ID_PREFIX = 'pacioli:chase-fee:v1:'


class AccountNotApproved(ValueError):
    """A downloaded QFX belongs to an account outside the approved schedule."""
    code = 'account_not_approved'


class ImportRejected(ValueError):
    """A safe, actionable acceptance code without provider/account details."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


SUPPORTED_PRECISION = {'USD': Decimal('.01'), 'EUR': Decimal('.01'),
                       'BTC': Decimal('.00000001')}


REJECTIONS = {
    'unsupported_statement': 'The file must contain exactly one bank or credit-card statement',
    'unsuccessful_status': 'The OFX response reports an unsuccessful status',
    'currency_mismatch': 'OFX statement currency does not match the reviewed account currency',
    'account_identity_invalid': 'The OFX statement must identify exactly one account',
    'transaction_id_invalid': 'The QFX contains a missing or reserved transaction ID',
    'transaction_value_invalid': 'The OFX contains an invalid transaction date or amount',
    'transaction_detail_unsupported': 'The OFX contains transaction details Pacioli does not import',
    'ledger_balance_invalid': 'A valid {currency} posted ledger balance is required',
}


def parse_statement(content, currency='USD'):
    """The typed document; parser rejections become safe acceptance codes."""
    try:
        return ofx_document.parse(content)
    except ofx_document.OFXError as error:
        code = _rejection_code(error)
        message = REJECTIONS.get(code, 'The file is not a valid OFX statement')
        raise ImportRejected(code, message.format(currency=currency)) from None


def _rejection_code(error):
    """Name a parser rejection by the part of the statement at fault."""
    path = error.path.split('/')
    if error.code == 'header_invalid':
        return 'ofx_header_invalid'
    if len(path) == 2 and error.code == 'element_unsupported':
        return 'unsupported_statement'
    if 'STMTTRN' in path:
        return ('transaction_id_invalid' if path[-1] == 'FITID' else
                'transaction_detail_unsupported' if error.code == 'element_unsupported' else
                'transaction_value_invalid')
    if 'LEDGERBAL' in path:
        return 'ledger_balance_invalid'
    if 'BANKACCTFROM' in path or 'CCACCTFROM' in path:
        return 'account_identity_invalid'
    if path[-1] == 'CURDEF':
        return 'currency_mismatch'
    if 'STATUS' in path:
        return 'unsuccessful_status'
    return 'ofx_' + error.code


def _validate_envelope(document, currency, expected_account):
    """Validate transport-independent acceptance before creating financial rows.

    Connection settings describe expectations; they cannot relabel the document.
    Returns the one statement response.
    """
    if document.signon is None or document.signon.status is None:
        raise ImportRejected('missing_signon_status', 'The OFX response is missing its sign-on status')
    if len(document.responses) != 1:
        raise ImportRejected('unsupported_statement', 'The file must contain exactly one bank or credit-card statement')
    response = document.responses[0]
    if response.status is None:
        raise ImportRejected('missing_statement_status', 'The OFX response is missing its statement status')
    if not all(status.success for status in document.statuses):
        raise ImportRejected('unsuccessful_status', 'The OFX response reports an unsuccessful status')
    statement = response.statement
    if statement is None:
        raise ImportRejected('unsupported_statement', 'The file must contain exactly one bank or credit-card statement')
    if statement.currency != currency:
        raise ImportRejected('currency_mismatch', 'OFX statement currency does not match the reviewed account currency')
    account, bank = statement.account, response.kind == 'bank'
    if account is None or not account.acctid or (bank and not (account.bankid and account.accttype)):
        raise ImportRejected('account_identity_invalid', 'The OFX statement must identify exactly one account')
    identity = {'type': account.accttype if bank else 'CREDITCARD',
                'account_number': account.acctid,
                'routing_number': account.bankid if bank else None}
    if expected_account is not None and identity != expected_account:
        raise AccountNotApproved('The OFX response account does not match the requested account')
    return response


def _retain_response(content, connection_id):
    """Durably retain encrypted acquisition evidence, independently of acceptance."""
    with Session(bind=db.engine) as session:
        # Also serialize creation of the shared manual-file connection.
        session.execute(text('SELECT pg_advisory_xact_lock(724109, 2)'))
        if connection_id is None:
            connection = session.query(Connections).filter_by(source='ofx_file', org='Chase').first()
            if connection is None:
                connection = Connections(source='ofx_file', org='Chase', type='File Import',
                                         currency='USD', created_at=datetime.now(timezone.utc))
                session.add(connection)
                session.flush()
        else:
            connection = session.get(Connections, connection_id)
            if connection is None or connection.source != 'ofx':
                raise ValueError('A saved OFX connection is required')
        response = ConnectionResponses(connection_id=connection.id,
            connected_at=datetime.now(timezone.utc), response=content.decode('latin-1'),
            import_status='pending')
        session.add(response)
        session.flush()
        result = response.id, connection.id
        session.commit()
        return result


def _reject_response(response_id, error):
    with Session(bind=db.engine) as session:
        response = session.get(ConnectionResponses, response_id)
        response.import_status = 'rejected'
        # Raw provider messages can contain credentials or account identifiers.
        # The response itself is encrypted; diagnostics retain only a safe code.
        response.import_error = (getattr(error, 'code', None) or type(error).__name__)[:64]
        session.commit()


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _naive(value):
    # ofx.* timestamps are naive UTC.
    return None if value is None else _utc(value).replace(tzinfo=None)


def _prepare_transactions(document, currency='USD'):
    """Separate Chase card fees from purchases that reuse the same bank FITID.

    The original QFX is retained verbatim; only the transactions to store get
    the derived fee ID. Always derive it, including for fee-only exports, so
    overlap/order cannot change transaction identity. Unknown conflicting IDs
    must fail closed. Returns {fitid: fingerprint}, {alias: original FITID},
    and the transactions to store.
    """
    if len(document.responses) != 1 or document.responses[0].statement is None:
        raise ValueError('The file must contain exactly one bank or credit-card statement')
    response, signon = document.responses[0], document.signon
    statement = response.statement
    chase_card = (response.kind == 'creditcard' and signon is not None and
                  signon.org == 'B1' and signon.fid == '10898')
    expected, fee_aliases, transactions = {}, {}, []
    for transaction in (statement.transactions.transactions if statement.transactions else ()):
        fitid = transaction.fitid or ''
        if not fitid or fitid.startswith(CHASE_FEE_ID_PREFIX):
            raise ImportRejected('transaction_id_invalid', 'The QFX contains a missing or reserved transaction ID')
        if transaction.correctfitid is not None or transaction.correctaction is not None:
            raise ImportRejected('transaction_correction_unsupported',
                                 'The OFX corrects an earlier transaction; review it before importing')
        name = transaction.name or ''
        if chase_card and name.upper() in CHASE_FEE_NAMES:
            identity = json.dumps([fitid, name.upper()], separators=(',', ':'))
            alias = CHASE_FEE_ID_PREFIX + sha256(identity.encode('utf-8')).hexdigest()
            fee_aliases[alias] = fitid
            transaction = replace(transaction, fitid=alias)
            fitid = alias
        posted, amount = _utc(transaction.dtposted), transaction.trnamt
        if (posted is None or amount is None or transaction.trntype is None or
                amount != amount.quantize(SUPPORTED_PRECISION[currency])):
            raise ImportRejected('transaction_value_invalid', 'The OFX contains an invalid transaction date or amount')
        fingerprint = (posted, amount, transaction.trntype, name, transaction.memo or '')
        if fitid in expected and expected[fitid] != fingerprint:
            raise ImportRejected('transaction_id_conflict', 'The QFX reuses a transaction ID for conflicting records')
        expected[fitid] = fingerprint
        transactions.append(transaction)
    return expected, fee_aliases, transactions


def _ledger_balance(statement, currency='USD'):
    """The posted balance and its as-of time, which every import must report."""
    ledger = statement.ledger
    amount, asof = (ledger.amount, _utc(ledger.asof)) if ledger else (None, None)
    if amount is None or asof is None or amount != amount.quantize(SUPPORTED_PRECISION[currency]):
        raise ImportRejected('ledger_balance_invalid', 'A valid {} posted ledger balance is required'.format(currency))
    if asof.date() > datetime.now(timezone.utc).date():
        raise ImportRejected('ledger_balance_future', 'The posted balance date is in the future')
    return amount, asof


TRANSACTION_COLUMNS = ('trntype', 'dtposted', 'dtuser', 'dtavail', 'trnamt', 'srvrtid', 'checknum',
                       'refnum', 'sic', 'payeeid', 'name', 'memo', 'inv401ksource', 'curtype',
                       'cursym', 'currate')


def _transaction_row(transaction):
    currency = transaction.currency
    return {'trntype': transaction.trntype, 'dtposted': _naive(transaction.dtposted),
            'dtuser': _naive(transaction.dtuser), 'dtavail': _naive(transaction.dtavail),
            'trnamt': transaction.trnamt, 'srvrtid': transaction.srvrtid,
            'checknum': transaction.checknum, 'refnum': transaction.refnum, 'sic': transaction.sic,
            'payeeid': transaction.payeeid, 'name': transaction.name, 'memo': transaction.memo,
            'inv401ksource': transaction.inv401ksource,
            'curtype': currency.kind if currency else None,
            'cursym': currency.symbol if currency else None,
            'currate': currency.rate if currency else None}


def _store_account(session, response):
    """The ofx.acctfrom id of the statement's account, created on first import."""
    account, bank = response.statement.account, response.kind == 'bank'
    if bank:
        row = session.execute(text('SELECT id, accttype FROM ofx.bankacctfrom WHERE bankid=:bankid AND acctid=:acctid'),
                              {'bankid': account.bankid, 'acctid': account.acctid}).first()
        if row is not None and row.accttype != account.accttype:
            raise ImportRejected('account_identity_invalid', 'The OFX account type differs from the stored account')
    else:
        row = session.execute(text('SELECT id FROM ofx.ccacctfrom WHERE acctid=:acctid'),
                              {'acctid': account.acctid}).first()
    if row is not None:
        return row.id
    account_id = session.execute(text('INSERT INTO ofx.acctfrom (subclass) VALUES (:subclass) RETURNING id'),
                                 {'subclass': 'bankacctfrom' if bank else 'ccacctfrom'}).scalar_one()
    if bank:
        session.execute(text("""
            INSERT INTO ofx.bankacctfrom (id, bankid, branchid, acctid, accttype, acctkey)
            VALUES (:id, :bankid, :branchid, :acctid, CAST(:accttype AS ofx.accttype), :acctkey)
        """), {'id': account_id, 'bankid': account.bankid, 'branchid': account.branchid,
               'acctid': account.acctid, 'accttype': account.accttype, 'acctkey': account.acctkey})
    else:
        session.execute(text('INSERT INTO ofx.ccacctfrom (id, acctid, acctkey) VALUES (:id, :acctid, :acctkey)'),
                        {'id': account_id, 'acctid': account.acctid, 'acctkey': account.acctkey})
    return account_id


def _store_transactions(session, account_id, transactions):
    """Insert new FITIDs; an existing FITID must match its stored row exactly.

    Returns how many transactions were inserted.
    """
    if not transactions:
        return 0
    stored = {row.fitid: {column: getattr(row, column) for column in TRANSACTION_COLUMNS}
              for row in session.execute(text(
                  'SELECT fitid, {} FROM ofx.stmttrn WHERE acctfrom_id=:account_id AND fitid=ANY(:fitids)'
                  .format(', '.join(TRANSACTION_COLUMNS))),
                  {'account_id': account_id, 'fitids': [t.fitid for t in transactions]})}
    inserts = []
    for transaction in transactions:
        row = _transaction_row(transaction)
        if transaction.fitid in stored:
            if stored[transaction.fitid] != row:
                raise ImportRejected('transaction_id_conflict', 'An imported transaction ID conflicts with its stored record')
            continue
        stored[transaction.fitid] = row
        inserts.append(dict(row, acctfrom_id=account_id, fitid=transaction.fitid))
    if inserts:
        session.execute(text("""
            INSERT INTO ofx.stmttrn (acctfrom_id, fitid, trntype, dtposted, dtuser, dtavail, trnamt,
                                     srvrtid, checknum, refnum, sic, payeeid, name, memo,
                                     inv401ksource, curtype, cursym, currate)
            VALUES (:acctfrom_id, :fitid, CAST(:trntype AS ofx.trntype), :dtposted, :dtuser, :dtavail,
                    :trnamt, :srvrtid, :checknum, :refnum, :sic, :payeeid, :name, :memo,
                    CAST(:inv401ksource AS ofx.inv401ksource), CAST(:curtype AS ofx.curtype),
                    CAST(:cursym AS ofx.cursym), :currate)
        """), inserts)
    return len(inserts)


def _check_legacy_fee_ids(session, account_id, fee_aliases):
    for original in set(fee_aliases.values()):
        legacy = session.execute(text('SELECT name FROM ofx.stmttrn WHERE acctfrom_id=:account_id AND fitid=:fitid'),
                                 {'account_id': account_id, 'fitid': original}).first()
        if legacy is not None and (legacy.name or '').upper() in CHASE_FEE_NAMES:
            raise ImportRejected('legacy_fee_id_conflict', 'Previously imported Chase fee IDs need review before reimport')


def _store_balances(session, account_id, statement):
    """Keep the latest reported amount for each (account, as-of) balance.

    Every original value stays in the encrypted source and its import snapshot.
    """
    for table, code, balance in (('ledgerbal', 'ledger_balance_invalid', statement.ledger),
                                 ('availbal', 'available_balance_invalid', statement.available)):
        if balance is None:
            continue
        if balance.amount is None or balance.asof is None:
            raise ImportRejected(code, 'The OFX statement reports an incomplete balance')
        session.execute(text("""
            INSERT INTO ofx.{} (acctfrom_id, dtasof, balamt) VALUES (:account_id, :asof, :amount)
            ON CONFLICT (acctfrom_id, dtasof) DO UPDATE SET balamt = EXCLUDED.balamt
        """.format(table)), {'account_id': account_id, 'asof': _naive(balance.asof), 'amount': balance.amount})


def _account_name(session, account_id, response, institution='Chase'):
    name = session.execute(text('SELECT name FROM ofx.acctfrom WHERE id=:id'), {'id': account_id}).scalar_one()
    if name:
        return name
    account = response.statement.account
    last_four = account.acctid[-4:]
    if response.kind == 'creditcard':
        kind, parent = 'Credit Card', 'Accounts Payable'
    elif account.accttype == 'SAVINGS':
        kind, parent = 'Savings', 'Cash'
    else:
        kind, parent = 'Checking', 'Cash'
    name = '{} {} ({})'.format(institution, kind, last_four)
    existing = session.query(Subaccounts).filter_by(name=name).first()
    if existing is not None:
        name = '{} {} ({}; feed {})'.format(institution, kind, last_four, account_id)
    session.add(Subaccounts(name=name, parent=parent))
    session.execute(text('UPDATE ofx.acctfrom SET name=:name WHERE id=:id'), {'name': name, 'id': account_id})
    return name


def account_summary(account_id):
    """Return imported coverage and a provisional pre-history balance."""
    row = db.session.execute(text("""
        SELECT af.name, COUNT(st.fitid), MIN(st.dtposted), MAX(st.dtposted)
        FROM ofx.acctfrom af
        LEFT JOIN ofx.stmttrn st ON st.acctfrom_id = af.id
        WHERE af.id = :account_id
        GROUP BY af.id, af.name
    """), {'account_id': account_id}).one()
    # Source snapshots preserve same-as-of revisions. Older historical exports
    # and exact-file replays must not displace a newer reported balance.
    source = (db.session.query(OFXFileImports).filter_by(ofx_account_id=account_id)
              .order_by(OFXFileImports.balance_asof.desc(), OFXFileImports.id.desc()).first())
    intervals, invalid = account_ranges(account_id)
    coverage_start = datetime.combine(intervals[0][0], datetime.min.time(), timezone.utc) if intervals else None
    coverage_end = datetime.combine(intervals[-1][1], datetime.min.time(), timezone.utc) if intervals else None
    start = min(intervals[0][0], row[2].date()) if intervals and row[2] else intervals[0][0] if intervals else None
    asof = _utc(source.balance_asof) if source else None
    gaps = missing_ranges(intervals, start, asof.date()) if start and asof and start <= asof.date() else []
    complete = bool(start and asof and start <= asof.date() and not invalid and not gaps)
    result = {'account_id': account_id, 'account_name': row[0],
              'transaction_count': row[1], 'oldest': row[2], 'newest': row[3],
              'coverage_start': coverage_start, 'coverage_end': coverage_end,
              'coverage_contiguous': complete,
              'balance': source.balance_amount if source else None,
              'balance_asof': asof,
              'balance_imported_at': source.imported_at if source else None,
              'balance_source_sha256': source.sha256 if source else None,
              'balance_is_current': bool(asof and asof.date() == date.today()),
              'coverage_gaps': [{'start': a.isoformat(), 'end': b.isoformat()} for a, b in gaps],
              'opening_date': start,
              'opening_candidate': None, 'signed_activity': None, 'balance_disagreements': [],
              'reviewed_balance_cutoffs': [],
              'opening_blocker': ('Balance unavailable' if not source else
                                  'Coverage missing or invalid' if invalid or not start else
                                  'History starts after the balance date' if start > asof.date() else
                                  'Missing transaction coverage through the balance date' if gaps else None)}
    if complete:
        # OFX balance timestamps and posting dates are interpreted as UTC.
        # An end-of-day balance includes transactions on its as-of date.
        signed_activity = db.session.execute(text("""
            SELECT COALESCE(SUM(trnamt), 0) FROM ofx.stmttrn
            WHERE acctfrom_id = :account_id
              AND dtposted >= :start AND dtposted < :end
        """), {'account_id': account_id,
               # ofx.stmttrn timestamps are naive UTC. Bind naive boundaries
               # too, avoiding PostgreSQL's session-timezone coercion.
               'start': datetime.combine(start, datetime.min.time()),
               'end': datetime.combine(asof.date() + timedelta(days=1), datetime.min.time())}).scalar()
        result['signed_activity'] = Decimal(signed_activity)
        result['opening_candidate'] = Decimal(source.balance_amount) - Decimal(signed_activity)
        # A second reported date provides an independent reconciliation check.
        # Same-day revisions supersede earlier observations on that date under
        # the app's end-of-day OFX convention; their original sources remain.
        historical = db.session.execute(text("""
            SELECT DISTINCT ON ((balance_asof AT TIME ZONE 'UTC')::date)
                   balance_asof, balance_amount
            FROM admin.ofx_file_imports
            WHERE ofx_account_id=:account_id AND balance_asof >= :start
              AND (balance_asof AT TIME ZONE 'UTC')::date < :asof
            ORDER BY (balance_asof AT TIME ZONE 'UTC')::date, balance_asof DESC, id DESC
        """), {'account_id': account_id, 'start': datetime.combine(start, datetime.min.time(), timezone.utc),
               'asof': asof.date()}).all()
        from pacioli.functions.snapshot_reconciliation import reviewed
        reconciled, reviews = reviewed(db.session)
        result['reviewed_balance_cutoffs'] = [
            {'evidence_id': r['id'], 'asof': r['proof']['as_of'], 'cutoff': r['proof']['cutoff']}
            for r in reviews if r['active'] and r['valid'] and r['proof']['account_id'] == account_id]
        for prior_asof, prior_amount in historical:
            prior_asof = _utc(prior_asof)
            # Only a still-valid, explicitly reviewed original-file proof may
            # use a start-of-day cutoff for a particular historical snapshot.
            prior_end = prior_asof.date() if (account_id, prior_asof, prior_amount) in reconciled else prior_asof.date() + timedelta(days=1)
            prior_activity = db.session.execute(text("""
                SELECT COALESCE(SUM(trnamt), 0) FROM ofx.stmttrn
                WHERE acctfrom_id=:account_id AND dtposted >= :start AND dtposted < :end
            """), {'account_id': account_id, 'start': datetime.combine(start, datetime.min.time()),
                   'end': datetime.combine(prior_end, datetime.min.time())}).scalar()
            difference = Decimal(prior_amount) - Decimal(prior_activity) - result['opening_candidate']
            if difference:
                result['balance_disagreements'].append({'asof': prior_asof.date().isoformat(), 'difference': str(difference)})
        if result['balance_disagreements']:
            result['opening_candidate'] = None
            result['opening_blocker'] = 'Reported balance dates imply different opening balances; reconcile missing or corrected activity'
    return result


def public_account_summary(summary):
    """Explicit JSON representation: exact money strings, ISO dates, no full IDs."""
    return {key: value.isoformat() if isinstance(value, (date, datetime)) else
            str(value) if isinstance(value, Decimal) else value
            for key, value in summary.items()}


def chase_account_summaries():
    ids = (db.session.query(OFXFileImports.ofx_account_id)
           .distinct().order_by(OFXFileImports.ofx_account_id).all())
    return [account_summary(account_id) for account_id, in ids]


def _record_verified_interval(session, imported, interval):
    if interval is None:
        return
    left, right = interval
    if type(left) is not date or type(right) is not date or left > right:
        raise ValueError('Invalid verified export interval')
    if ((imported.first_transaction and imported.first_transaction.date() < left) or
            (imported.last_transaction and imported.last_transaction.date() > right)):
        raise ValueError('Transactions fall outside the verified export interval')
    session.flush()
    if not session.query(ChaseExportCoverage).filter_by(file_import_id=imported.id, start=left, end=right).first():
        session.add(ChaseExportCoverage(file_import_id=imported.id, start=left, end=right))


def import_chase_qfx(content, *, reviewed_usd, allowed_account_ids=None, verified_interval=None,
                     progress=lambda *args, **kwargs: None):
    """Reviewed manual/browser entry point into the common OFX importer."""
    if not reviewed_usd:
        raise ValueError('Confirm the Chase account and USD currency before importing')
    return import_ofx_statement(content, currency='USD', allowed_account_ids=allowed_account_ids,
                                verified_interval=verified_interval, progress=progress)


def import_ofx_statement(content, *, currency, connection_id=None, expected_account=None,
                         allowed_account_ids=None, verified_interval=None,
                         progress=lambda *args, **kwargs: None):
    """Atomically accept one statement; rejected evidence never creates sources.

    All transports share locks, validation, replay checks and provenance. A
    pending response survives a process crash without claiming financial success.
    """
    if currency not in SUPPORTED_PRECISION:
        raise ValueError('Set a supported OFX account currency before importing')
    if connection_id is not None and expected_account is None:
        raise ValueError('Direct OFX import requires an expected account')
    if not isinstance(content, bytes) or not content or len(content) > MAX_OFX_BYTES:
        raise ValueError('Choose an OFX/QFX file no larger than 10 MiB')
    if verified_interval is not None and (allowed_account_ids is None or len(allowed_account_ids) != 1):
        raise ValueError('Verified export coverage requires one approved account')
    digest = sha256(content).hexdigest()
    response_id, source_connection_id = _retain_response(content, connection_id)
    accepted = False
    with Session(bind=db.engine) as session:
        try:
            # Manual uploads and background replays must see each other's commits
            # before checking hashes or FITIDs. Locks release on commit/rollback.
            progress('import_lock')
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
            session.execute(text('SELECT pg_advisory_xact_lock(724109, 2)'))
            response = session.get(ConnectionResponses, response_id)
            connection = (session.query(Connections).filter_by(id=source_connection_id)
                          .with_for_update().one())
            if connection.currency != currency:
                raise ImportRejected('connection_changed', 'OFX connection currency changed before import')
            if connection_id is not None:
                configured_account = {
                    'type': 'CREDITCARD' if connection.type == 'Credit Card' else connection.type.upper(),
                    'account_number': connection.account_number,
                    'routing_number': None if connection.type == 'Credit Card' else connection.routing_number}
                if configured_account != expected_account:
                    raise ImportRejected('connection_changed', 'OFX connection account changed before import')
            progress('import_parse')
            document = parse_statement(content, currency)
            ofx_response = _validate_envelope(document, currency, expected_account)
            statement = ofx_response.statement
            expected, fee_aliases, transactions = _prepare_transactions(document, currency)
            balance_amount, balance_asof = _ledger_balance(statement, currency)

            progress('import_validate')
            account_id = _store_account(session, ofx_response)
            new_transactions = _store_transactions(session, account_id, transactions)
            _check_legacy_fee_ids(session, account_id, fee_aliases)
            if allowed_account_ids is not None and account_id not in allowed_account_ids:
                raise AccountNotApproved('QFX account is not approved for automatic import')
            registration = session.get(BankFeedAccounts, account_id)
            if registration is not None and registration.currency != currency:
                raise ImportRejected('currency_registration_conflict', 'This feed has a conflicting currency registration')
            if registration is None:
                session.add(BankFeedAccounts(ofx_account_id=account_id, currency=currency))

            progress('import_deduplicate')
            prior = session.query(OFXFileImports).filter_by(sha256=digest).one_or_none()
            if prior is not None:
                if prior.ofx_account_id != account_id:
                    raise ImportRejected('import_identity_changed', 'The imported statement account identity has changed')
                _record_verified_interval(session, prior, verified_interval)
                response.import_status = 'accepted'
                if connection_id is not None:
                    connection.synced_at = datetime.now(timezone.utc)
                progress('import_commit', duplicate=True, transactions=prior.transaction_count)
                session.commit()
                accepted = True
                db.session.expire_all()
                return {'duplicate': True, 'sha256': digest,
                        'transaction_count': prior.transaction_count,
                        'new_transactions': 0, 'existing_transactions': prior.transaction_count,
                        'reported_balance': str(prior.balance_amount), 'reported_balance_asof': prior.balance_asof.isoformat(),
                        'account_id': prior.ofx_account_id,
                        'summary': account_summary(prior.ofx_account_id)}

            _account_name(session, account_id, ofx_response, connection.org or 'OFX')
            _store_balances(session, account_id, statement)

            progress('import_save')
            dates = [_utc(transaction.dtposted) for transaction in transactions]
            coverage = statement.transactions
            imported = OFXFileImports(
                connection_id=connection.id, response_id=response.id,
                ofx_account_id=account_id, sha256=digest,
                transaction_count=len(expected),
                coverage_start=_utc(coverage.start) if coverage else None,
                coverage_end=_utc(coverage.end) if coverage else None,
                first_transaction=min(dates) if dates else None,
                last_transaction=max(dates) if dates else None,
                balance_amount=balance_amount,
                balance_asof=balance_asof)
            session.add(imported)
            _record_verified_interval(session, imported, verified_interval)
            response.import_status = 'accepted'
            if connection_id is not None:
                connection.synced_at = datetime.now(timezone.utc)
            progress('import_commit', duplicate=False, transactions=len(expected))
            session.commit()
            accepted = True
            db.session.expire_all()
            return {'duplicate': False, 'sha256': digest,
                    'transaction_count': len(expected),
                    'new_transactions': new_transactions, 'existing_transactions': len(expected) - new_transactions,
                    'reported_balance': str(balance_amount), 'reported_balance_asof': balance_asof.isoformat(),
                    'account_id': account_id,
                    'summary': account_summary(account_id)}
        except Exception as error:
            session.rollback()
            if not accepted:
                _reject_response(response_id, error)
            raise
