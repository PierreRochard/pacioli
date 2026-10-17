"""Exercise real OFX parsing against the disposable PostgreSQL database."""

from decimal import Decimal
from datetime import date, datetime, timezone
from hashlib import sha256
from io import BytesIO
import re
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db, run_sql
from pacioli.functions.accounting_report import bank_balance_differences
from pacioli.functions.ofx_file_import import AccountNotApproved, import_chase_qfx, account_summary
from pacioli.functions.journal_posting import post_pair
from pacioli.models import (BankFeedAccounts, ConnectionResponses, Connections,
                            OFXFileImports, Subaccounts)
from pacioli.secure_fields import PREFIX


@pytest.fixture(autouse=True)
def isolate_imported_accounts(app_context):
    """Card import cases must not leave bank balances for later ledger audits."""
    protected = {}
    for table in ('ofx.acctfrom', 'admin.ofx_file_imports',
                  'admin.connection_responses', 'admin.connections'):
        protected[table] = {row[0] for row in db.session.execute(text('SELECT id FROM ' + table))}
    names = {row[0] for row in db.session.execute(text('SELECT name FROM bookkeeping.subaccounts'))}
    yield
    db.session.remove()
    for row in db.session.execute(text('SELECT id FROM admin.ofx_file_imports')).all():
        if row.id not in protected['admin.ofx_file_imports']:
            db.session.execute(text('DELETE FROM admin.ofx_file_imports WHERE id=:id'), {'id': row.id})
    for row in db.session.execute(text('SELECT id FROM ofx.acctfrom')).all():
        if row.id in protected['ofx.acctfrom']:
            continue
        # Source/journal associations now enforce referential integrity. Remove
        # only this fixture's postings before deleting its source transactions.
        db.session.execute(text('''DELETE FROM bookkeeping.journal_entries
            WHERE transaction_source='ofx' AND transaction_id IN (
                SELECT public_id FROM bookkeeping.ofx_source_identities WHERE acctfrom_id=:id
            )'''), {'id': row.id})
        for table, column in (('bookkeeping.bank_feed_accounts', 'ofx_account_id'),
                              ('ofx.stmttrn', 'acctfrom_id'), ('ofx.ledgerbal', 'acctfrom_id'), ('ofx.availbal', 'acctfrom_id'),
                              ('ofx.bankacctfrom', 'id'), ('ofx.ccacctfrom', 'id'),
                              ('ofx.acctfrom', 'id')):
            db.session.execute(text('DELETE FROM {} WHERE {}=:id'.format(table, column)), {'id': row.id})
    for table in ('admin.connection_responses', 'admin.connections'):
        for row in db.session.execute(text('SELECT id FROM ' + table)).all():
            if row.id not in protected[table]:
                db.session.execute(text('DELETE FROM ' + table + ' WHERE id=:id'), {'id': row.id})
    for row in db.session.execute(text('SELECT name FROM bookkeeping.subaccounts')).all():
        if row.name not in names:
            db.session.execute(text('DELETE FROM bookkeeping.subaccounts WHERE name=:name'), {'name': row.name})
    db.session.commit()


def _qfx(account_number):
    return ('''OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20260904000000<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS><STMTRS>
<CURDEF>USD
<BANKACCTFROM><BANKID>021000021<ACCTID>{account}<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260901000000<DTEND>20260903000000
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260901000000<TRNAMT>100.00<FITID>deposit-1<NAME>Deposit</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260903000000<TRNAMT>-25.00<FITID>expense-1<NAME>Expense</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>575.00<DTASOF>20260903000000</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
'''.format(account=account_number)).encode('ascii')


def _empty_qfx(content, start, end, asof, balance):
    content = re.sub(rb'<STMTTRN>.*?</STMTTRN>\n?', b'', content, flags=re.S)
    for tag, value in [('DTSTART', start), ('DTEND', end), ('DTASOF', asof), ('BALAMT', balance)]:
        content = re.sub(('<' + tag + '>[^<]+').encode(), ('<' + tag + '>' + value).encode(), content)
    return content


