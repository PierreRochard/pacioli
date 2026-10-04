"""Strict, bounded parsing for the disposable localhost WebAuthn probe.

Only derived metadata is returned to the report. Credential IDs, challenges,
public-key coordinates, and private keys stay in memory during the probe.
"""

import base64
from dataclasses import dataclass
import hashlib


MAX_CBOR_BYTES = 65536
MAX_CBOR_DEPTH = 8
MAX_CBOR_ITEMS = 64


def decode_url(value):
    if not isinstance(value, str):
        raise ValueError('base64url value must be text')
    return base64.b64decode(value + '=' * (-len(value) % 4),
                            altchars=b'-_', validate=True)


def _cbor_item(data, offset=0, depth=0):
    if depth > MAX_CBOR_DEPTH or offset >= len(data):
        raise ValueError('truncated or deeply nested CBOR')
    first = data[offset]
    offset += 1
    major, extra = first >> 5, first & 31
    if extra < 24:
        length = extra
    elif extra in (24, 25, 26, 27):
        size = {24: 1, 25: 2, 26: 4, 27: 8}[extra]
        if offset + size > len(data):
            raise ValueError('truncated CBOR length')
        length = int.from_bytes(data[offset:offset + size], 'big')
        offset += size
    else:
        raise ValueError('indefinite or reserved CBOR length')
    if major == 0:
        return length, offset
    if major == 1:
        return -1 - length, offset
    if major in (2, 3):
        if length > MAX_CBOR_BYTES or offset + length > len(data):
            raise ValueError('truncated or oversized CBOR string')
        raw = data[offset:offset + length]
        return (raw if major == 2 else raw.decode('utf-8')), offset + length
    if major in (4, 5):
        if length > MAX_CBOR_ITEMS:
            raise ValueError('oversized CBOR container')
        if major == 4:
            result = []
            for _ in range(length):
                value, offset = _cbor_item(data, offset, depth + 1)
                result.append(value)
            return result, offset
        result = {}
        for _ in range(length):
            key, offset = _cbor_item(data, offset, depth + 1)
            value, offset = _cbor_item(data, offset, depth + 1)
            if not isinstance(key, (str, int, bytes)) or key in result:
                raise ValueError('invalid or duplicate CBOR map key')
            result[key] = value
        return result, offset
    if major == 7 and extra in (20, 21, 22):
        return {20: False, 21: True, 22: None}[extra], offset
    raise ValueError('unsupported CBOR type')


def decode_cbor(data):
    if not isinstance(data, bytes) or len(data) > MAX_CBOR_BYTES:
        raise ValueError('invalid CBOR input')
    value, offset = _cbor_item(data)
    if offset != len(data):
        raise ValueError('trailing CBOR bytes')
    return value


@dataclass(frozen=True)
class AuthenticatorData:
    flags: int
    sign_count: int
    aaguid: bytes | None
    credential_id: bytes | None
    cose_key: dict | None
    extension_names: tuple

    def report(self):
        return {
            'user_present': bool(self.flags & 0x01),
            'user_verified': bool(self.flags & 0x04),
            'backup_eligible': bool(self.flags & 0x08),
            'backed_up': bool(self.flags & 0x10),
            'attested_credential_data': bool(self.flags & 0x40),
            'extension_data': bool(self.flags & 0x80),
            'sign_count': self.sign_count,
            'aaguid': self.aaguid.hex() if self.aaguid is not None else None,
            'credential_id_bytes': (len(self.credential_id)
                                    if self.credential_id is not None else None),
            'extension_names': list(self.extension_names),
        }


def parse_authenticator_data(data, rp_id, *, registration):
    if not isinstance(data, bytes) or len(data) < 37:
        raise ValueError('truncated authenticator data')
    if data[:32] != hashlib.sha256(rp_id.encode('utf-8')).digest():
        raise ValueError('wrong relying party')
    flags = data[32]
    if flags & 0x22 or (flags & 0x10 and not flags & 0x08):
        raise ValueError('invalid authenticator flags')
    if not flags & 0x01 or not flags & 0x04:
        raise ValueError('user presence or verification missing')
    if bool(flags & 0x40) != registration:
        raise ValueError('unexpected attested-credential flag')
    sign_count = int.from_bytes(data[33:37], 'big')
    offset = 37
    aaguid = credential_id = cose_key = None
    if registration:
        if len(data) < offset + 18:
            raise ValueError('truncated attested credential')
        aaguid = data[offset:offset + 16]
        offset += 16
        size = int.from_bytes(data[offset:offset + 2], 'big')
        offset += 2
        if not size or offset + size > len(data):
            raise ValueError('invalid credential ID length')
        credential_id = data[offset:offset + size]
        offset += size
        cose_key, offset = _cbor_item(data, offset)
        if not isinstance(cose_key, dict):
            raise ValueError('invalid COSE key')
    extension_names = ()
    if flags & 0x80:
        extensions, offset = _cbor_item(data, offset)
        if not isinstance(extensions, dict) or not all(
                isinstance(key, str) for key in extensions):
            raise ValueError('invalid extension data')
        extension_names = tuple(sorted(extensions))
    if offset != len(data):
        raise ValueError('trailing authenticator data')
    return AuthenticatorData(flags, sign_count, aaguid, credential_id,
                             cose_key, extension_names)


def parse_attestation(value, rp_id):
    decoded = decode_cbor(decode_url(value))
    if not isinstance(decoded, dict) or set(decoded) != {
            'fmt', 'authData', 'attStmt'}:
        raise ValueError('invalid attestation object')
    if not isinstance(decoded['fmt'], str) or not isinstance(
            decoded['attStmt'], dict):
        raise ValueError('invalid attestation fields')
    auth = parse_authenticator_data(decoded['authData'], rp_id,
                                    registration=True)
    return decoded['fmt'], decoded['attStmt'], auth
