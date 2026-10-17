from datetime import date, datetime, timezone
import ssl
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from uuid import uuid4

from pacioli import db
from pacioli.functions import ofx_document
from pacioli.functions.ofx_file_import import MAX_OFX_BYTES, import_ofx_statement
from pacioli.functions.journal_posting import post_pair
from pacioli.models import (Subaccounts, Mappings, JournalEntries,
                            Connections, Transactions,
                            AccountsFrom, CreditCardAccounts, BankAccounts)


DOWNLOAD_TIMEOUT_SECONDS = 60


class _RefuseRedirects(HTTPRedirectHandler):
    """The request carries credentials; never resend it elsewhere."""

    def redirect_request(self, *args, **kwargs):
        raise ValueError('The OFX server answered with a redirect')


def download(url, body):
    """POST one OFX request over verified HTTPS; at most MAX_OFX_BYTES + 1 bytes."""
    if urlparse(url).scheme != 'https':
        raise ValueError('Direct OFX downloads require an https URL')
    request = Request(url, data=body, method='POST',
                      headers={'Content-Type': 'application/x-ofx', 'Accept': '*/*, application/x-ofx'})
    opener = build_opener(_RefuseRedirects, HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read(MAX_OFX_BYTES + 1)


def sync_ofx():
    for connection in (db.session.query(Connections)
                       .filter(Connections.source == 'ofx')
                       .all()):
        success = sync_ofx_connection(connection)
        if not success:
            break

    for account in (db.session.query(AccountsFrom)
                    .filter(AccountsFrom.name.is_(None))
                    .all()):
        account.name = ''
        db.session.commit()


def sync_ofx_connection(connection):
    if connection.currency not in ('USD', 'EUR', 'BTC'):
        raise ValueError('Set the OFX connection currency before syncing')
    if connection.type in ['Checking', 'Savings']:
        account_model = BankAccounts
        account = {'account': 'bank', 'bankid': connection.routing_number,
                   'acctid': connection.account_number, 'accttype': connection.type.upper()}
    elif connection.type == 'Credit Card':
        account_model = CreditCardAccounts
        account = {'account': 'creditcard', 'acctid': connection.account_number}
    else:
        raise Exception('Unrecognized account/'
                        'connection type: {0}'.format(connection.type))

    latest_query = (db.session.query(Transactions.date)
                   .join(account_model, Transactions.account_id == account_model.id)
                   .filter(account_model.acctid == connection.account_number))
    if connection.type in ['Checking', 'Savings']:
        latest_query = latest_query.filter(BankAccounts.bankid == connection.routing_number)
    latest = latest_query.order_by(Transactions.date.desc()).first()
    # Without a stored transaction, ask for everything the bank keeps.
    start = latest[0].date() if latest else None
    request = ofx_document.statement_request(
        dtclient=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'),
        userid=connection.user, userpass=connection.password,
        org=connection.org, fid=connection.fid, appid='QWIN', appver='2500',
        clientuid=connection.clientuid, trnuid=str(uuid4()), newfileuid=str(uuid4()),
        dtstart=start.strftime('%Y%m%d') if start else None,
        dtend=date.today().strftime('%Y%m%d') if start else None,
        **account)
    content = download(connection.url, request)

    # Both direct downloads and QFX uploads use the same acceptance boundary.
    # Retain bounded encrypted response evidence; account/currency/status and
    # repeated FITID contents are proved before any financial commit.
    import_ofx_statement(content, currency=connection.currency, connection_id=connection.id,
        expected_account={'type': ('CREDITCARD' if connection.type == 'Credit Card'
                                  else connection.type.upper()),
                          'account_number': connection.account_number,
                          'routing_number': (None if connection.type == 'Credit Card'
                                             else connection.routing_number)})

    return True


def apply_all_mappings():
    """Resolve every source against the same locked rules, then commit once."""
    return _apply_ofx_mappings()


def apply_single_ofx_mapping(mapping_id):
    """Scope by this rule, but resolve against all rules before any posting."""
    return _apply_ofx_mappings(mapping_id)


def _apply_ofx_mappings(mapping_id=None):
    from collections import defaultdict
    from sqlalchemy import text
    from pacioli.functions.mapping_resolution import (
        MappingResolver, source_matches, verify_replay,
    )

    try:
        db.session.execute(text("SET LOCAL lock_timeout = '10s'"))
        db.session.execute(text("SET LOCAL statement_timeout = '120s'"))
        # Match the reviewed-batch/close lock order. Concurrent rule edits and
        # financial imports cannot change eligibility midway through this batch.
        db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
        db.session.execute(text('LOCK TABLE bookkeeping.journal_entries IN SHARE ROW EXCLUSIVE MODE'))
        db.session.execute(text('''LOCK TABLE ofx.stmttrn, ofx.acctfrom,
            bookkeeping.bank_feed_accounts, bookkeeping.subaccounts IN SHARE MODE'''))
        db.session.execute(text('LOCK TABLE admin.mappings IN SHARE ROW EXCLUSIVE MODE'))
        mappings = db.session.query(Mappings).filter_by(source='ofx').populate_existing().all()
        requested = next((rule for rule in mappings if rule.id == mapping_id), None)
        if mapping_id is not None and (requested is None or not (requested.keyword or '').strip()):
            raise ValueError('An OFX mapping with a keyword is required')
        resolver = MappingResolver(mappings)
        existing = defaultdict(list)
        for entry in db.session.query(JournalEntries).filter_by(transaction_source='ofx').all():
            existing[entry.transaction_id].append(entry)
        chart = {name for name, in db.session.query(Subaccounts.name)}
        planned = []
        already_posted = 0
        for transaction in db.session.query(Transactions).order_by(Transactions.date.asc(), Transactions.id).all():
            if requested is not None and not source_matches(requested, transaction):
                continue
            candidates = resolver.candidates(transaction)
            if not candidates:
                continue
            if existing.get(transaction.id):
                verify_replay(transaction, existing[transaction.id])
                already_posted += 1
                continue
            mapping, decision = resolver.resolve(transaction, candidates)
            if any(decision['posting'][side] not in chart for side in ('debit', 'credit')):
                raise ValueError('OFX transaction and mapping require existing chart accounts')
            planned.append((transaction, mapping, decision))
        for transaction, mapping, decision in planned:
            posting_time = transaction.date
            if posting_time.tzinfo is None:
                posting_time = posting_time.replace(tzinfo=timezone.utc)
            post_pair(transaction_id=transaction.id, transaction_source='ofx',
                      timestamp=posting_time, debit=decision['posting']['debit'],
                      credit=decision['posting']['credit'], functional_amount=abs(transaction.amount),
                      mapping_id=mapping.id, mapping_decision=decision,
                      memo=('Provisional classification; review required. ' if mapping.review_required else '')
                           + (mapping.review_note or 'Applied saved OFX mapping'))
            db.session.flush()
        db.session.commit()
        return {'posted': len(planned), 'already_posted': already_posted}
    except Exception:
        db.session.rollback()
        raise
