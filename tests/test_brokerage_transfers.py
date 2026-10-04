"""Checking <-> brokerage transfers match like bank transfers; OFX legs are unchanged."""
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import reconciliation as r
from pacioli.functions.accounting_report import audit_ledger, statement
from pacioli.functions.brokerage_accounts import register_account
from pacioli.functions.brokerage_posting import post, preview
from pacioli.functions.cash_flow import cash_flow_report
from pacioli.functions.cash_flow_evidence import cash_flow_context
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.source_identity import source_identity
from pacioli.models import BankFeedAccounts, JournalEntries


def ofx_leg(name, amount, day, description):
    # A fresh feed each call: ofx.acctfrom outlives clean_ledger, its registration does not.
    account_id = db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) "
                                         "VALUES('bankacctfrom',:name) RETURNING id"), {'name': name}).scalar()
    db.session.add(BankFeedAccounts(ofx_account_id=account_id, currency='USD'))
    db.session.flush()
    amount, fitid = Decimal(amount), uuid4().hex
    when = datetime(2026, 1, day, 12, tzinfo=timezone.utc)
    db.session.execute(text('''INSERT INTO ofx.stmttrn(fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES(:fitid,:aid,'OTHER',:when,:amount,:name)'''),
        dict(fitid=fitid, aid=account_id, when=when, amount=amount, name=description))
    entry = post_pair(timestamp=when, debit=name if amount > 0 else 'Transfer Clearing',
                      credit='Transfer Clearing' if amount > 0 else name, functional_amount=abs(amount),
                      transaction_source='ofx', transaction_id=source_identity(db.session, account_id, fitid))
    db.session.flush()
    return entry


@pytest.fixture
def brokerage(clean_ledger):
    account_id = register_account(db.session, provider='schwab', label='Xfer', masked_number='777',
                                  opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    row = r.evidence('schwab_source', {'fixture': uuid4().hex}, 'owner@example.com')
    export_id = db.session.execute(text('''INSERT INTO investments.brokerage_exports
        (account_id, kind, format, evidence_id, sha256, row_count, state, reviewer)
        VALUES (:account, 'transactions', 'json', :evidence, :sha, 0, 'active', 'owner@example.com')
        RETURNING id'''), dict(account=account_id, evidence=row.id, sha=uuid4().hex * 2)).scalar()
    source = db.session.execute(text("INSERT INTO investments.brokerage_sources(account_id, kind) "
                                     "VALUES (:account, 'activity') RETURNING source_id, public_id"),
                                {'account': account_id}).first()
    db.session.execute(text('''INSERT INTO investments.brokerage_activity
        (source_id, account_id, posted_on, action, description, amount, economic_digest, day_ordinal, first_export_id)
        VALUES (:source, :account, :day, 'MoneyLink Transfer', 'Tfr CHASE CHECKING', 500.00, :digest, 1, :export)'''),
        dict(source=source.source_id, account=account_id, day=date(2026, 1, 6),
             digest=sha256(uuid4().bytes).hexdigest(), export=export_id))
    db.session.commit()
    summary, _ = preview(db.session)
    post(summary['digest'], 'owner@example.com', 'Fixture posting')
    schwab = db.session.query(JournalEntries).filter_by(transaction_source='schwab').one()
    return schwab


def test_checking_to_brokerage_transfer_matches_and_is_investing(brokerage):
    checking = ofx_leg('Chase Checking', '-500.00', 5, 'ONLINE TRANSFER TO SCHWAB')
    db.session.commit()
    pairs = r.transfer_candidates()
    assert len(pairs) == 1 and pairs[0]['unique']
    accounts = {leg['account_id'] for leg in pairs[0]['legs']}
    assert 'schwab:1' in accounts
    r.match_transfer(checking.id, brokerage.id, pairs[0]['digest'], 'owner', 'Own Schwab brokerage account')
    db.session.commit()
    assert {checking.id, brokerage.id} <= r.resolved_transfer_ids(db.session)
    assert r.unresolved_transfers() == []
    assert statement(db.session, 'YYYY-MM', '2026-01')['suspense_entries'] == 0
    assert not [i for i in audit_ledger(db.session) if i['kind'] in ('transfer_evidence', 'classification_pending')]
    start, end = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)
    context = cash_flow_context(db.session, start, end)
    assert context['transfer_kinds'][checking.id]['kind'] == 'investment_transfer'
    report = cash_flow_report(db.session, start, end, transfer_kinds=context['transfer_kinds'])
    investing = next(section for section in report['sections'] if section['key'] == 'investing')
    labels = {row['label']: row['amount'] for row in investing['outflows']}
    assert labels == {'Transfers with brokerage accounts': Decimal('500.00')}


def test_tampered_brokerage_leg_reopens_the_transfer(brokerage):
    checking = ofx_leg('Chase Checking', '-500.00', 5, 'ONLINE TRANSFER TO SCHWAB')
    db.session.commit()
    pair = r.transfer_candidates()[0]
    r.match_transfer(checking.id, brokerage.id, pair['digest'], 'owner', 'Own Schwab brokerage account')
    db.session.commit()
    db.session.execute(text("UPDATE bookkeeping.journal_entries SET timestamp=timestamp + interval '1 day' "
                            "WHERE id=:id"), {'id': brokerage.id})
    db.session.commit()
    assert brokerage.id not in r.resolved_transfer_ids(db.session)
    assert any(i['kind'] == 'brokerage_source' and i['scope'] == 'ledger' for i in audit_ledger(db.session))


def test_ofx_leg_shape_is_unchanged(clean_ledger):
    left = ofx_leg('Chase Checking', '-50.00', 2, 'CARD AUTOPAY')
    right = ofx_leg('Chase Savings', '50.00', 3, 'PAYMENT THANK YOU')
    db.session.commit()
    legs = r.transfer_pair(left, right)
    for leg, entry in zip(legs, (left, right)):
        source = db.session.execute(text('SELECT * FROM ofx.transactions WHERE id=:id'),
                                    {'id': entry.transaction_id}).first()
        # Exactly the pre-brokerage shape: existing match fingerprints must stay valid.
        assert leg == dict(journal_id=entry.id, transaction_id=source.id, account_id=source.account_id,
                           account=source.account, amount=str(source.amount),
                           date=r.utc(source.date).isoformat(), description=source.description)
