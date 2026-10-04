"""Known WebAuthn wire-format cases for the localhost credential probe."""

import base64
import hashlib

import pytest

from scripts.webauthn_profile import (
    decode_cbor, decode_url, parse_attestation, parse_authenticator_data)


def _bytes(value):
    assert len(value) < 256
    return b'\x58' + bytes([len(value)]) + value


def _auth_data(flags):
    # A structurally valid ES256 COSE key is enough for the parser fixture;
    # the browser probe separately verifies coordinates against the actual key.
    cose = (b'\xa5\x01\x02\x03\x26\x20\x01\x21\x58\x20' + b'X' * 32 +
            b'\x22\x58\x20' + b'Y' * 32)
    return (hashlib.sha256(b'localhost').digest() + bytes([flags]) +
            b'\x00\x00\x00\x00' + b'\x11' * 16 + b'\x00\x03' +
            b'key' + cose)


def test_registration_profile_extracts_flags_without_reporting_id_or_key():
    auth = _auth_data(0x4d)  # UP + UV + BE + AT
    attestation = (b'\xa3\x63fmt\x64none\x68authData' + _bytes(auth) +
                   b'\x67attStmt\xa0')
    encoded = base64.urlsafe_b64encode(attestation).decode().rstrip('=')
    fmt, statement, parsed = parse_attestation(encoded, 'localhost')
    assert fmt == 'none' and statement == {}
    assert parsed.credential_id == b'key'
    assert parsed.cose_key[3] == -7
    assert parsed.report() == {
        'user_present': True, 'user_verified': True,
        'backup_eligible': True, 'backed_up': False,
        'attested_credential_data': True, 'extension_data': False,
        'sign_count': 0, 'aaguid': '11' * 16,
        'credential_id_bytes': 3, 'extension_names': [],
    }
    assert b'key' not in str(parsed.report()).encode()
    assert b'X' * 32 not in str(parsed.report()).encode()


def test_assertion_profile_rejects_invalid_backup_and_presence_flags():
    prefix = hashlib.sha256(b'localhost').digest()
    for flags in (0x14, 0x0c, 0x09):  # BS without BE, missing UP, missing UV
        with pytest.raises(ValueError):
            parse_authenticator_data(prefix + bytes([flags]) +
                                     b'\x00' * 4, 'localhost',
                                     registration=False)
    parsed = parse_authenticator_data(
        prefix + b'\x1d' + b'\x00\x00\x00\x02', 'localhost',
        registration=False)
    assert parsed.report()['backup_eligible'] is True
    assert parsed.report()['backed_up'] is True
    assert parsed.report()['sign_count'] == 2
    with pytest.raises(ValueError, match='wrong relying party'):
        parse_authenticator_data(prefix + b'\x05' + b'\x00' * 4,
                                 'chase.com', registration=False)


@pytest.mark.parametrize('wire', [
    b'\x9f\xff',             # indefinite array
    b'\xa2\x61a\x01\x61a\x02',  # duplicate map key
    b'\x58\x05x',            # truncated bytes
    b'\xa0\x00',             # trailing data
    b'\xd8\x01\x00',       # unsupported tag
])
def test_profile_parser_fails_closed_on_malformed_cbor(wire):
    with pytest.raises(ValueError):
        decode_cbor(wire)


def test_profile_base64url_rejects_invalid_text():
    assert decode_url('AQID') == b'\x01\x02\x03'
    for invalid in ('not base64!', '', 5):
        if invalid == '':
            continue  # Empty text is valid base64, but not a valid attestation.
        with pytest.raises((ValueError, TypeError)):
            decode_url(invalid)