def test_balance_refresh_same_asof_keeps_snapshots_and_counts_only_missing_transactions(app_context):
    content = _qfx(uuid4().hex[:16])
    original = import_chase_qfx(content, reviewed_usd=True)
    assert (original['new_transactions'], original['existing_transactions']) == (2, 0)
    count = db.session.execute(text('SELECT COUNT(*) FROM bookkeeping.journal_entries')).scalar()
    late = content.replace(b'</BANKTRANLIST>', b'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260902000000<TRNAMT>-10.00<FITID>late-1<NAME>Late purchase</STMTTRN></BANKTRANLIST>').replace(b'575.00', b'565.00')
    result = import_chase_qfx(late, reviewed_usd=True)
    assert (result['new_transactions'], result['existing_transactions']) == (1, 2)
    assert result['summary']['balance'] == Decimal('565')
    assert result['summary']['opening_candidate'] == Decimal('500')
    aid = original['account_id']
    assert db.session.execute(text('SELECT balamt FROM ofx.ledgerbal WHERE acctfrom_id=:id'), {'id':aid}).scalar() == Decimal('565')
    rows = db.session.query(OFXFileImports).filter_by(ofx_account_id=aid).order_by(OFXFileImports.id).all()
    assert [row.balance_amount for row in rows] == [Decimal('575'), Decimal('565')]
    # Exact-file replay cannot roll the latest balance backward or duplicate a journal/source.
    for data in (content, late, late + b'\n'):
        replay = import_chase_qfx(data, reviewed_usd=True)
        assert replay['new_transactions'] == 0
        assert replay['summary']['opening_candidate'] == Decimal('500')
        assert db.session.execute(text('SELECT balamt FROM ofx.ledgerbal WHERE acctfrom_id=:id'),
                                  {'id': aid}).scalar() == Decimal('565')
    assert db.session.execute(text('SELECT COUNT(*) FROM bookkeeping.journal_entries')).scalar() == count


def test_verified_empty_export_closes_gap_and_refreshes_balance_without_activity(app_context):
    from pacioli.functions.chase_local_controller import verify_qfx
    number = uuid4().hex[:16]
    content = _qfx(number)
    original = import_chase_qfx(content, reviewed_usd=True)
    aid = original['account_id']
    later = _empty_qfx(content, '20260906000000', '20260906000000', '20260906000000', '575.00')
    result = import_chase_qfx(later, reviewed_usd=True)
    assert result['new_transactions'] == 0
    assert result['summary']['opening_candidate'] is None
    assert result['summary']['coverage_gaps'] == [{'start':'2026-09-04', 'end':'2026-09-05'}]
    empty = later
    job = {'account_number':number, 'start':'2026-09-04', 'end':'2026-09-06'}
    from pacioli.functions.chase_local_bridge import LocalChaseError
    with pytest.raises(LocalChaseError, match='qfx_coverage_unverified'):
        verify_qfx(empty, job)
    verify_qfx(empty, job, verified_export=True)
    for _ in range(2):
        result = import_chase_qfx(empty, reviewed_usd=True, allowed_account_ids=[aid],
                                 verified_interval=(date(2026,9,4),date(2026,9,6)))
        assert result['new_transactions'] == result['existing_transactions'] == 0
        assert result['summary']['opening_candidate'] == Decimal('500')
        assert result['summary']['opening_date'] == date(2026,9,1)
        assert result['summary']['signed_activity'] == Decimal('75')
        assert result['summary']['coverage_contiguous']
    # Importing an older dated balance later does not replace the latest one.
    old = _empty_qfx(content, '20260904000000', '20260904000000', '20260904000000', '123.00')
    assert import_chase_qfx(old, reviewed_usd=True)['summary']['balance'] == Decimal('575')


def test_verified_request_bounds_drive_opening_date_and_cover_balance_date(app_context):
    content = _qfx(uuid4().hex[:16]).replace(b'<DTASOF>20260903', b'<DTASOF>20260904')
    result = import_chase_qfx(content, reviewed_usd=True)
    assert result['summary']['opening_candidate'] is None
    result = import_chase_qfx(content, reviewed_usd=True, allowed_account_ids=[result['account_id']],
                             verified_interval=(date(2026,8,31),date(2026,9,4)))
    assert result['summary']['opening_date'] == date(2026,8,31)
    assert result['summary']['opening_candidate'] == Decimal('500')
    source = db.session.query(OFXFileImports).filter_by(sha256=result['sha256']).one()
    # A proven interval remains usable if a legacy file had no declared bounds.
    source.coverage_start = source.coverage_end = None
    db.session.commit()
    assert account_summary(result['account_id'])['opening_candidate'] == Decimal('500')


