"""Real parser regressions for every OFX transport's acceptance boundary."""

from io import BytesIO
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import ofx_file_import
from pacioli.models import BankFeedAccounts, ConnectionResponses, Connections, OFXFileImports
from pacioli.secure_fields import PREFIX
from tests.test_chase_qfx import _qfx, isolate_imported_accounts


@pytest.fixture
def direct_connection(clean_ledger):
    connection = Connections(source='ofx', type='Checking', routing_number='021000021',
        account_number=uuid4().hex[:16], url='https://bank.invalid', org='Fixture Bank', fid='1',
        user='fixture', password='fixture', currency='USD')
    db.session.add(connection)
    db.session.commit()
    return connection


def _transport(monkeypatch, content):
    from pacioli.functions import ofx_functions

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def statement_request(self, *args, **kwargs):
            return object()

        def download(self, request):
            return BytesIO(content)

    monkeypatch.setattr(ofx_functions, 'OFXClient', Client)
    return ofx_functions.sync_ofx_connection


def _financial_snapshot():
    """Rejected responses may add evidence, but no financial or accepted rows."""
    tables = ('ofx.acctfrom', 'ofx.bankacctfrom', 'ofx.ccacctfrom', 'ofx.stmttrn',
              'ofx.ledgerbal', 'ofx.availbal', 'bookkeeping.bank_feed_accounts',
              'bookkeeping.subaccounts', 'admin.ofx_file_imports')
    return {table: sorted(repr(tuple(row)) for row in db.session.execute(text('SELECT * FROM ' + table)))
            for table in tables}


def _assert_rejection(connection_id, content):
    response = db.session.query(ConnectionResponses).filter_by(connection_id=connection_id).order_by(
        ConnectionResponses.id.desc()).first()
    assert response.import_status == 'rejected'
    assert response.import_error
    assert response.response.encode('latin-1') == content
    stored = db.session.execute(text('SELECT response FROM admin.connection_responses WHERE id=:id'),
                                {'id': response.id}).scalar()
    assert stored.startswith(PREFIX)
    assert content.decode('latin-1') not in stored


def test_direct_ofx_import_and_file_replay_share_provenance(direct_connection, monkeypatch):
    content = _qfx(direct_connection.account_number)
    sync = _transport(monkeypatch, content)
    assert sync(direct_connection) is True
    assert direct_connection.synced_at is not None
    imported = db.session.query(OFXFileImports).filter_by(connection_id=direct_connection.id).one()
    assert db.session.get(BankFeedAccounts, imported.ofx_account_id).currency == 'USD'
    snapshot = _financial_snapshot()
    assert sync(direct_connection) is True
    assert _financial_snapshot() == snapshot
    assert ofx_file_import.import_chase_qfx(content, reviewed_usd=True)['duplicate']
    assert _financial_snapshot() == snapshot
    assert all(row.import_status == 'accepted' for row in
               db.session.query(ConnectionResponses).filter_by(connection_id=direct_connection.id))


@pytest.mark.parametrize('defect', ['currency', 'account', 'routing', 'type', 'multiple',
                                     'signon_status', 'statement_status', 'missing_status',
                                     'nan_amount', 'fractional_amount'])
def test_direct_ofx_rejects_invalid_envelope_without_financial_changes(
        direct_connection, monkeypatch, defect):
    content = _qfx(direct_connection.account_number)
    if defect == 'currency':
        content = content.replace(b'<CURDEF>USD', b'<CURDEF>EUR')
    elif defect == 'account':
        content = content.replace(direct_connection.account_number.encode(), b'wrong-account')
    elif defect == 'routing':
        content = content.replace(b'021000021', b'021000022')
    elif defect == 'type':
        content = content.replace(b'<ACCTTYPE>CHECKING', b'<ACCTTYPE>SAVINGS')
    elif defect == 'multiple':
        start, end = content.index(b'<STMTTRNRS>'), content.index(b'</STMTTRNRS>') + len(b'</STMTTRNRS>')
        content = content[:end] + content[start:end] + content[end:]
    elif defect in ('signon_status', 'statement_status'):
        chunks = content.split(b'<CODE>0')
        index = 1 if defect == 'signon_status' else 2
        chunks[index] = chunks[index].replace(b'<SEVERITY>INFO', b'<SEVERITY>ERROR', 1)
        content = chunks[0] + b''.join((b'<CODE>15500' if i == index else b'<CODE>0') + chunk
                                      for i, chunk in enumerate(chunks[1:], 1))
    elif defect == 'missing_status':
        content = content.replace(b'<STATUS><CODE>0<SEVERITY>INFO</STATUS>', b'', 1)
    elif defect == 'nan_amount':
        content = content.replace(b'<TRNAMT>100.00', b'<TRNAMT>NaN')
    else:
        content = content.replace(b'<TRNAMT>100.00', b'<TRNAMT>100.001')
    before = _financial_snapshot()
    with pytest.raises(ValueError):
        _transport(monkeypatch, content)(direct_connection)
    assert _financial_snapshot() == before
    assert direct_connection.synced_at is None
    _assert_rejection(direct_connection.id, content)


