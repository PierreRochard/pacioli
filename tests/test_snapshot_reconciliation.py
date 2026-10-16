"""Real QFX parsing and adversarial cutoff evidence in disposable PostgreSQL."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import re
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.test_chase_qfx import _qfx, isolate_imported_accounts
from pacioli.extensions import db
from pacioli.models import (OFXFileImports, ConnectionResponses, AccountingEvidence,
                            JournalEntries, ClosedPeriods, BankFeedAccounts)
from pacioli.functions import reconciliation as rec
from pacioli.functions import snapshot_reconciliation as timing
from pacioli.functions.accounting_report import bank_balance_differences
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.ofx_file_import import import_chase_qfx, account_summary
from pacioli.functions.period_close import journal_fingerprint
from pacioli.functions.source_identity import source_identity


@pytest.fixture(params=['bank', 'card'])
def snapshot_case(clean_ledger, request):
    number = uuid4().hex[:16]
    raw = _qfx(number)
    card = request.param == 'card'
    if card:
        raw = raw.replace(b'BANKMSGSRSV1', b'CREDITCARDMSGSRSV1').replace(b'STMTTRNRS', b'CCSTMTTRNRS').replace(b'<STMTRS>', b'<CCSTMTRS>').replace(b'</STMTRS>', b'</CCSTMTRS>')
        raw = re.sub(rb'<BANKACCTFROM>.*?</BANKACCTFROM>', b'<CCACCTFROM><ACCTID>'+number.encode()+b'</CCACCTFROM>', raw)
    old_balance, later_balance, opening = ('-400.00', '-425.00', '-500.00') if card else ('600.00', '575.00', '500.00')
    original = re.sub(rb'<STMTTRN><TRNTYPE>DEBIT.*?</STMTTRN>', b'', raw, flags=re.S).replace(b'575.00', old_balance.encode())
    later = raw.replace(b'575.00', later_balance.encode()).replace(b'<DTASOF>20260903', b'<DTASOF>20260904').replace(b'<DTEND>20260903', b'<DTEND>20260904')
    first = import_chase_qfx(original, reviewed_usd=True)
    last = import_chase_qfx(later, reviewed_usd=True)
    aid = first['account_id']
    name = rec.account(aid).name
    for source in db.session.execute(text('SELECT * FROM ofx.transactions WHERE account_id=:id'), {'id': aid}):
        post_pair(timestamp=rec.utc(source.date), debit=name if source.amount > 0 else 'Groceries',
            credit='Salary' if source.amount > 0 else name, functional_amount=abs(source.amount),
            transaction_source='ofx', transaction_id=source.id)
    anchor = rec.import_statement(dict(account_id=aid, start='2026-09-01', end='2026-09-01',
        opening=opening, closing=old_balance, movement='100.00', verification='Fixture PDF identity and amounts checked'),
        b'%PDF-1.4 fixture bank statement', 'fixture reviewer')
    p = rec.opening_preview(aid, date(2026, 9, 1))
    rec.post_opening(aid, date(2026, 9, 1), p['digest'], 'fixture reviewer')
    db.session.commit()
    old = db.session.query(OFXFileImports).filter_by(sha256=first['sha256']).one()
    new = db.session.query(OFXFileImports).filter_by(sha256=last['sha256']).one()
    return old, new, anchor


def proof(case):
    return timing.preview(*(row.id for row in case))


def approve(case, p):
    return timing.approve(*(row.id for row in case), p['digest'], 'independent reviewer',
        'Reviewed original files and statement; later export adds the exact posting-date movement')


def rewrite_source(row, transform):
    response = db.session.get(ConnectionResponses, row.response_id)
    raw = transform(response.response.encode('latin-1'))
    response.response = raw.decode('latin-1')
    row.sha256 = sha256(raw).hexdigest()
    db.session.flush()


def test_snapshot_review_is_explicit_idempotent_and_preserves_books(snapshot_case):
    old, new, anchor = snapshot_case
    before = journal_fingerprint(db.session, datetime(2027, 1, 1, tzinfo=timezone.utc))
    p = proof(snapshot_case)
    assert account_summary(old.ofx_account_id)['opening_candidate'] is None
    assert p['same_day_count'] == 1 and Decimal(p['same_day_movement']) == Decimal('-25')
    assert bank_balance_differences(db.session)  # Preview is not approval.
    assert timing.candidate(old.ofx_account_id, old.balance_asof, old.balance_amount) == p
    first = approve(snapshot_case, p)
    db.session.commit()
    assert approve(snapshot_case, p).id == first.id
    db.session.commit()
    assert db.session.query(AccountingEvidence).filter_by(kind=timing.KIND).count() == 1
    assert not bank_balance_differences(db.session)
    summary = account_summary(old.ofx_account_id)
    assert summary['opening_candidate'] == (Decimal('-500') if old.balance_amount < 0 else Decimal('500'))
    assert not summary['balance_disagreements'] and len(summary['reviewed_balance_cutoffs']) == 1
    assert len(bank_balance_differences(db.session, include_reviewed=True)) == 1
    assert not rec.evidence_differences(db.session)
    assert before == journal_fingerprint(db.session, datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert db.session.execute(text('SELECT payload FROM bookkeeping.accounting_evidence WHERE id=:id'), {'id': first.id}).scalar().startswith('fernet:')


@pytest.mark.parametrize('failure', ['hash', 'identity', 'balance', 'same_day_in_original',
    'missing_later_row', 'unposted', 'wrong_posting_date', 'missing_coverage', 'missing_opening',
    'revised_snapshot', 'revised_witness', 'coverage_metadata', 'later_not_later', 'stale_digest', 'unreviewed_currency', 'extra_bank_entry', 'closed'])
def test_snapshot_requires_complete_independent_evidence(snapshot_case, monkeypatch, failure):
    old, new, anchor = snapshot_case
    p = proof(snapshot_case)
    if failure == 'hash':
        db.session.get(ConnectionResponses, old.response_id).response += '\n'
    elif failure == 'identity':
        rewrite_source(old, lambda b: re.sub(rb'<ACCTID>[^<]+', b'<ACCTID>not-this-account', b))
    elif failure == 'balance':
        new.balance_amount += Decimal('1')
    elif failure == 'same_day_in_original':
        response = db.session.get(ConnectionResponses, new.response_id).response.encode('latin-1')
        expense = re.search(rb'<STMTTRN><TRNTYPE>DEBIT.*?</STMTTRN>', response, flags=re.S)[0]
        rewrite_source(old, lambda b: b.replace(b'</BANKTRANLIST>', expense+b'</BANKTRANLIST>'))
    elif failure == 'missing_later_row':
        rewrite_source(new, lambda b: re.sub(rb'<STMTTRN><TRNTYPE>DEBIT.*?</STMTTRN>', b'', b, flags=re.S))
    elif failure in ('unposted', 'wrong_posting_date'):
        entry = db.session.query(JournalEntries).filter_by(transaction_source='ofx', transaction_id=source_identity(db.session, old.ofx_account_id, 'expense-1')).one()
        if failure == 'unposted': db.session.delete(entry)
        else: entry.timestamp += timedelta(days=1)
    elif failure == 'missing_coverage':
        monkeypatch.setattr(rec, 'account_ranges', lambda aid: ([(date(2026,9,1), date(2026,9,2))], False))
    elif failure == 'missing_opening':
        monkeypatch.setattr(rec, 'opening_journal_matches', lambda row: False)
    elif failure in ('revised_snapshot','revised_witness'):
        target = old if failure == 'revised_snapshot' else new
        db.session.execute(text('UPDATE ofx.ledgerbal SET balamt=balamt+1 WHERE acctfrom_id=:aid AND dtasof=:asof'),
            {'aid': target.ofx_account_id, 'asof': target.balance_asof.replace(tzinfo=None)})
    elif failure == 'coverage_metadata': old.coverage_end += timedelta(days=1)
    elif failure == 'later_not_later': new.imported_at = old.imported_at
    elif failure == 'stale_digest': p['digest'] = 'wrong'
    elif failure == 'unreviewed_currency': db.session.get(BankFeedAccounts, old.ofx_account_id).currency = 'EUR'
    elif failure == 'extra_bank_entry':
        post_pair(timestamp=datetime(2026,9,3,tzinfo=timezone.utc), debit='Personal Equity', credit=rec.account(old.ofx_account_id).name, functional_amount='1')
    elif failure == 'closed':
        db.session.add(ClosedPeriods(period='2026-09',journal_count=0,journal_digest='fixture',statement_snapshot={}))
    db.session.flush()
    with pytest.raises(ValueError): approve(snapshot_case, p)
    db.session.rollback()
    assert db.session.query(AccountingEvidence).filter_by(kind=timing.KIND).count() == 0


def test_review_reopens_when_source_changes_and_keeps_categories_independent(snapshot_case):
    old, _, _ = snapshot_case
    p = proof(snapshot_case)
    approve(snapshot_case, p); db.session.commit()
    entry = db.session.query(JournalEntries).filter_by(transaction_source='ofx', transaction_id=source_identity(db.session, old.ofx_account_id, 'expense-1')).one()
    entry.debit_subaccount = 'Restaurants'
    db.session.flush()
    assert not bank_balance_differences(db.session)  # Expense review does not change cash evidence.
    db.session.execute(text("UPDATE ofx.stmttrn SET name='Changed source' WHERE acctfrom_id=:aid AND fitid='expense-1'"), {'aid': old.ofx_account_id})
    assert len(bank_balance_differences(db.session)) == 1
    assert any(d['kind'] == 'qfx_snapshot_evidence' for d in rec.evidence_differences(db.session))
    db.session.rollback()


def test_snapshot_utc_boundaries_ignore_database_timezone(snapshot_case):
    expected = proof(snapshot_case)
    db.session.execute(text("SET LOCAL timezone='Pacific/Honolulu'"))
    assert proof(snapshot_case) == expected
    approve(snapshot_case, expected)
    assert not bank_balance_differences(db.session)
    db.session.rollback()


def test_new_review_supersedes_stale_proof_without_erasing_history(snapshot_case):
    old, new, anchor = snapshot_case
    first = approve(snapshot_case, proof(snapshot_case)); db.session.commit()
    response = db.session.get(ConnectionResponses, new.response_id).response.encode('latin-1')
    extra = (b'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260903000000<TRNAMT>-10.00<FITID>late-debit<NAME>Late debit</STMTTRN>'
             b'<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260903000000<TRNAMT>10.00<FITID>late-credit<NAME>Late credit</STMTTRN>')
    imported = import_chase_qfx(response.replace(b'</BANKTRANLIST>', extra+b'</BANKTRANLIST>'), reviewed_usd=True)
    name = rec.account(old.ofx_account_id).name
    for fitid, debit, credit in (('late-debit','Groceries',name),('late-credit',name,'Groceries')):
        post_pair(timestamp=datetime(2026,9,3,tzinfo=timezone.utc),debit=debit,credit=credit,
            functional_amount='10',transaction_source='ofx',transaction_id=source_identity(db.session, old.ofx_account_id, fitid))
    db.session.commit()
    assert bank_balance_differences(db.session)
    later = db.session.query(OFXFileImports).filter_by(sha256=imported['sha256']).one()
    refreshed = (old,later,anchor)
    second = approve(refreshed,proof(refreshed)); db.session.commit()
    assert first.id != second.id
    statuses = timing.reviewed(db.session)[1]
    assert statuses[0]['superseded_by'] == second.id
    assert statuses[1]['active'] and statuses[1]['valid']
    assert not bank_balance_differences(db.session)
    assert not rec.evidence_differences(db.session)
    assert db.session.query(AccountingEvidence).filter_by(kind=timing.KIND).count() == 2


def test_ordinary_later_import_does_not_invalidate_reviewed_past_interval(snapshot_case):
    old, witness, _ = snapshot_case
    p = proof(snapshot_case)
    approve(snapshot_case, p); db.session.commit()
    raw = db.session.get(ConnectionResponses, witness.response_id).response.encode('latin-1')
    extra = b'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260904000000<TRNAMT>-10.00<FITID>future-expense<NAME>Future expense</STMTTRN>'
    raw = raw.replace(b'</BANKTRANLIST>',extra+b'</BANKTRANLIST>')
    raw = re.sub(rb'<BALAMT>[^<]+', b'<BALAMT>'+str(witness.balance_amount-10).encode(), raw)
    raw = raw.replace(b'<DTASOF>20260904',b'<DTASOF>20260905').replace(b'<DTEND>20260904',b'<DTEND>20260905')
    import_chase_qfx(raw,reviewed_usd=True)
    post_pair(timestamp=datetime(2026,9,4,tzinfo=timezone.utc),debit='Groceries',credit=rec.account(old.ofx_account_id).name,
        functional_amount='10',transaction_source='ofx',transaction_id=source_identity(db.session, old.ofx_account_id, 'future-expense'))
    db.session.commit()
    assert proof(snapshot_case) == p
    assert timing.reviewed(db.session)[1][0]['valid']
    assert not rec.evidence_differences(db.session)
    # Only the later snapshot acquires a new difference, not the reviewed one.
    differences=bank_balance_differences(db.session)
    assert len(differences)==1 and differences[0]['as_of'].date()==date(2026,9,4)


def test_snapshot_routes_require_admin_csrf_and_post(snapshot_case, client, admin_user, app):
    assert client.post('/reconciliation/snapshot/').status_code == 302
    client.post('/login/',data=admin_user)
    response = client.get('/reconciliation/')
    assert response.status_code == 200 and b'Record this cutoff explanation' in response.data
    assert response.headers['Cache-Control'] == 'no-store'
    assert client.get('/reconciliation/snapshot/').status_code == 405
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post('/reconciliation/snapshot/',data={}).status_code == 400
    finally:
        app.config['WTF_CSRF_ENABLED'] = False
    p = proof(snapshot_case)
    response = client.post('/reconciliation/snapshot/',data={k:p[k] for k in ('original_id','witness_id','statement_id','digest')} | {'reason':'Reviewed fixture source exports'})
    assert response.status_code == 302
    assert not bank_balance_differences(db.session)