def test_posted_balance_excludes_available_balance_and_future_transactions(app_context):
    content = _qfx(uuid4().hex[:16]).replace(b'</LEDGERBAL>', b'</LEDGERBAL><AVAILBAL><BALAMT>999.99<DTASOF>20260903000000</AVAILBAL>')
    original = import_chase_qfx(content, reviewed_usd=True)
    future = _empty_qfx(content, '20260905000000', '20260905000000', '20260903000000', '575.00')
    future = future.replace(b'</BANKTRANLIST>', b'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260905000000<TRNAMT>-10.00<FITID>after-balance<NAME>Later</STMTTRN></BANKTRANLIST>')
    result = import_chase_qfx(future, reviewed_usd=True)
    assert result['summary']['balance'] == Decimal('575')
    assert result['summary']['opening_candidate'] == Decimal('500')
    assert result['summary']['signed_activity'] == Decimal('75')


def test_inconsistent_balance_dates_block_opening_until_missing_activity_is_imported(app_context):
    content = _qfx(uuid4().hex[:16])
    initial = import_chase_qfx(content, reviewed_usd=True)
    later = _empty_qfx(content, '20260904000000', '20260906000000', '20260906000000', '565.00')
    result = import_chase_qfx(later, reviewed_usd=True)
    assert result['new_transactions'] == 0 and result['summary']['coverage_contiguous']
    assert result['summary']['balance'] == Decimal('565')
    assert result['summary']['opening_candidate'] is None
    assert result['summary']['balance_disagreements'] == [{'asof':'2026-09-03','difference':'10.00'}]
    repaired = later.replace(b'</BANKTRANLIST>', b'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260905000000<TRNAMT>-10.00<FITID>missing<NAME>Missing purchase</STMTTRN></BANKTRANLIST>')
    result = import_chase_qfx(repaired, reviewed_usd=True)
    assert result['new_transactions'] == 1
    assert result['summary']['opening_candidate'] == Decimal('500')
    assert not result['summary']['balance_disagreements']


def test_opening_cutoff_is_utc_even_with_a_different_database_session_timezone(app_context):
    content = _qfx(uuid4().hex[:16]).replace(b'<DTPOSTED>20260903000000', b'<DTPOSTED>20260903235959')
    initial = import_chase_qfx(content, reviewed_usd=True)
    import_chase_qfx(_empty_qfx(content, '20260904000000', '20260904000000', '20260904000000', '575.00'), reviewed_usd=True)
    db.session.expire_all()
    db.session.execute(text("SET LOCAL TIME ZONE 'America/Chicago'"))
    try:
        summary = account_summary(initial['account_id'])
        assert summary['opening_candidate'] == Decimal('500')
        assert summary['opening_date'] == date(2026,9,1)
        assert summary['balance_asof'].date() == date(2026,9,4)
        assert not summary['balance_disagreements']
    finally:
        db.session.rollback()


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', '1.001'])
def test_invalid_ledger_balance_cannot_commit_transactions(app_context, amount):
    content = _qfx(uuid4().hex[:16]).replace(b'575.00', amount.encode())
    before = db.session.query(OFXFileImports).count()
    with pytest.raises(ValueError, match='valid USD posted'):
        import_chase_qfx(content, reviewed_usd=True)
    assert db.session.query(OFXFileImports).count() == before