@pytest.mark.parametrize('field', ['amount', 'date', 'description', 'memo'])
def test_direct_ofx_rejects_changed_fitid_contents(direct_connection, monkeypatch, field):
    content = _qfx(direct_connection.account_number)
    assert _transport(monkeypatch, content)(direct_connection)
    before = _financial_snapshot()
    old, new = {'amount': (b'100.00', b'999.00'),
                'date': (b'<DTPOSTED>20260901000000', b'<DTPOSTED>20260902000000'),
                'description': (b'<NAME>Deposit', b'<NAME>Changed'),
                'memo': (b'<NAME>Deposit', b'<NAME>Deposit<MEMO>Changed')}[field]
    changed = content.replace(old, new)
    with pytest.raises(ValueError, match='conflicts with its stored record'):
        _transport(monkeypatch, changed)(direct_connection)
    assert _financial_snapshot() == before
    _assert_rejection(direct_connection.id, changed)


def test_direct_ofx_rejects_registration_conflict_after_instantiation(direct_connection, monkeypatch):
    content = _qfx(direct_connection.account_number)
    assert _transport(monkeypatch, content)(direct_connection)
    imported = db.session.query(OFXFileImports).filter_by(connection_id=direct_connection.id).one()
    db.session.get(BankFeedAccounts, imported.ofx_account_id).currency = 'EUR'
    db.session.commit()
    before = _financial_snapshot()
    changed = content.replace(b'expense-1', b'new-expense')
    with pytest.raises(ValueError, match='conflicting currency registration'):
        _transport(monkeypatch, changed)(direct_connection)
    assert _financial_snapshot() == before
    _assert_rejection(direct_connection.id, changed)


def test_direct_ofx_rolls_back_failure_after_source_creation(direct_connection, monkeypatch):
    content = _qfx(direct_connection.account_number)
    before = _financial_snapshot()
    verify = ofx_file_import._verify_transactions

    def fail_after_creation(statement, expected, aliases):
        verify(statement, expected, aliases)
        ofx_file_import.OFX_Session.flush()
        raise RuntimeError('injected failure after source insertion')

    monkeypatch.setattr(ofx_file_import, '_verify_transactions', fail_after_creation)
    with pytest.raises(RuntimeError, match='injected failure'):
        _transport(monkeypatch, content)(direct_connection)
    assert _financial_snapshot() == before
    assert direct_connection.synced_at is None
    _assert_rejection(direct_connection.id, content)


def test_reviewed_eur_is_preserved_instead_of_relabelled(direct_connection, monkeypatch):
    direct_connection.currency = 'EUR'
    db.session.commit()
    content = _qfx(direct_connection.account_number).replace(b'<CURDEF>USD', b'<CURDEF>EUR')
    assert _transport(monkeypatch, content)(direct_connection)
    imported = db.session.query(OFXFileImports).filter_by(connection_id=direct_connection.id).one()
    assert db.session.get(BankFeedAccounts, imported.ofx_account_id).currency == 'EUR'


def test_direct_and_file_imports_serialize_the_same_sources(app, direct_connection):
    content = _qfx(direct_connection.account_number)
    connection_id = direct_connection.id
    expected = {'type': 'CHECKING', 'account_number': direct_connection.account_number,
                'routing_number': direct_connection.routing_number}

    def import_once(direct):
        with app.app_context():
            if direct:
                return ofx_file_import.import_ofx_statement(content, currency='USD',
                    connection_id=connection_id, expected_account=expected)
            return ofx_file_import.import_chase_qfx(content, reviewed_usd=True)

    # Bounded integration concurrency exercises the shared transaction lock;
    # the local CI runner itself still executes validation jobs serially.
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(import_once, [True, False]))
    assert sorted(result['duplicate'] for result in results) == [False, True]
    assert len({result['account_id'] for result in results}) == 1
    account_id = results[0]['account_id']
    assert db.session.execute(text('SELECT count(*) FROM ofx.stmttrn WHERE acctfrom_id=:id'),
                              {'id': account_id}).scalar() == 2
    assert db.session.query(OFXFileImports).filter_by(ofx_account_id=account_id).count() == 1
