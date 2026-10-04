"""Offline profile of Pacioli's native Chromium WebAuthn authenticator.

Uses only a disposable localhost origin. Prints derived metadata; never prints
or persists credential IDs, challenges, public-key coordinates, or private keys.
Synthetic profiles vary one advertised property at a time and are never used
for a Chase request.
"""

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from threading import Thread

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from playwright.sync_api import sync_playwright

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pacioli.functions.chase_browser import (
    PASSKEY_AUTHENTICATOR_OPTIONS, _create_authenticator,
    _validate_passkey_credential)
from scripts.webauthn_profile import (
    decode_cbor, decode_url, parse_attestation, parse_authenticator_data)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>Pacioli localhost WebAuthn profile</title>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _add_authenticator(context, page, options):
    session = context.new_cdp_session(page)
    session.send('WebAuthn.enable', {'enableUI': False})
    authenticator_id = session.send('WebAuthn.addVirtualAuthenticator', {
        'options': dict(options)})['authenticatorId']
    return session, authenticator_id


def _check_client(value, challenge, origin, ceremony):
    client = json.loads(decode_url(value))
    assert client['type'] == ceremony
    assert client['origin'] == origin
    assert decode_url(client['challenge']) == challenge
    assert client.get('crossOrigin') in (None, False)


def _check_key(cose, exported, response):
    assert cose[1] == 2 and cose[3] == -7 and cose[-1] == 1
    assert len(cose[-2]) == len(cose[-3]) == 32
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(cose[-2], 'big'), int.from_bytes(cose[-3], 'big'),
        ec.SECP256R1())
    public = numbers.public_key()
    private = serialization.load_der_private_key(
        base64.b64decode(exported['privateKey'], validate=True), password=None)
    assert isinstance(private, ec.EllipticCurvePrivateKey)
    assert private.public_key().public_numbers() == public.public_numbers()
    if response.get('publicKey'):
        browser_key = serialization.load_der_public_key(
            decode_url(response['publicKey']))
        assert browser_key.public_numbers() == public.public_numbers()
    assert response.get('publicKeyAlgorithm') in (None, -7)
    return public


def _restore(browser, origin, options, exported, expected_id, public):
    restored = browser.new_context()
    try:
        page = restored.new_page()
        session, authenticator_id = _add_authenticator(restored, page, options)
        session.send('WebAuthn.addCredential', {
            'authenticatorId': authenticator_id, 'credential': exported})
        assert len(session.send('WebAuthn.getCredentials', {
            'authenticatorId': authenticator_id})['credentials']) == 1
        asserted = []
        session.on('WebAuthn.credentialAsserted',
                   lambda event: asserted.append(event))
        page.goto(origin)
        challenge = os.urandom(32)
        assertion = page.evaluate('''async challenge => {
            const credential = await navigator.credentials.get({publicKey: {
                challenge: new Uint8Array(challenge), rpId: 'localhost',
                userVerification: 'required', timeout: 30000
            }});
            return credential.toJSON();
        }''', list(challenge))
        assert assertion['id'] == expected_id
        assert assertion['type'] == 'public-key'
        assert len(asserted) == 1
        assert asserted[0]['authenticatorId'] == authenticator_id
        response = assertion['response']
        _check_client(response['clientDataJSON'], challenge, origin,
                      'webauthn.get')
        auth_bytes = decode_url(response['authenticatorData'])
        parsed = parse_authenticator_data(
            auth_bytes, 'localhost', registration=False)
        public.verify(
            decode_url(response['signature']),
            auth_bytes + hashlib.sha256(
                decode_url(response['clientDataJSON'])).digest(),
            ec.ECDSA(hashes.SHA256()))
        updated = session.send('WebAuthn.getCredentials', {
            'authenticatorId': authenticator_id})['credentials']
        assert len(updated) == 1
        stored_update = _validate_passkey_credential(
            updated[0], allowed_rp='localhost')
        assert stored_update['signCount'] == parsed.sign_count
        return parsed, stored_update
    finally:
        restored.close()


