"""Offline WebAuthn smoke test for the installed Chromium and Playwright.

This serves only localhost, creates a disposable passkey, restores it in a new
browser context, and verifies a signed assertion. No bank is contacted.
"""

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from playwright.sync_api import sync_playwright


def _decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>Pacioli local WebAuthn test</title>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    if not os.environ.get('TMPDIR'):
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
                context = browser.new_context()
                context.credentials.install()
                page = context.new_page()
                page.goto(origin)
                page.evaluate('''async () => {
                    const challenge = crypto.getRandomValues(new Uint8Array(32));
                    const credential = await navigator.credentials.create({publicKey: {
                        challenge, rp: {name: 'Pacioli local test', id: 'localhost'},
                        user: {id: new Uint8Array([1, 2, 3, 4]), name: 'test', displayName: 'Test'},
                        pubKeyCredParams: [{type: 'public-key', alg: -7}],
                        authenticatorSelection: {residentKey: 'required', userVerification: 'required'},
                        timeout: 30000, attestation: 'none'
                    }});
                    if (!credential) throw new Error('No WebAuthn credential was created');
                }''')
                registered = context.credentials.get(rp_id='localhost')
                assert len(registered) == 1
                credential = registered[0]
                assert not context.storage_state().get('credentials')
                state = context.storage_state(credentials=True)
                assert state.get('credentials')
                context.close()

                restored = browser.new_context(storage_state=state)
                try:
                    assert len(restored.credentials.get(rp_id='localhost')) == 1
                    page = restored.new_page()
                    page.goto(origin)
                    challenge = os.urandom(32)
                    assertion = page.evaluate('''async challenge => {
                        const result = await navigator.credentials.get({publicKey: {
                            challenge: new Uint8Array(challenge), rpId: 'localhost',
                            userVerification: 'required', timeout: 30000
                        }});
                        const bytes = value => Array.from(new Uint8Array(value));
                        return {id: result.id,
                            authenticatorData: bytes(result.response.authenticatorData),
                            clientDataJSON: bytes(result.response.clientDataJSON),
                            signature: bytes(result.response.signature)};
                    }''', list(challenge))
                    assert assertion['id'] == credential['id']
                    authenticator_data = bytes(assertion['authenticatorData'])
                    client_data = bytes(assertion['clientDataJSON'])
                    details = json.loads(client_data)
                    assert details['type'] == 'webauthn.get'
                    assert details['origin'] == origin
                    assert _decode(details['challenge']) == challenge
                    assert authenticator_data[:32] == hashlib.sha256(b'localhost').digest()
                    assert authenticator_data[32] & 0x04  # User verification flag.
                    public_key = serialization.load_der_public_key(
                        _decode(credential['publicKey']))
                    public_key.verify(
                        bytes(assertion['signature']),
                        authenticator_data + hashlib.sha256(client_data).digest(),
                        ec.ECDSA(hashes.SHA256()))
                finally:
                    restored.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print('Offline WebAuthn registration, restore, and signed assertion passed.')


if __name__ == '__main__':
    main()
