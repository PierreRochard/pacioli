"""Bounded, secret-free evidence for a supervised Chase passkey attempt.

The collector never records request bodies, full headers, cookies, URL queries,
credential IDs, private keys, username fields, or raw console messages. It
retains only selected response request IDs and narrowly validated error codes.
The report is stored through EncryptedText and is accessible only to Pacioli
administrators.
"""

from collections import deque
import hashlib
import json
import re
import time
import traceback
from urllib.parse import urlsplit

from pacioli.extensions import db
from pacioli.models import ChaseBrowserRuns


MAX_EVENTS = 120
MAX_PENDING = 24
MAX_RESPONSE_BYTES = 65536
POST_CREDENTIAL_SECONDS = 45
PASSKEY_PATH = re.compile(r'passkey|webauthn|credential|fido', re.I)
SAFE_CODE = re.compile(r'(?:[A-Z][A-Z0-9_-]{1,79}|[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+)\Z')
ERROR_CODE_WORD = re.compile(
    r'passkey|webauthn|fido|attest|credential|auth|error|fail|invalid|'
    r'unsupported|denied|verify|security', re.I)
ERROR_KEYS = frozenset(('errorCode', 'error_code', 'reasonCode',
                        'reason_code', 'statusCode', 'errorType', 'code'))
ERROR_TAGS = ('NotAllowedError', 'SecurityError', 'NetworkError',
              'InvalidStateError', 'NotSupportedError', 'AbortError',
              'TypeError', 'attestation', 'unsupported', 'verification')
REQUEST_ID_HEADERS = ('x-request-id', 'x-correlation-id', 'x-amzn-requestid')
ERROR_FIELD_NAME = re.compile(r'error|message|reason|code|status', re.I)