def _probe(browser, origin, name, options):
    context = browser.new_context()
    try:
        page = context.new_page()
        authenticator = (_create_authenticator(context, page) if name == 'current'
                         else _add_authenticator(context, page, options))
        session, authenticator_id = authenticator
        added = []
        session.on('WebAuthn.credentialAdded', lambda event: added.append(event))
        page.goto(origin)
        challenge = os.urandom(32)
        result = page.evaluate('''async challenge => {
            const credential = await navigator.credentials.create({publicKey: {
                challenge: new Uint8Array(challenge),
                rp: {name: 'Pacioli localhost test', id: 'localhost'},
                user: {id: new Uint8Array([1, 2, 3, 4]),
                       name: 'test', displayName: 'Test'},
                pubKeyCredParams: [{type: 'public-key', alg: -7}],
                authenticatorSelection: {
                    residentKey: 'required', userVerification: 'required'},
                extensions: {credProps: true},
                timeout: 30000, attestation: 'none'
            }});
            return {
                ...credential.toJSON(),
                browserAuthenticatorData: Array.from(new Uint8Array(
                    credential.response.getAuthenticatorData())),
                browserPublicKeyAlgorithm:
                    credential.response.getPublicKeyAlgorithm(),
                browserTransports: credential.response.getTransports()
            };
        }''', list(challenge))
        response = result['response']
        _check_client(response['clientDataJSON'], challenge, origin,
                      'webauthn.create')
        fmt, statement, registration = parse_attestation(
            response['attestationObject'], 'localhost')
        assert bytes(result['browserAuthenticatorData']) == decode_cbor(
            decode_url(response['attestationObject']))['authData']
        assert fmt == 'none' and not statement
        assert result['type'] == 'public-key'
        assert result.get('authenticatorAttachment') == 'platform'
        assert isinstance(result.get('clientExtensionResults', {}), dict)
        assert result['clientExtensionResults']['credProps']['rk'] is True
        assert result['browserPublicKeyAlgorithm'] == -7
        assert result['browserTransports'] == response.get('transports')
        credentials = session.send('WebAuthn.getCredentials', {
            'authenticatorId': authenticator_id})['credentials']
        assert len(credentials) == len(added) == 1
        assert added[0]['authenticatorId'] == authenticator_id
        exported = credentials[0]
        assert exported['rpId'] == 'localhost'
        assert exported['isResidentCredential'] is True
        assert exported['userHandle']
        assert decode_url(result['rawId']) == registration.credential_id
        assert decode_url(result['rawId']) == base64.b64decode(
            exported['credentialId'], validate=True)
        public = _check_key(registration.cose_key, exported, response)
        expected_be = options.get('defaultBackupEligibility', False)
        expected_bs = options.get('defaultBackupState', False)
        assert exported['backupEligibility'] is expected_be
        assert exported['backupState'] is expected_bs
        stored = _validate_passkey_credential(exported, allowed_rp='localhost')
        assert stored['backupEligibility'] is expected_be
        assert stored['backupState'] is expected_bs
        assert bool(registration.flags & 0x08) is expected_be
        assert bool(registration.flags & 0x10) is expected_bs
        assert not context.storage_state().get('credentials')
        report = {
            'profile': name,
            'ctap_version': options.get('ctap2Version', 'ctap2_0'),
            'transport': options['transport'],
            'attestation_format': fmt,
            'attestation_statement_empty': True,
            'attachment': result.get('authenticatorAttachment'),
            'reported_transports': response.get('transports', []),
            'public_key_algorithm': registration.cose_key[3],
            'client_extension_names': sorted(result.get(
                'clientExtensionResults', {})),
            'registration': registration.report(),
            'credential_exported_in_memory': True,
            'exported_field_names': sorted(exported),
            'stored_field_names': sorted(stored),
        }
    finally:
        context.close()

    after_restore, stored_update = _restore(
        browser, origin, options, stored, result['id'], public)
    assert bool(after_restore.flags & 0x08) is expected_be
    assert bool(after_restore.flags & 0x10) is expected_bs
    assert after_restore.sign_count >= registration.sign_count
    report['restored_assertion'] = after_restore.report()
    report['restored_signature_verified'] = True
    report['backup_flags_preserved_after_restore'] = (
        after_restore.flags & 0x18 == registration.flags & 0x18)
    next_assertion, _ = _restore(browser, origin, options, stored_update,
                                 result['id'], public)
    assert next_assertion.sign_count > after_restore.sign_count
    report['second_restored_assertion_sign_count'] = next_assertion.sign_count
    if name == 'backup_eligible':
        # Check whether CDP preserves backup properties on an imported key
        # even when the new authenticator's defaults differ.
        wrong_profile, _ = _restore(
            browser, origin, PASSKEY_AUTHENTICATOR_OPTIONS,
            stored, result['id'], public)
        report['restored_with_current_profile'] = {
            'backup_eligible': bool(wrong_profile.flags & 0x08),
            'backed_up': bool(wrong_profile.flags & 0x10),
            'signature_verified': True,
            'backup_flags_match_registration': (
                wrong_profile.flags & 0x18 == registration.flags & 0x18),
        }
    return report


def main():
    scratch = os.environ.get('TMPDIR')
    if not scratch or not os.path.isdir(scratch):
        raise RuntimeError('TMPDIR must point to private build scratch')
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = 'http://localhost:{}'.format(server.server_port)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path='/snap/bin/chromium', headless=True,
                chromium_sandbox=True,
                args=['--no-first-run', '--no-default-browser-check'])
            try:
                current = dict(PASSKEY_AUTHENTICATOR_OPTIONS)
                ctap21 = dict(current, ctap2Version='ctap2_1')
                backup_eligible = dict(current, defaultBackupEligibility=True,
                                       defaultBackupState=False)
                backed_up = dict(backup_eligible, defaultBackupState=True)
                reports = [
                    _probe(browser, origin, 'current', current),
                    _probe(browser, origin, 'ctap2_1', ctap21),
                    _probe(browser, origin, 'backup_eligible', backup_eligible),
                    _probe(browser, origin, 'backed_up_simulated', backed_up),
                ]
                print(json.dumps({'browser_version': browser.version,
                                  'profiles': reports}, indent=2,
                                 sort_keys=True))
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