def test_chase_qfx_import_is_deduplicated_and_preserves_encrypted_source(app_context):
    account_number = uuid4().hex[:16]
    content = _qfx(account_number)
    account_id = None
    connection_id = None
    summary = None
    try:
        result = import_chase_qfx(content, reviewed_usd=True)
        account_id = result['account_id']
        summary = result['summary']
        assert result['duplicate'] is False
        assert summary['transaction_count'] == 2
        assert summary['coverage_start'].date().isoformat() == '2026-09-01'
        assert summary['coverage_end'].date().isoformat() == '2026-09-03'
        assert summary['coverage_contiguous'] is True
        assert summary['oldest'].date().isoformat() == '2026-09-01'
        assert summary['newest'].date().isoformat() == '2026-09-03'
        assert summary['balance'] == Decimal('575.00')
        assert summary['opening_candidate'] == Decimal('500.00')
        assert account_number not in summary['account_name']
        assert db.session.query(Subaccounts).filter_by(name=summary['account_name']).one().parent == 'Cash'
        assert db.session.get(BankFeedAccounts, account_id).currency == 'USD'

        record = db.session.query(OFXFileImports).filter_by(sha256=result['sha256']).one()
        connection_id = record.connection_id
        assert db.session.get(Connections, connection_id).source == 'ofx_file'
        raw = db.session.execute(text(
            'SELECT response FROM admin.connection_responses WHERE id = :id'),
            {'id': record.response_id}).scalar()
        assert raw.startswith(PREFIX)
        assert account_number not in raw

        with pytest.raises(AccountNotApproved):
            import_chase_qfx(content, reviewed_usd=True, allowed_account_ids=[-1])
        another_file = content.replace(b'deposit-1', b'deposit-2').replace(
            b'expense-1', b'expense-2')
        later = import_chase_qfx(another_file, reviewed_usd=True,
                                 allowed_account_ids=[account_id])
        assert later['account_id'] == account_id
        assert later['duplicate'] is False
        repeated = import_chase_qfx(content, reviewed_usd=True)
        assert repeated['duplicate'] is True
        assert repeated['summary']['transaction_count'] == 4
        assert db.session.query(OFXFileImports).filter_by(sha256=result['sha256']).count() == 1
    finally:
        db.session.remove()
        if account_id is not None:
            run_sql(text(
                'DELETE FROM admin.ofx_file_imports WHERE ofx_account_id = :id'), {'id': account_id})
            run_sql(text(
                'DELETE FROM admin.connection_responses WHERE connection_id = :id'),
                {'id': connection_id})
            run_sql(text(
                'DELETE FROM bookkeeping.bank_feed_accounts WHERE ofx_account_id = :id'),
                {'id': account_id})
            run_sql(text(
                'DELETE FROM ofx.stmttrn WHERE acctfrom_id = :id'), {'id': account_id})
            run_sql(text(
                'DELETE FROM ofx.ledgerbal WHERE acctfrom_id = :id'), {'id': account_id})
            run_sql(text(
                'DELETE FROM ofx.bankacctfrom WHERE id = :id'), {'id': account_id})
            run_sql(text(
                'DELETE FROM ofx.acctfrom WHERE id = :id'), {'id': account_id})
            run_sql(text(
                'DELETE FROM admin.connections WHERE id = :id'), {'id': connection_id})
            if summary is not None:
                run_sql(text(
                    'DELETE FROM bookkeeping.subaccounts WHERE name = :name'),
                    {'name': summary['account_name']})


def test_automatic_qfx_rejects_unapproved_account_before_import(app_context):
    account_number = uuid4().hex[:16]
    content = _qfx(account_number)
    with pytest.raises(AccountNotApproved):
        import_chase_qfx(content, reviewed_usd=True,
                         allowed_account_ids=[-1])
    assert db.session.query(OFXFileImports).filter_by(
        sha256=sha256(content).hexdigest()).count() == 0
    assert db.session.execute(text(
        'SELECT count(*) FROM ofx.bankacctfrom WHERE acctid = :acctid'),
        {'acctid': account_number}).scalar() == 0


def test_chase_qfx_import_page_requires_login_and_csrf(
        app_context, app, client, admin_user):
    path = '/connections/import_chase/'
    assert client.get(path).status_code == 302
    client.post('/login/', data=admin_user)
    prior = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        page = client.get(path)
        assert page.status_code == 200
        assert b'Import Chase QFX' in page.data
        # The import form and the navbar's logout form.
        assert page.data.count(b'name="csrf_token"') == 2
        assert client.post(path, data={'reviewed_usd': 'yes',
                                       'statement': (BytesIO(b'not ofx'), 'bad.qfx')}).status_code == 400
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode('ascii')
        response = client.post(path, data={
            'csrf_token': token, 'reviewed_usd': 'yes',
            'statement': (BytesIO(b'not ofx'), 'bad.qfx')})
        assert response.status_code == 200, response.data[:1000]
        assert b'The file is not a valid OFX statement' in response.data
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior


