"""Pacioli's OFX reader and request writer: the SPARK program in ofx/.

The program runs in its own process with no environment, reads the document
on standard input and answers with typed JSON or a structured error. Errors
name the element path and kind, never a value, so they are safe to record.
See ofx/README.md for the grammar and the JSON format.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
FORMAT = 'pacioli-ofx/1'
TIMEOUT_SECONDS = 60


class OFXError(ValueError):
    """The document was rejected; code, path and offset locate the fault."""

    def __init__(self, code, path='', offset=None):
        self.code, self.path, self.offset = code, path, offset
        super().__init__('OFX {} at {}'.format(code, path or 'document'))


def binary():
    return Path(os.environ.get('PACIOLI_OFX_BINARY') or ROOT / 'ofx' / 'build' / 'bin' / 'pacioli-ofx')


def _run(mode, data):
    try:
        result = subprocess.run([str(binary()), mode], input=data, capture_output=True,
                                env={}, timeout=TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('The OFX program could not run: {}'.format(type(error).__name__)) from None
    if result.returncode == 2:
        try:
            error = json.loads(result.stderr)
        except ValueError:
            error = {}
        raise OFXError(error.get('error') or 'rejected', error.get('path') or '', error.get('offset'))
    if result.returncode != 0:
        raise RuntimeError('The OFX program failed with status {}'.format(result.returncode))
    return result.stdout


@dataclass(frozen=True)
class Status:
    code: int | None
    severity: str | None

    @property
    def success(self):
        return self.code == 0 and self.severity == 'INFO'


@dataclass(frozen=True)
class Signon:
    status: Status | None
    dtserver: datetime | None
    language: str | None
    org: str | None
    fid: str | None


@dataclass(frozen=True)
class Currency:
    kind: str                    # CURRENCY or ORIGCURRENCY
    rate: Decimal | None
    symbol: str | None


@dataclass(frozen=True)
class Transaction:
    trntype: str | None
    dtposted: datetime | None
    dtuser: datetime | None
    dtavail: datetime | None
    trnamt: Decimal | None
    fitid: str | None
    correctfitid: str | None
    correctaction: str | None
    srvrtid: str | None
    checknum: str | None
    refnum: str | None
    sic: int | None
    payeeid: str | None
    name: str | None
    memo: str | None
    inv401ksource: str | None
    currency: Currency | None


@dataclass(frozen=True)
class Account:
    acctid: str | None
    acctkey: str | None
    bankid: str | None = None    # bank accounts only
    branchid: str | None = None
    accttype: str | None = None


@dataclass(frozen=True)
class Balance:
    amount: Decimal | None
    asof: datetime | None


@dataclass(frozen=True)
class TransactionList:
    start: datetime | None
    end: datetime | None
    transactions: tuple


@dataclass(frozen=True)
class Statement:
    currency: str | None
    account: Account | None
    transactions: TransactionList | None
    ledger: Balance | None
    available: Balance | None


@dataclass(frozen=True)
class Response:
    """A STMTTRNRS or CCSTMTTRNRS; statement is None when the bank sent none."""
    kind: str                    # bank or creditcard
    trnuid: str | None
    status: Status | None
    statement: Statement | None


@dataclass(frozen=True)
class Document:
    header: dict
    signon: Signon | None
    responses: tuple

    @property
    def statuses(self):
        return [s for s in [self.signon and self.signon.status] + [r.status for r in self.responses] if s]


def _time(value):
    return None if value is None else datetime.fromisoformat(value)


def _money(value):
    return None if value is None else Decimal(value)


def _status(value):
    return None if value is None else Status(value['code'], value['severity'])


def _balance(value):
    return None if value is None else Balance(_money(value['balamt']), _time(value['dtasof']))


def _transaction(value):
    currency = value['currency']
    return Transaction(
        trntype=value['trntype'], dtposted=_time(value['dtposted']), dtuser=_time(value['dtuser']),
        dtavail=_time(value['dtavail']), trnamt=_money(value['trnamt']), fitid=value['fitid'],
        correctfitid=value['correctfitid'], correctaction=value['correctaction'],
        srvrtid=value['srvrtid'], checknum=value['checknum'], refnum=value['refnum'],
        sic=value['sic'], payeeid=value['payeeid'], name=value['name'], memo=value['memo'],
        inv401ksource=value['inv401ksource'],
        currency=None if currency is None else Currency(currency['kind'], _money(currency['currate']),
                                                        currency['cursym']))


def _statement(value):
    if value is None:
        return None
    account, tranlist = value['account'], value['banktranlist']
    return Statement(
        currency=value['curdef'],
        account=None if account is None else Account(
            acctid=account['acctid'], acctkey=account['acctkey'], bankid=account.get('bankid'),
            branchid=account.get('branchid'), accttype=account.get('accttype')),
        transactions=None if tranlist is None else TransactionList(
            _time(tranlist['dtstart']), _time(tranlist['dtend']),
            tuple(_transaction(t) for t in tranlist['transactions'])),
        ledger=_balance(value['ledgerbal']), available=_balance(value['availbal']))


def parse(content):
    """Read one OFX response; raise OFXError when the document is rejected."""
    if not isinstance(content, bytes):
        raise TypeError('OFX content must be bytes')
    value = json.loads(_run('parse', content))
    if value.get('format') != FORMAT:
        raise RuntimeError('The OFX program answered in an unexpected format')
    signon = value['signon']
    return Document(
        header=value['header'],
        signon=None if signon is None else Signon(
            _status(signon['status']), _time(signon['dtserver']), signon['language'],
            (signon['fi'] or {}).get('org'), (signon['fi'] or {}).get('fid')),
        responses=tuple(Response(r['type'], r['trnuid'], _status(r['status']), _statement(r['statement']))
                        for r in value['statements']))


REQUEST_FIELDS = ('account', 'dtclient', 'userid', 'userpass', 'org', 'fid', 'appid', 'appver',
                  'clientuid', 'trnuid', 'newfileuid', 'bankid', 'acctid', 'accttype',
                  'dtstart', 'dtend')


def statement_request(**fields):
    """An OFX 2.2 statement request body; values must not contain line breaks."""
    lines = []
    for key, value in fields.items():
        if key not in REQUEST_FIELDS:
            raise ValueError('Unknown OFX request field')
        if value is None:
            continue
        value = str(value)
        if '\n' in value or '\r' in value:
            raise ValueError('OFX request values cannot contain line breaks')
        lines.append('{}={}'.format(key, value))
    try:
        return _run('request', '\n'.join(lines).encode('utf-8'))
    except OFXError as error:
        raise ValueError('The OFX request is invalid') from error
