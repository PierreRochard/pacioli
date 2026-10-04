"""One validated financial import boundary for downloaded and direct OFX."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json

from ofxtools.Types import DateTime as OFXDateTime
from ofxtools.ofxalchemy import OFXParser, Session as OFX_Session
from ofxtools.ofxalchemy.Parser import BankStatement, CreditCardStatement
from ofxtools.ofxalchemy import models as ofx_models
from sqlalchemy import text
from sqlalchemy.orm import Session

from pacioli.database.setup import ofx_engine
from pacioli.extensions import db
from pacioli.models import (BankFeedAccounts, ConnectionResponses, Connections,
                            OFXFileImports, Subaccounts, ChaseExportCoverage)
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


def _validate_envelope(parser, currency, expected_account):
    """Validate transport-independent acceptance before creating financial rows.

    ofxtools deliberately skips error responses and does not validate statuses.
    Connection settings describe expectations; they cannot relabel the document.
    """
    signon = parser.find('SIGNONMSGSRSV1/SONRS/STATUS')
    if signon is None:
        raise ImportRejected('missing_signon_status', 'The OFX response is missing its sign-on status')
    responses = (parser.findall('BANKMSGSRSV1/STMTTRNRS') +
                 parser.findall('CREDITCARDMSGSRSV1/CCSTMTTRNRS'))
    if (len(responses) != 1 or parser.findall('INVSTMTMSGSRSV1/INVSTMTTRNRS') or
            parser.find('SECLISTMSGSRSV1') is not None):
        raise ImportRejected('unsupported_statement', 'The file must contain exactly one bank or credit-card statement')
    if responses[0].find('STATUS') is None:
        raise ImportRejected('missing_statement_status', 'The OFX response is missing its statement status')
    for status in parser.findall('.//STATUS'):
        if ((status.findtext('CODE') or '').strip() != '0' or
                (status.findtext('SEVERITY') or '').strip().upper() != 'INFO'):
            raise ImportRejected('unsuccessful_status', 'The OFX response reports an unsuccessful status')
    statements = (responses[0].findall('STMTRS') + responses[0].findall('CCSTMTRS'))
    if len(statements) != 1:
        raise ImportRejected('unsupported_statement', 'The file must contain exactly one bank or credit-card statement')
    statement = statements[0]
    if (statement.findtext('CURDEF') or '').strip() != currency:
        raise ImportRejected('currency_mismatch', 'OFX statement currency does not match the reviewed account currency')
    bank, card = statement.find('BANKACCTFROM'), statement.find('CCACCTFROM')
    if (bank is None) == (card is None):
        raise ImportRejected('account_identity_invalid', 'The OFX statement must identify exactly one account')
    node = bank if bank is not None else card
    identity = {'type': (node.findtext('ACCTTYPE') or '').strip() if bank is not None else 'CREDITCARD',
                'account_number': (node.findtext('ACCTID') or '').strip(),
                'routing_number': (node.findtext('BANKID') or '').strip() if bank is not None else None}
    if not identity['account_number']:
        raise ImportRejected('account_identity_invalid', 'The OFX statement account is missing')
    if expected_account is not None and identity != expected_account:
        raise AccountNotApproved('The OFX response account does not match the requested account')
    return statement


def _retain_response(content, connection_id):
    """Durably retain encrypted acquisition evidence, independently of acceptance."""
    with Session(bind=ofx_engine()) as session:
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
            connection = session.query(Connections).get(connection_id)
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
    with Session(bind=ofx_engine()) as session:
        response = session.query(ConnectionResponses).get(response_id)
        response.import_status = 'rejected'
        # Raw provider messages can contain credentials or account identifiers.
        # The response itself is encrypted; diagnostics retain only a safe code.
        response.import_error = (getattr(error, 'code', None) or type(error).__name__)[:64]
        session.commit()


def _utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = OFXDateTime().convert(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _prepare_transactions(parser, currency='USD'):
    """Separate Chase card fees from purchases that reuse the same bank FITID.

    Change only the parsed tree; the original QFX is retained verbatim. Always
    derive the fee ID, including fee-only exports, so overlap/order cannot
    change transaction identity. Unknown conflicting IDs must fail closed.
    """
    statements = (parser.findall('BANKMSGSRSV1/STMTTRNRS/STMTRS') +
                  parser.findall('CREDITCARDMSGSRSV1/CCSTMTTRNRS/CCSTMTRS'))
    if len(statements) != 1:
        raise ValueError('The file must contain exactly one bank or credit-card statement')
    statement = statements[0]
    chase_card = (statement.tag == 'CCSTMTRS' and
                  parser.findtext('SIGNONMSGSRSV1/SONRS/FI/ORG') == 'B1' and
                  parser.findtext('SIGNONMSGSRSV1/SONRS/FI/FID') == '10898')
    expected, fee_aliases = {}, {}
    for transaction in statement.findall('BANKTRANLIST/STMTTRN'):
        def value(tag):
            return (transaction.findtext(tag) or '').strip()
        fitid = value('FITID')
        if not fitid or fitid.startswith(CHASE_FEE_ID_PREFIX):
            raise ImportRejected('transaction_id_invalid', 'The QFX contains a missing or reserved transaction ID')
        name = value('NAME')
        if chase_card and name.upper() in CHASE_FEE_NAMES:
            identity = json.dumps([fitid, name.upper()], separators=(',', ':'))
            alias = CHASE_FEE_ID_PREFIX + sha256(identity.encode('utf-8')).hexdigest()
            fee_aliases[alias] = fitid
            transaction.find('FITID').text = alias
            fitid = alias
        posted, amount = _utc(value('DTPOSTED')), Decimal(value('TRNAMT'))
        if (posted is None or not amount.is_finite() or
                amount != amount.quantize(SUPPORTED_PRECISION[currency])):
            raise ImportRejected('transaction_value_invalid', 'The OFX contains an invalid transaction date or amount')
        fingerprint = (posted, amount,
                       value('TRNTYPE'), name, value('MEMO'))
        if fitid in expected and expected[fitid] != fingerprint:
            raise ImportRejected('transaction_id_conflict', 'The QFX reuses a transaction ID for conflicting records')
        expected[fitid] = fingerprint
    return expected, fee_aliases


def _verify_transactions(statement, expected, fee_aliases):
    # ofxtools returns the existing row when a primary key already exists,
    # without checking its contents. Never silently accept a changed record.
    for transaction in statement.transactions:
        actual = (_utc(transaction.dtposted), Decimal(transaction.trnamt),
                  transaction.trntype, transaction.name or '', transaction.memo or '')
        if actual != expected[transaction.fitid]:
            raise ImportRejected('transaction_id_conflict', 'An imported transaction ID conflicts with its stored record')
    for original in set(fee_aliases.values()):
        legacy = OFX_Session.query(ofx_models.STMTTRN).filter_by(
            acctfrom_id=statement.account.id, fitid=original).one_or_none()
        if legacy is not None and (legacy.name or '').upper() in CHASE_FEE_NAMES:
            raise ImportRejected('legacy_fee_id_conflict', 'Previously imported Chase fee IDs need review before reimport')


def _account_name(session, account, institution='Chase'):
    if account.name:
        return account.name
    last_four = account.acctid[-4:]
    if isinstance(account, ofx_models.CCACCTFROM):
        kind, parent = 'Credit Card', 'Accounts Payable'
    elif account.accttype == 'SAVINGS':
        kind, parent = 'Savings', 'Cash'
    else:
        kind, parent = 'Checking', 'Cash'
    name = '{} {} ({})'.format(institution, kind, last_four)
    existing = session.query(Subaccounts).filter_by(name=name).first()
    if existing is not None:
        name = '{} {} ({}; feed {})'.format(institution, kind, last_four, account.id)
    session.add(Subaccounts(name=name, parent=parent))
    account.name = name
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


def _source_metadata(parser, currency='USD'):
    """Read before ofxtools mutates the tree or reuses existing ORM balances."""
    nodes = parser.findall('BANKMSGSRSV1/STMTTRNRS/STMTRS') + parser.findall('CREDITCARDMSGSRSV1/CCSTMTTRNRS/CCSTMTRS')
    if len(nodes) != 1:
        raise ValueError('Expected one statement')
    node = nodes[0]
    amount = Decimal(node.findtext('LEDGERBAL/BALAMT') or 'NaN')
    asof = _utc(node.findtext('LEDGERBAL/DTASOF'))
    if not amount.is_finite() or amount != amount.quantize(SUPPORTED_PRECISION[currency]) or asof is None:
        raise ImportRejected('ledger_balance_invalid', 'A valid {} posted ledger balance is required'.format(currency))
    if asof.date() > datetime.now(timezone.utc).date():
        raise ImportRejected('ledger_balance_future', 'The posted balance date is in the future')
    bank = node.find('BANKACCTFROM')
    def field(parent, tag):
        return (parent.findtext(tag) or '').strip()
    if bank is not None:
        account = OFX_Session.query(ofx_models.BANKACCTFROM).filter_by(
            bankid=field(bank, 'BANKID'), acctid=field(bank, 'ACCTID'),
            accttype=field(bank, 'ACCTTYPE')).one_or_none()
    else:
        account = OFX_Session.query(ofx_models.CCACCTFROM).filter_by(
            acctid=field(node, 'CCACCTFROM/ACCTID')).one_or_none()
    count = OFX_Session.query(ofx_models.STMTTRN).filter_by(acctfrom_id=account.id).count() if account else 0
    return amount, asof, count


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
    if not content or len(content) > MAX_OFX_BYTES:
        raise ValueError('Choose an OFX/QFX file no larger than 10 MiB')
    if verified_interval is not None and (allowed_account_ids is None or len(allowed_account_ids) != 1):
        raise ValueError('Verified export coverage requires one approved account')
    digest = sha256(content).hexdigest()
    response_id, source_connection_id = _retain_response(content, connection_id)
    OFX_Session.remove()
    OFX_Session.configure(bind=ofx_engine())
    accepted = False
    try:
        # Manual uploads and background replays must see each other's commits
        # before checking hashes or FITIDs. Locks release on commit/rollback.
        progress('import_lock')
        OFX_Session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
        OFX_Session.execute(text('SELECT pg_advisory_xact_lock(724109, 2)'))
        response = OFX_Session.query(ConnectionResponses).get(response_id)
        connection = (OFX_Session.query(Connections).filter_by(id=source_connection_id)
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
        parser = OFXParser()
        parser.parse(BytesIO(content))
        _validate_envelope(parser, currency, expected_account)
        expected, fee_aliases = _prepare_transactions(parser, currency)
        balance_amount, balance_asof, before_count = _source_metadata(parser, currency)
        parser.instantiate()
        if len(parser.statements) != 1 or not isinstance(
                parser.statements[0], (BankStatement, CreditCardStatement)):
            raise ValueError('The file must contain exactly one bank or credit-card statement')
        statement = parser.statements[0]
        progress('import_validate')
        _verify_transactions(statement, expected, fee_aliases)
        account = statement.account
        OFX_Session.flush()
        if allowed_account_ids is not None and account.id not in allowed_account_ids:
            raise AccountNotApproved('QFX account is not approved for automatic import')
        registration = OFX_Session.query(BankFeedAccounts).get(account.id)
        if registration is not None and registration.currency != currency:
            raise ImportRejected('currency_registration_conflict', 'This feed has a conflicting currency registration')
        if registration is None:
            OFX_Session.add(BankFeedAccounts(ofx_account_id=account.id, currency=currency))
        progress('import_deduplicate')
        prior = OFX_Session.query(OFXFileImports).filter_by(sha256=digest).one_or_none()
        if prior is not None:
            if prior.ofx_account_id != account.id:
                raise ImportRejected('import_identity_changed', 'The imported statement account identity has changed')
            if (allowed_account_ids is not None and
                    prior.ofx_account_id not in allowed_account_ids):
                raise AccountNotApproved('QFX account is not approved for automatic import')
            _record_verified_interval(OFX_Session, prior, verified_interval)
            response.import_status = 'accepted'
            if connection_id is not None:
                connection.synced_at = datetime.now(timezone.utc)
            progress('import_commit', duplicate=True, transactions=prior.transaction_count)
            OFX_Session.commit()
            accepted = True
            db.session.expire_all()
            return {'duplicate': True, 'sha256': digest,
                    'transaction_count': prior.transaction_count,
                    'new_transactions': 0, 'existing_transactions': prior.transaction_count,
                    'reported_balance': str(prior.balance_amount), 'reported_balance_asof': prior.balance_asof.isoformat(),
                    'account_id': prior.ofx_account_id,
                    'summary': account_summary(prior.ofx_account_id)}

        _account_name(OFX_Session, account, connection.org or 'OFX')
        # ofxtools identifies balances by (account, as-of) and otherwise silently
        # keeps the old amount. Refresh this derived row; retain every original
        # value in the encrypted source and its immutable import snapshot.
        statement.ledgerbal.balamt = balance_amount
        new_transactions = OFX_Session.query(ofx_models.STMTTRN).filter_by(acctfrom_id=account.id).count() - before_count

        progress('import_save')
        dates = [_utc(transaction.dtposted) for transaction in statement.transactions]
        imported = OFXFileImports(
            connection_id=connection.id, response_id=response.id,
            ofx_account_id=account.id, sha256=digest,
            transaction_count=len(expected),
            coverage_start=_utc(getattr(statement.transactions, 'dtstart', None)),
            coverage_end=_utc(getattr(statement.transactions, 'dtend', None)),
            first_transaction=min(dates) if dates else None,
            last_transaction=max(dates) if dates else None,
            balance_amount=balance_amount,
            balance_asof=balance_asof)
        OFX_Session.add(imported)
        _record_verified_interval(OFX_Session, imported, verified_interval)
        response.import_status = 'accepted'
        if connection_id is not None:
            connection.synced_at = datetime.now(timezone.utc)
        progress('import_commit', duplicate=False, transactions=len(expected))
        OFX_Session.commit()
        accepted = True
        db.session.expire_all()
        return {'duplicate': False, 'sha256': digest,
                'transaction_count': len(expected),
                'new_transactions': new_transactions, 'existing_transactions': len(expected) - new_transactions,
                'reported_balance': str(balance_amount), 'reported_balance_asof': balance_asof.isoformat(),
                'account_id': account.id,
                'summary': account_summary(account.id)}
    except Exception as error:
        OFX_Session.rollback()
        if not accepted:
            _reject_response(response_id, error)
        raise
    finally:
        OFX_Session.remove()