def test_chase_card_balance_reconciles_to_liability(clean_ledger):
    account_name = 'Chase Card ' + uuid4().hex[:12]
    account_id = None
    try:
        db.session.add(Subaccounts(name=account_name, parent='Accounts Payable'))
        db.session.commit()
        account_id = run_sql(text("""
            INSERT INTO ofx.acctfrom (subclass, name)
            VALUES ('ccacctfrom', :name) RETURNING id
        """), {'name': account_name}).scalar()
        run_sql(text("""
            INSERT INTO ofx.ccacctfrom (id, acctid) VALUES (:id, :acctid)
        """), {'id': account_id, 'acctid': uuid4().hex[:16]})
        run_sql(text("""
            INSERT INTO ofx.ledgerbal (acctfrom_id, dtasof, balamt)
            VALUES (:id, :asof, -500)
        """), {'id': account_id, 'asof': datetime(2026, 9, 3)})
        db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
        post_pair(timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
                  debit='Personal Equity', credit=account_name,
                  functional_amount='500.00')
        db.session.commit()
        assert not [issue for issue in bank_balance_differences(db.session)
                    if issue['account_id'] == account_id]
    finally:
        db.session.remove()
        run_sql(text(
            'DELETE FROM bookkeeping.journal_entries WHERE credit_subaccount = :name'),
            {'name': account_name})
        if account_id is not None:
            run_sql(text(
                'DELETE FROM bookkeeping.bank_feed_accounts WHERE ofx_account_id = :id'),
                {'id': account_id})
            run_sql(text('DELETE FROM ofx.ledgerbal WHERE acctfrom_id = :id'),
                              {'id': account_id})
            run_sql(text('DELETE FROM ofx.ccacctfrom WHERE id = :id'),
                              {'id': account_id})
            run_sql(text('DELETE FROM ofx.acctfrom WHERE id = :id'),
                              {'id': account_id})
        run_sql(text('DELETE FROM bookkeeping.subaccounts WHERE name = :name'),
                          {'name': account_name})


def _card_qfx(account, transactions, chase=True):
    content = _qfx(account).decode('ascii')
    content = content.replace('BANKMSGSRSV1', 'CREDITCARDMSGSRSV1')
    content = content.replace('STMTTRNRS', 'CCSTMTTRNRS').replace('STMTRS', 'CCSTMTRS')
    content = re.sub(r'<BANKACCTFROM>.*?</BANKACCTFROM>',
                     '<CCACCTFROM><ACCTID>{}</CCACCTFROM>'.format(account), content)
    content = re.sub(r'<STMTTRN>.*</STMTTRN>', ''.join(transactions), content,
                     flags=re.DOTALL)
    if chase:
        content = content.replace('</SONRS>', '<FI><ORG>B1<FID>10898</FI></SONRS>')
    return content.encode('ascii')


def _transaction(fitid, amount, name, date='20260903000000'):
    return ('<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{}<TRNAMT>{}<FITID>{}'
            '<NAME>{}</STMTTRN>').format(date, amount, fitid, name)


def test_local_account_listing_uses_real_bank_and_card_tables(app_context, client, admin_user):
    from pacioli.functions.chase_local import accounts

    checking_number = uuid4().hex[:12] + '1357'
    card_number = uuid4().hex[:12] + '2468'
    checking = import_chase_qfx(_qfx(checking_number), reviewed_usd=True)
    card = import_chase_qfx(_card_qfx(card_number, [
        _transaction('card-1', '-10.00', 'Fixture purchase')]), reviewed_usd=True)
    found = {a['id']: a for a in accounts()}
    for imported, number in ((checking, checking_number), (card, card_number)):
        account = found[imported['account_id']]
        assert account['account_number'] == number and account['last4'] == number[-4:]
        assert number not in account['label']

    client.post('/login/', data=admin_user)
    page = client.get('/connections/chase/local/')
    assert page.status_code == 200
    assert checking_number.encode() not in page.data and card_number.encode() not in page.data
    assert b'1357' in page.data and b'2468' in page.data

    db.session.get(BankFeedAccounts, card['account_id']).currency = 'EUR'
    db.session.commit()
    assert card['account_id'] not in {a['id'] for a in accounts()}