def _chase_endpoint(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
    except ValueError:
        return None
    if parsed.scheme != 'https' or not (
            host == 'chase.com' or host.endswith('.chase.com')):
        return None
    # A path may contain an account identifier. Keep only a stable digest.
    path = parsed.path or '/'
    return {'host': host,
            'path_hash': hashlib.sha256(path.encode('utf-8')).hexdigest()[:16],
            'passkey_path': bool(PASSKEY_PATH.search(path))}


def _machine_codes(value):
    """Extract bounded error codes, never arbitrary server text or data."""
    found = []

    def visit(item, depth):
        if depth > 5 or len(found) >= 6:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ERROR_KEYS and not isinstance(child, bool):
                    if isinstance(child, int) and 0 <= child <= 9999:
                        found.append({'field': key, 'value': str(child)})
                    elif (isinstance(child, str) and SAFE_CODE.fullmatch(child)
                          and (child.isupper() or ERROR_CODE_WORD.search(child))):
                        found.append({'field': key, 'value': child})
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item[:8]:
                visit(child, depth + 1)

    visit(value, 0)
    return found[:6]


def _error_tag(message):
    for tag in ERROR_TAGS:
        if tag.lower() in message.lower():
            return tag
    return None


def _error_field_names(value):
    """Report an error payload's shape, never its arbitrary values."""
    names = set()

    def visit(item, depth):
        if depth > 3 or len(names) >= 12:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if (isinstance(key, str) and len(key) <= 40 and
                        re.fullmatch(r'[A-Za-z_]+', key) and
                        ERROR_FIELD_NAME.search(key)):
                    names.add(key)
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item[:8]:
                visit(child, depth + 1)

    visit(value, 0)
    return sorted(names)[:12]


def _content_type_category(value):
    value = value.split(';', 1)[0].lower()
    if value in ('application/json', 'application/problem+json'):
        return 'json'
    if value == 'text/html':
        return 'html'
    if value == 'text/plain':
        return 'text'
    return 'other'


def safe_exception_details(error):
    """Identify a worker failure without storing its potentially secret text."""
    frames = traceback.extract_tb(error.__traceback__)[-8:]
    return {'exception_type': type(error).__name__,
            'frames': [{'file': frame.filename.rsplit('/', 1)[-1],
                        'function': frame.name, 'line': frame.lineno}
                       for frame in frames]}


class ChasePasskeyDiagnostics:
    def __init__(self, run_id, browser_major=None):
        self.run_id = run_id
        self.browser_major = browser_major
        self.started = time.monotonic()
        self.events = []
        self.dropped = 0
        self.pending = deque()
        self.recent_responses = deque(maxlen=12)
        self.credential_added_at = None
        self.credential_added_count = 0
        self.credential_asserted_count = 0
        self.last_candidate_count = None
        self.http_status_counts = {}
        self.error_codes = []
        self.last_persist = 0

    def record(self, kind, **details):
        if len(self.events) >= MAX_EVENTS:
            # Keep the latest evidence around the visible Chase error.
            self.events.pop(0)
            self.dropped += 1
        self.events.append({'ms': int((time.monotonic() - self.started) * 1000),
                            'kind': kind, **details})

    def attach(self, context, page, authenticator):
        context.on('response', lambda value: self._safe_event(
            'response', self._on_response, value))
        context.on('requestfinished', lambda value: self._safe_event(
            'requestfinished', self._on_request_finished, value))
        context.on('requestfailed', lambda value: self._safe_event(
            'requestfailed', self._on_request_failed, value))
        page.on('pageerror', lambda value: self._safe_event(
            'pageerror', self._on_page_error, value))
        page.on('console', lambda value: self._safe_event(
            'console', self._on_console, value))
        page.on('framenavigated', lambda value: self._safe_event(
            'navigation', self._on_navigation, value))
        session, authenticator_id = authenticator
        session.on('WebAuthn.credentialAdded',
                   lambda event: self._safe_event(
                       'credential_added', self._credential_event,
                       'credential_added', event, authenticator_id))
        session.on('WebAuthn.credentialAsserted',
                   lambda event: self._safe_event(
                       'credential_asserted', self._credential_event,
                       'credential_asserted', event, authenticator_id))
        self.record('instrumentation_ready', browser_major=self.browser_major,
                    native_authenticator=True)

    def _safe_event(self, source, callback, *args):
        try:
            callback(*args)
        except Exception:
            self.record('collector_error', source=source)

    def _credential_event(self, kind, event, authenticator_id):
        if event.get('authenticatorId') != authenticator_id:
            return
        credential = event.get('credential') or {}
        rp_id = credential.get('rpId')
        safe_rp = (rp_id if isinstance(rp_id, str) and
                   (rp_id == 'chase.com' or rp_id.endswith('.chase.com'))
                   else 'other')
        if kind == 'credential_added':
            self.credential_added_count += 1
            self.credential_added_at = time.monotonic()
            if self.recent_responses:
                self.record('recent_chase_responses',
                            responses=list(self.recent_responses))
        elif kind == 'credential_asserted':
            self.credential_asserted_count += 1
        self.record(kind, rp_id=safe_rp,
                    resident=credential.get('isResidentCredential') is True)

    def _on_navigation(self, frame):
        endpoint = _chase_endpoint(frame.url)
        if endpoint:
            self.record('navigation', **endpoint)

    def _on_page_error(self, error):
        message = str(error)
        self.record('page_error', tag=_error_tag(message),
                    message_hash=hashlib.sha256(message.encode()).hexdigest()[:16])

    def _on_console(self, message):
        if message.type != 'error':
            return
        value = message.text
        self.record('console_error', tag=_error_tag(value),
                    message_hash=hashlib.sha256(value.encode()).hexdigest()[:16])

    def _on_request_failed(self, request):
        endpoint = _chase_endpoint(request.url)
        if not endpoint:
            return
        failure = request.failure or ''
        match = re.search(r'net::ERR_[A-Z_]+', failure)
        self.record('request_failed', **endpoint, method=request.method,
                    network_error=match.group(0) if match else 'other')

    def _on_response(self, response):
        endpoint = _chase_endpoint(response.url)
        if not endpoint or response.request.resource_type not in ('xhr', 'fetch'):
            return
        metadata = dict(endpoint, method=response.request.method,
                        status=response.status)
        self.recent_responses.append(metadata)
        near_passkey = (self.credential_added_at is not None and
                        time.monotonic() - self.credential_added_at <
                        POST_CREDENTIAL_SECONDS)
        if not (response.status >= 400 or endpoint['passkey_path'] or
                near_passkey):
            return
        key = str(response.status)
        self.http_status_counts[key] = self.http_status_counts.get(key, 0) + 1
        self.record('chase_response', **metadata)

    def _on_request_finished(self, request):
        response = request.response()
        if response is None:
            return
        endpoint = _chase_endpoint(response.url)
        if not endpoint or request.resource_type not in ('xhr', 'fetch'):
            return
        near_passkey = (self.credential_added_at is not None and
                        time.monotonic() - self.credential_added_at <
                        POST_CREDENTIAL_SECONDS)
        if not (response.status >= 400 or endpoint['passkey_path'] or
                near_passkey):
            return
        if len(self.pending) >= MAX_PENDING:
            self.dropped += 1
            return
        headers = response.headers
        content_type = _content_type_category(headers.get('content-type', ''))
        try:
            size = int(headers.get('content-length', '-1'))
        except ValueError:
            size = -1
        if size > MAX_RESPONSE_BYTES:
            if response.status >= 400:
                self.record('chase_error_body_skipped', status=response.status,
                            reason='declared_too_large')
            return
        if content_type != 'json' and response.status < 400:
            return
        request_ids = {}
        for header in REQUEST_ID_HEADERS:
            value = headers.get(header, '')
            if re.fullmatch(r'[A-Za-z0-9_-]{8,80}', value):
                request_ids[header] = value
        metadata = dict(endpoint, method=request.method,
                        status=response.status)
        self.pending.append((response, metadata, request_ids, content_type))

    def drain(self):
        """Read only small completed JSON responses, retaining machine codes."""
        while self.pending:
            response, metadata, request_ids, content_type = self.pending.popleft()
            try:
                body = response.body()
                if len(body) > MAX_RESPONSE_BYTES:
                    if metadata['status'] >= 400:
                        self.record('chase_error_body_skipped',
                                    status=metadata['status'],
                                    reason='actual_too_large')
                    continue
            except Exception as error:
                if metadata['status'] >= 400:
                    self.record('chase_error_body_skipped',
                                status=metadata['status'],
                                reason='body_unavailable',
                                exception_type=type(error).__name__)
                continue
            if metadata['status'] >= 400:
                self.record('chase_error_body', status=metadata['status'],
                            content_type=content_type, body_bytes=len(body),
                            body_hash=hashlib.sha256(body).hexdigest()[:16])
            if content_type != 'json':
                continue
            try:
                decoded = json.loads(body)
            except (TypeError, ValueError):
                if metadata['status'] >= 400:
                    self.record('chase_error_body_parse',
                                status=metadata['status'], result='invalid_json')
                continue
            codes = _machine_codes(decoded)
            if metadata['status'] >= 400:
                self.record('chase_error_body_shape', status=metadata['status'],
                            error_fields=_error_field_names(decoded),
                            root_type=type(decoded).__name__)
            for code in codes:
                if code not in self.error_codes and len(self.error_codes) < 6:
                    self.error_codes.append(code)
            if codes or request_ids:
                self.record('chase_response_detail', **metadata,
                            codes=codes, request_ids=request_ids)

    def report(self):
        return {'version': 1, 'kind': 'chase_passkey_enrollment',
                'browser_major': self.browser_major, 'events': self.events,
                'summary': {
                    'credential_added_count': self.credential_added_count,
                    'credential_asserted_count': self.credential_asserted_count,
                    'last_candidate_count': self.last_candidate_count,
                    'http_status_counts': self.http_status_counts,
                    'error_codes': self.error_codes},
                'dropped_events': self.dropped,
                'limits': {'max_events': MAX_EVENTS,
                           'max_response_bytes': MAX_RESPONSE_BYTES}}

    def persist(self, force=False):
        if not force and time.monotonic() - self.last_persist < 10:
            return
        run = db.session.get(ChaseBrowserRuns, self.run_id)
        if run is None:
            return
        run.diagnostics = json.dumps(self.report(), separators=(',', ':'))
        db.session.commit()
        self.last_persist = time.monotonic()
