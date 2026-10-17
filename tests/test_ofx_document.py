"""The SPARK OFX program in ofx/: parsing, rejection, requests, and the text migration."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import random
import subprocess
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import ofx_document
from pacioli.functions.ofx_document import Account, Balance, Currency, OFXError
from tests.test_migrations import revision_module


HEADER = '''OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

'''

CARD = '''<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20260904000000<LANGUAGE>ENG<FI><ORG>B1<FID>10898</FI><INTU.BID>10898</SONRS></SIGNONMSGSRSV1>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS><CCSTMTRS>
<CURDEF>USD
<CCACCTFROM><ACCTID>4111000011112222</CCACCTFROM>
<BANKTRANLIST><DTSTART>20260901000000<DTEND>20260903000000
{transactions}
</BANKTRANLIST>
<LEDGERBAL><BALAMT>575.00<DTASOF>20260903000000</LEDGERBAL>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>
'''

TRANSACTIONS = ('<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260903120000[0:GMT]<TRNAMT>-1.50<FITID>f1'
                '<NAME>AT&amp;T &#233; A&B</STMTTRN>\n'
                '<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260903010000.5[-5.5:IST]<TRNAMT>+07,5'
                '<FITID>f2<NAME>X<MEMO>caf\xe9 \u20ac<CHECKNUM>0042</STMTTRN>')


def card(transactions=TRANSACTIONS):
    return (HEADER + CARD.format(transactions=transactions)).encode('cp1252')


def test_reads_an_ofx_1_card_statement():
    document = ofx_document.parse(card())
    assert document.header['version'] == 102 and document.header['charset'] == '1252'
    assert document.signon.status.success and (document.signon.org, document.signon.fid) == ('B1', '10898')
    (response,) = document.responses
    assert response.kind == 'creditcard' and response.trnuid == '1' and response.status.success
    statement = response.statement
    assert statement.currency == 'USD'
    assert statement.account == Account(acctid='4111000011112222', acctkey=None)
    first, second = statement.transactions.transactions
    assert first.name == 'AT&T \xe9 A&B'
    assert str(first.trnamt) == '-1.50'
    assert first.dtposted == datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    # 01:00:00.5 at UTC-5:30 is 06:30:00.5 UTC; the comma is a decimal point.
    assert second.dtposted == datetime(2026, 9, 3, 6, 30, 0, 500000, tzinfo=timezone.utc)
    assert str(second.trnamt) == '7.5' and second.memo == 'caf\xe9 €' and second.checknum == '0042'
    assert statement.ledger == Balance(Decimal('575.00'), datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert statement.available is None


XML = '''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
<DTSERVER>20260904000000</DTSERVER><LANGUAGE>ENG</LANGUAGE></SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
<STMTRS><CURDEF>EUR</CURDEF>
<BANKACCTFROM><BANKID>021000021</BANKID><ACCTID>9876</ACCTID><ACCTTYPE>SAVINGS</ACCTTYPE></BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260901</DTSTART><DTEND>20260903</DTEND>
<STMTTRN><TRNTYPE>INT</TRNTYPE><DTPOSTED>20260902</DTPOSTED><TRNAMT>0.42</TRNAMT><FITID>i1</FITID>
<NAME>Zinsen für Sparkonto</NAME><MEMO/><CURRENCY><CURRATE>1.0825</CURRATE><CURSYM>USD</CURSYM></CURRENCY>
</STMTTRN></BANKTRANLIST>
<LEDGERBAL><BALAMT>1000.42</BALAMT><DTASOF>20260903</DTASOF></LEDGERBAL>
<AVAILBAL><BALAMT>1000.00</BALAMT><DTASOF>20260903</DTASOF></AVAILBAL>
<BALLIST><BAL><NAME>Interest</NAME><DESC>YTD</DESC><BALTYPE>DOLLAR</BALTYPE><VALUE>3.10</VALUE></BAL></BALLIST>
<MKTGINFO>Save more</MKTGINFO></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>'''


def test_reads_an_ofx_2_bank_statement_and_skips_informational_aggregates():
    document = ofx_document.parse(XML.encode('utf-8'))
    assert document.header['major'] == 2 and document.header['version'] == 220
    (response,) = document.responses
    statement = response.statement
    assert response.kind == 'bank' and statement.currency == 'EUR'
    assert statement.account == Account(acctid='9876', acctkey=None, bankid='021000021',
                                        branchid=None, accttype='SAVINGS')
    (interest,) = statement.transactions.transactions
    assert interest.name == 'Zinsen für Sparkonto' and interest.memo is None
    assert interest.currency == Currency('CURRENCY', Decimal('1.0825'), 'USD')
    assert statement.available == Balance(Decimal('1000.00'), datetime(2026, 9, 3, tzinfo=timezone.utc))


SECRET = 'SECRETVALUE'


@pytest.mark.parametrize('before, after, code, path', [
    ('OFXHEADER:100', 'OFXHEADER:200', 'header_invalid', ''),
    ('</OFX>', '<INVSTMTMSGSRSV1></INVSTMTMSGSRSV1></OFX>', 'element_unsupported', 'OFX/INVSTMTMSGSRSV1'),
    ('<CURDEF>USD', '<CURDEF>USD<CURDEF>USD', 'element_duplicate', 'CCSTMTRS/CURDEF'),
    ('</CCSTMTRS>', '</STMTRS>', 'syntax_invalid', 'CCSTMTRS/STMTRS'),
    ('</OFX>', '', 'syntax_invalid', ''),
    ('</OFX>', '</OFX><OFX>', 'syntax_invalid', 'OFX'),
    ('<CURDEF>USD', '<CURDEF>USD ' + SECRET, 'value_invalid', 'CCSTMTRS/CURDEF'),
    ('<TRNAMT>-1.50', '<TRNAMT>1e3', 'value_invalid', 'STMTTRN/TRNAMT'),
    ('<TRNAMT>-1.50', '<TRNAMT>NaN', 'value_invalid', 'STMTTRN/TRNAMT'),
    ('<DTPOSTED>20260903120000[0:GMT]', '<DTPOSTED>20260230', 'value_invalid', 'STMTTRN/DTPOSTED'),
    ('<DTPOSTED>20260903120000[0:GMT]', '<DTPOSTED>20260903120000[-15]', 'value_invalid', 'STMTTRN/DTPOSTED'),
    ('<NAME>X', '<NAME>' + SECRET * 3, 'value_invalid', 'STMTTRN/NAME'),
    ('<NAME>X', '<NAME>X\x81', 'value_invalid', 'STMTTRN/NAME'),
    ('<NAME>X', '<NAME>X&#0;', 'value_invalid', 'STMTTRN/NAME'),
    ('<TRNTYPE>DEBIT', '<TRNTYPE>HOLD', 'value_invalid', 'STMTTRN/TRNTYPE'),
    ('<NAME>X', '<NAME>X<PAYEE><NAME>Y</PAYEE>', 'element_unsupported', 'STMTTRN/PAYEE'),
    ('</CCACCTFROM>', '</CCACCTFROM>' + SECRET, 'syntax_invalid', 'CCSTMTRS'),
    ('<CURDEF>USD', '<CURDEF>USD<BANKACCTFROM>', 'element_unsupported', 'CCSTMTRS/BANKACCTFROM'),
])
def test_rejections_name_the_element_and_never_a_value(before, after, code, path):
    content = card().decode('latin-1').replace(before, after, 1).encode('latin-1')
    with pytest.raises(OFXError) as error:
        ofx_document.parse(content)
    assert error.value.code == code
    assert error.value.path.endswith(path)
    assert SECRET not in str(error.value) and SECRET not in error.value.path


def test_private_extensions_are_ignored_at_any_depth_within_limits():
    nested = '<INTU.X>' * 20 + '<INTU.Y>1' + '</INTU.X>' * 20
    content = card().replace(b'<INTU.BID>10898', nested.encode())
    assert ofx_document.parse(content).signon.org == 'B1'
    too_deep = '<INTU.X>' * 40 + '<INTU.Y>1' + '</INTU.X>' * 40
    with pytest.raises(OFXError, match='limit_exceeded'):
        ofx_document.parse(card().replace(b'<INTU.BID>10898', too_deep.encode()))


def test_statement_count_and_input_size_are_bounded():
    response = CARD.split('<CREDITCARDMSGSRSV1>')[1].split('</CREDITCARDMSGSRSV1>')[0]
    many = CARD.replace(response, response * 33).format(transactions='')
    with pytest.raises(OFXError, match='limit_exceeded'):
        ofx_document.parse((HEADER + many).encode())
    with pytest.raises(OFXError, match='input_too_large'):
        ofx_document.parse(card() + b' ' * (10 * 1024 * 1024))


def test_dates_and_amounts_agree_with_python_arithmetic():
    rng = random.Random(20261016)
    expected, transactions = [], []
    for index in range(300):
        local = (datetime(rng.randint(1950, 2090), 1, 1) +
                 timedelta(seconds=rng.randint(0, 365 * 86400 - 1), milliseconds=rng.choice([0, 1, 500, 999])))
        hours = rng.choice(['0', '-5', '+5.5', '-3.5', '14', '-12', '9.75', '-0.25', '+1'])
        fraction = '.{:03d}'.format(local.microsecond // 1000) if local.microsecond else ''
        posted = local.strftime('%Y%m%d%H%M%S') + fraction + '[{}:ZONE]'.format(hours)
        sign = rng.choice(['', '+', '-'])
        whole = rng.choice(['', '0', '00', '7', '0012', '123456789012345678'])
        decimals = rng.choice(['', '5', '50', '05', '12345678'])
        point = rng.choice(['.', ','])
        amount = sign + (whole or '0') + (point + decimals if decimals else '')
        utc = (local - timedelta(minutes=round(float(hours) * 60))).replace(tzinfo=timezone.utc)
        expected.append((utc, Decimal(amount.replace(',', '.'))))
        transactions.append('<STMTTRN><TRNTYPE>OTHER<DTPOSTED>{}<TRNAMT>{}<FITID>t{}</STMTTRN>'
                            .format(posted, amount, index))
    parsed = ofx_document.parse(card('\n'.join(transactions))).responses[0].statement.transactions
    assert [(t.dtposted, t.trnamt) for t in parsed.transactions] == expected
    # The digits are kept as written, apart from the sign of zero and leading zeros.
    assert all(t.trnamt.as_tuple().exponent == amount.as_tuple().exponent
               for t, (_, amount) in zip(parsed.transactions, expected))


def test_mutated_documents_are_read_or_rejected_and_never_crash():
    rng = random.Random(4)
    base = card()
    for _ in range(300):
        data = bytearray(base)
        for _ in range(rng.randint(1, 6)):
            position = rng.randrange(len(data))
            operation = rng.randrange(4)
            if operation == 0:
                data[position] = rng.randrange(256)
            elif operation == 1:
                del data[position:position + rng.randint(1, 20)]
            elif operation == 2:
                data[position:position] = rng.choice([b'<', b'>', b'&', b'</', b'<STMTTRN>', b'&#x10FFFF;', b'\xff'])
            else:
                start = rng.randrange(len(data))
                data[position:position] = data[start:start + rng.randint(1, 200)]
        result = subprocess.run([str(ofx_document.binary()), 'parse'], input=bytes(data),
                                capture_output=True, env={}, timeout=10)
        assert result.returncode in (0, 2), result.stderr
        json.loads(result.stdout if result.returncode == 0 else result.stderr)


REQUEST = dict(dtclient='20261016120000', userid='user', userpass='p&ss<w>rd', org='Bank', fid='1234',
               appid='QWIN', appver='2500', trnuid='0f8fad5b-d9cb-469f-a165-70867728950e',
               newfileuid='7c9e6679-7425-40de-944b-e07fc1f90ae7')


def test_statement_requests_follow_the_ofx_2_2_layout():
    body = ofx_document.statement_request(account='bank', bankid='021000021', acctid='123456',
                                          accttype='CHECKING', dtstart='20260901', dtend='20261016',
                                          **REQUEST)
    assert body == (
        b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\r\n'
        b'<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" '
        b'NEWFILEUID="7c9e6679-7425-40de-944b-e07fc1f90ae7"?>'
        b'<OFX><SIGNONMSGSRQV1><SONRQ><DTCLIENT>20261016120000</DTCLIENT><USERID>user</USERID>'
        b'<USERPASS>p&amp;ss&lt;w&gt;rd</USERPASS><LANGUAGE>ENG</LANGUAGE>'
        b'<FI><ORG>Bank</ORG><FID>1234</FID></FI><APPID>QWIN</APPID><APPVER>2500</APPVER>'
        b'</SONRQ></SIGNONMSGSRQV1><BANKMSGSRQV1><STMTTRNRQ>'
        b'<TRNUID>0f8fad5b-d9cb-469f-a165-70867728950e</TRNUID><STMTRQ><BANKACCTFROM>'
        b'<BANKID>021000021</BANKID><ACCTID>123456</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE>'
        b'</BANKACCTFROM><INCTRAN><DTSTART>20260901</DTSTART><DTEND>20261016</DTEND>'
        b'<INCLUDE>Y</INCLUDE></INCTRAN></STMTRQ></STMTTRNRQ></BANKMSGSRQV1></OFX>')
    card_body = ofx_document.statement_request(account='creditcard', acctid='4111', **REQUEST)
    assert (b'<CREDITCARDMSGSRQV1><CCSTMTTRNRQ><TRNUID>0f8fad5b-d9cb-469f-a165-70867728950e</TRNUID>'
            b'<CCSTMTRQ><CCACCTFROM><ACCTID>4111</ACCTID></CCACCTFROM><INCTRAN><INCLUDE>Y</INCLUDE>'
            b'</INCTRAN></CCSTMTRQ></CCSTMTTRNRQ></CREDITCARDMSGSRQV1></OFX>') in card_body


@pytest.mark.parametrize('change', [
    dict(bankid='02100002'), dict(accttype='CD'), dict(userid=''), dict(userpass=None),
    dict(account='creditcard'), dict(dtstart='2026-09-01'), dict(trnuid='not a uuid'),
    dict(userid='x' * 33), dict(fid='1', org=None),
])
def test_invalid_statement_requests_are_refused(change):
    fields = dict(REQUEST, account='bank', bankid='021000021', acctid='123456', accttype='CHECKING')
    fields.update(change)
    with pytest.raises(ValueError):
        ofx_document.statement_request(**fields)
    with pytest.raises(ValueError, match='line breaks'):
        ofx_document.statement_request(**dict(fields, userpass='a\nb'))


TRICKY = ['A &amp; B', 'AT&T', 'X &amp;amp; Y', '&#233;t&#xE9;', '&nbsp;', '&#;', 'a&b;c', '&AMP;',
          '&#0065;&#x42;', '&apos;&quot;&lt;&gt;']


def test_the_migration_decodes_text_exactly_as_the_reader_does():
    decode = revision_module('0003_decode_ofx_text').decode
    transactions = ''.join('<STMTTRN><TRNTYPE>OTHER<DTPOSTED>20260902<TRNAMT>1<FITID>r{}<NAME>{}</STMTTRN>'
                           .format(i, name) for i, name in enumerate(TRICKY))
    parsed = ofx_document.parse(card(transactions)).responses[0].statement.transactions.transactions
    assert [t.name for t in parsed] == [decode(name) for name in TRICKY]


def test_the_text_migration_restores_the_original_values_on_downgrade(app_context):
    migration = revision_module('0003_decode_ofx_text')
    db.session.remove()
    with db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                account = connection.execute(text(
                    "INSERT INTO ofx.acctfrom (subclass) VALUES ('ccacctfrom') RETURNING id")).scalar()
                connection.execute(text('INSERT INTO ofx.ccacctfrom (id, acctid) VALUES (:id, :acctid)'),
                                   {'id': account, 'acctid': 'm' + uuid4().hex[:12]})
                for index, name in enumerate(TRICKY):
                    connection.execute(text(
                        "INSERT INTO ofx.stmttrn (acctfrom_id, fitid, trntype, dtposted, trnamt, name, memo) "
                        "VALUES (:account, :fitid, 'OTHER', '2026-09-02', 1, :name, :name)"),
                        {'account': account, 'fitid': 'm{}'.format(index), 'name': name})
                keyword = 'kw &amp; ' + uuid4().hex
                mapping = connection.execute(text(
                    "INSERT INTO admin.mappings (source, keyword) VALUES ('ofx', :keyword) RETURNING id"),
                    {'keyword': keyword}).scalar()

                def stored():
                    rows = connection.execute(text(
                        'SELECT name, memo FROM ofx.stmttrn WHERE acctfrom_id = :account ORDER BY fitid'),
                        {'account': account}).all()
                    return rows, connection.execute(text('SELECT keyword FROM admin.mappings WHERE id = :id'),
                                                    {'id': mapping}).scalar()

                migration.upgrade()
                assert stored() == ([(migration.decode(name),) * 2 for name in TRICKY],
                                    keyword.replace('&amp;', '&'))
                migration.downgrade()
                assert stored() == ([(name, name) for name in TRICKY], keyword)
                migration.upgrade()
        finally:
            transaction.rollback()