@pytest.mark.parametrize('fee_name', ['FOREIGN TRANSACTION FEE', 'TRANSACTION FEE'])
@pytest.mark.parametrize('fee_first', [True, False])
def test_chase_qfx_preserves_purchase_and_fee_with_shared_fitid(
        app_context, fee_name, fee_first):
    account = uuid4().hex[:16]
    purchase = _transaction('bank-reference', '-100.00', 'Example merchant')
    fee = _transaction('bank-reference', '-3.00', fee_name)
    rows = [fee, purchase] if fee_first else [purchase, fee]
    original = _card_qfx(account, rows)
    first = import_chase_qfx(original, reviewed_usd=True)
    account_id = first['account_id']
    assert first['summary']['transaction_count'] == 2
    amounts = db.session.execute(text(
        'SELECT trnamt FROM ofx.stmttrn WHERE acctfrom_id=:id ORDER BY trnamt'),
        {'id': account_id}).scalars().all()
    assert amounts == [Decimal('-100'), Decimal('-3')]
    record = OFXFileImports.query.filter_by(sha256=sha256(original).hexdigest()).one()
    assert db.session.get(ConnectionResponses, record.response_id).response.encode('latin-1') == original

    # Boundaries, reversed order, and fee-only downloads retain the same IDs.
    for subset in ([purchase], [fee], list(reversed(rows))):
        result = import_chase_qfx(_card_qfx(account, subset), reviewed_usd=True)
        assert result['summary']['transaction_count'] == 2
        assert result['summary']['opening_candidate'] == Decimal('678.00')
    assert import_chase_qfx(original, reviewed_usd=True)['duplicate']


@pytest.mark.parametrize('chase', [True, False])
def test_chase_qfx_rejects_unknown_fitid_collisions_atomically(app_context, chase):
    account = uuid4().hex[:16]
    rows = [_transaction('same-id', '-10', 'Example merchant'),
            _transaction('same-id', '-20', 'Different merchant')]
    if not chase:
        rows[1] = _transaction('same-id', '-3', 'FOREIGN TRANSACTION FEE')
    content = _card_qfx(account, rows, chase=chase)
    with pytest.raises(ValueError, match='conflicting records'):
        import_chase_qfx(content, reviewed_usd=True)
    assert OFXFileImports.query.filter_by(sha256=sha256(content).hexdigest()).count() == 0
    assert db.session.execute(text('SELECT count(*) FROM ofx.ccacctfrom WHERE acctid=:id'),
                              {'id': account}).scalar() == 0


@pytest.mark.parametrize('change', ['amount', 'date', 'name'])
def test_chase_qfx_rejects_changed_existing_transaction_atomically(app_context, change):
    account = uuid4().hex[:16]
    original = _transaction('same-id', '-10', 'Example merchant')
    first = import_chase_qfx(_card_qfx(account, [original]), reviewed_usd=True)
    changed = _transaction('same-id', '-11' if change == 'amount' else '-10',
                           'Other merchant' if change == 'name' else 'Example merchant',
                           '20260902000000' if change == 'date' else '20260903000000')
    content = _card_qfx(account, [_transaction('new-id', '-5', 'New purchase'), changed])
    with pytest.raises(ValueError, match='conflicts with its stored record'):
        import_chase_qfx(content, reviewed_usd=True)
    assert OFXFileImports.query.filter_by(sha256=sha256(content).hexdigest()).count() == 0
    assert db.session.execute(text('SELECT count(*) FROM ofx.stmttrn WHERE acctfrom_id=:id'),
                              {'id': first['account_id']}).scalar() == 1


def test_chase_qfx_does_not_duplicate_legacy_fee_ids(app_context):
    account = uuid4().hex[:16]
    fee = _transaction('same-id', '-3', 'FOREIGN TRANSACTION FEE')
    # Simulate a fee imported before the Chase-specific normalization existed.
    first = import_chase_qfx(_card_qfx(account, [fee], chase=False), reviewed_usd=True)
    with pytest.raises(ValueError, match='Previously imported Chase fee IDs'):
        import_chase_qfx(_card_qfx(account, [fee]), reviewed_usd=True)
    assert db.session.execute(text('SELECT count(*) FROM ofx.stmttrn WHERE acctfrom_id=:id'),
                              {'id': first['account_id']}).scalar() == 1
