"""Attach to ordinary Firefox through its loopback WebDriver BiDi endpoint.

No profile copying, extension inspection, browser-wide download overrides, or
browser-close command. Each connection owns exactly one new tab. Scripts and
input are limited to allowlisted origins within that tab.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlsplit

from websockets.sync.client import connect


class FirefoxError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _origin(url):
    parsed = urlsplit(url.removeprefix('blob:'))
    return '{}://{}'.format(parsed.scheme, parsed.netloc)


ERROR_PHRASES = (
    'something went wrong', 'try adding a passkey again',
    "we can't sign you in", "we couldn't sign you in", 'try again later',
    'access denied', 'temporarily unavailable', 'browser is not supported',
    "we're having trouble", "this part of our site isn't working right now",
)


def navigation_summary(url, allowed_origins):
    """Classify an address without retaining paths, queries or unknown hosts."""
    try:
        parsed = urlsplit(url)
        origin = _origin(url)
        if origin in allowed_origins:
            return {'kind': 'allowed', 'origin': origin}
        if parsed.scheme == 'about':
            page = parsed.path if parsed.path in ('blank', 'neterror', 'certerror') else 'other'
            return {'kind': 'browser_page', 'page': page}
        if parsed.scheme == 'https' and re.fullmatch(
                r'(?:[a-z0-9-]+\.)*chase\.com', parsed.hostname or ''):
            return {'kind': 'unapproved_chase_host', 'host': parsed.hostname}
        return {'kind': 'outside_allowlist', 'scheme': parsed.scheme
                if parsed.scheme in ('http', 'https', 'moz-extension', 'file', 'data', 'blob')
                else 'other'}
    except ValueError:
        return {'kind': 'invalid_address'}


class FirefoxConnection:
    def __init__(self, port=9222, *, allowed_origins=None, heartbeat=None,
                 download_started=None, diagnostic=None):
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise FirefoxError('firefox_port_invalid')
        self.endpoint = 'ws://127.0.0.1:{}/session'.format(port)
        self.allowed_origins = frozenset(allowed_origins or (
            'https://secure.chase.com', 'https://chase.com', 'https://www.chase.com'))
        self.heartbeat = heartbeat or (lambda: None)
        self.download_started = download_started or (lambda: None)
        self.diagnostic = diagnostic or (lambda event, **details: None)
        self.socket = None
        self.tab = None
        self.last_navigation = None
        self.sequence = 0
        self.events = []
        self.session_started = False

    def __enter__(self):
        try:
            self._socket_manager = connect(self.endpoint, open_timeout=3,
                                           max_size=2 * 1024 * 1024, proxy=None)
            self.socket = self._socket_manager.__enter__()
        except Exception:
            raise FirefoxError('firefox_not_connected') from None
        try:
            self.command('session.new', {'capabilities': {'alwaysMatch': {
                'browserName': 'firefox', 'acceptInsecureCerts': False,
                'unhandledPromptBehavior': 'ignore'}}})
            self.session_started = True
            self.diagnostic('connection', attached=True)
            self.tab = self.command('browsingContext.create', {
                'type': 'tab', 'background': True})['context']
            self.command('session.subscribe', {
                'events': ['browsingContext.downloadWillBegin', 'browsingContext.downloadEnd'],
                'contexts': [self.tab]})
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, exc_type, *exc):
        # Keep a failed Chase page visible for the user and error inspection.
        self.close(close_tab=exc_type is None)

    def close(self, close_tab=True):
        if self.socket is None:
            return
        try:
            if self.tab and close_tab:
                try:
                    self.command('browsingContext.close', {'context': self.tab}, timeout=3)
                except FirefoxError:
                    pass
            if self.session_started:
                try:
                    # session.end detaches; browser.close would close personal tabs.
                    self.command('session.end', {}, timeout=3)
                    try:
                        self.diagnostic('connection', attached=False)
                    except Exception:
                        pass  # Cleanup must not mask the original failure.
                except FirefoxError:
                    pass
        finally:
            try:
                self._socket_manager.__exit__(None, None, None)
            except Exception:
                pass  # Do not mask cancellation when the connection is lost.
            finally:
                self.socket = None

    def command(self, method, params, timeout=20):
        self.sequence += 1
        sequence = self.sequence
        try:
            self.socket.send(json.dumps({'id': sequence, 'method': method, 'params': params}))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FirefoxError('firefox_command_timeout')
                response = json.loads(self.socket.recv(timeout=remaining))
                if response.get('type') == 'event':
                    # Subscriptions are scoped to our tab, and only download
                    # metadata is retained. Never subscribe to all network data.
                    if response.get('params', {}).get('context') == self.tab:
                        self.events.append(response)
                        self.events = self.events[-100:]
                    continue
                if response.get('id') != sequence:
                    continue
                if response.get('type') == 'error':
                    code = response.get('error')
                    if method == 'session.new' and code == 'session not created':
                        raise FirefoxError('firefox_session_busy')
                    raise FirefoxError('browser_window_closed' if code == 'no such frame'
                                       else 'firefox_command_failed')
                return response['result']
        except FirefoxError:
            raise
        except Exception:
            # Protocol errors can contain page text or URLs. Never persist them.
            raise FirefoxError('firefox_disconnected') from None

    def tree(self):
        return self.command('browsingContext.getTree', {'root': self.tab})['contexts'][0]

    def context_info(self, context):
        def find(node):
            if node['context'] == context:
                return node
            for child in node.get('children') or []:
                found = find(child)
                if found:
                    return found
        # Firefox temporarily reports about:blank while replacing a document
        # after a trusted click. Wait without reading or acting on that page.
        deadline = time.monotonic() + 3
        while True:
            found = find(self.tree())
            if found is None:
                raise FirefoxError('browser_window_closed')
            if context == self.tab:
                navigation = navigation_summary(found['url'], self.allowed_origins)
                if navigation != self.last_navigation:
                    self.last_navigation = navigation
                    self.diagnostic('navigation', **navigation)
            if found['url'] != 'about:blank' or time.monotonic() >= deadline:
                return found
            self.heartbeat()
            time.sleep(.05)

    def check(self, context):
        if _origin(self.context_info(context)['url']) not in self.allowed_origins:
            raise FirefoxError('unexpected_browser_host')

    def script(self, context, body, args=None):
        self.check(context)
        # Check the origin again inside the same script, preventing navigation
        # between the protocol check and reading or editing a document.
        declaration = '''function(serialized) {
            const args = JSON.parse(serialized);
            if (!args.origins.includes(location.origin)) throw new Error('wrong origin');
            return JSON.stringify((function() { BODY }).call(null));
        }'''.replace('BODY', body)
        values = dict(args or {}, origins=sorted(self.allowed_origins))
        result = self.command('script.callFunction', {
            'functionDeclaration': declaration, 'target': {'context': context},
            'arguments': [{'type': 'string', 'value': json.dumps(values)}],
            'awaitPromise': False, 'resultOwnership': 'none'})
        if result.get('type') != 'success':
            raise FirefoxError('firefox_page_changed')
        return json.loads(result['result'].get('value', 'null'))

    @property
    def page(self):
        return FirefoxPage(self, self.tab)


class FirefoxPage:
    def __init__(self, connection, context):
        self.connection = connection
        self.context = context

    @property
    def url(self):
        return self.connection.context_info(self.context)['url']

    @property
    def frames(self):
        found = []
        def visit(node):
            if _origin(node['url']) in self.connection.allowed_origins:
                found.append(FirefoxPage(self.connection, node['context']))
                for child in node.get('children') or []:
                    visit(child)
        visit(self.connection.context_info(self.context))
        return found

    def goto(self, url, **kwargs):
        if _origin(url) not in self.connection.allowed_origins:
            raise FirefoxError('unexpected_browser_host')
        self.connection.command('browsingContext.navigate', {
            'context': self.context, 'url': url, 'wait': 'interactive'}, timeout=30)

    def bring_to_front(self):
        self.connection.check(self.context)
        self.connection.command('browsingContext.activate', {'context': self.context})

    def capture_error_signals(self):
        # Chase renders sign-in and its errors inside a bank-owned iframe.
        # frames remains scoped to this tab and filters foreign origins.
        found = {phrase for frame in self.frames for phrase in frame._error_signals_here()}
        phrases = [phrase for phrase in ERROR_PHRASES if phrase in found]
        if phrases:
            self.connection.diagnostic('visible_error', phrases=phrases)
        return phrases

    def _error_signals_here(self):
        # Return fixed phrases, not arbitrary page text, names, values or URLs.
        return self.connection.script(self.context, '''
            const found = new Set();
            let visited = 0;
            function visit(root) {
                for (const el of root.querySelectorAll('*')) {
                    if (++visited > 5000) return;
                    if (el.shadowRoot) visit(el.shadowRoot);
                    if (el.matches('input,textarea,script,style') || !el.getClientRects().length) continue;
                    if (getComputedStyle(el).visibility !== 'visible') continue;
                    const text = (el.innerText || '').trim().toLowerCase();
                    if (text.length > 600) continue;
                    for (const phrase of args.phrases) if (text.includes(phrase)) found.add(phrase);
                }
            }
            visit(document);
            return [...found];
        ''', {'phrases': ERROR_PHRASES})

    def wait_for_timeout(self, milliseconds):
        time.sleep(milliseconds / 1000)

    def locator(self, selector):
        return FirefoxLocator(self, [{'selector': selector}])

    def get_by_role(self, role, name):
        selectors = {'button': 'button, [role="button"], input[type="submit"]',
                     'link': 'a[href], [role="link"]'}
        locator = self.locator(selectors[role])
        locator.steps[-1]['name_pattern'] = name.pattern
        locator.steps[-1]['ignore_case'] = bool(name.flags & re.I)
        return locator

    @property
    def keyboard(self):
        return self

    def press(self, key):
        self.connection.check(self.context)
        value = {'Escape': '\ue00c', 'Enter': '\ue007'}[key]
        self.connection.command('input.performActions', {'context': self.context,
            'actions': [{'type': 'key', 'id': 'pacioli-keyboard', 'actions': [
                {'type': 'keyDown', 'value': value}, {'type': 'keyUp', 'value': value}]}]})

    @contextmanager
    def expect_download(self, timeout):
        self.connection.events.clear()
        event = FirefoxDownloadEvent(self)
        self.connection.download_started()
        yield event
        event.wait(max(timeout / 1000, 120))


_SELECT = '''
function deep(root, selector) {
    const matches = [...root.querySelectorAll(selector)];
    for (const el of root.querySelectorAll('*')) {
        if (el.shadowRoot) matches.push(...deep(el.shadowRoot, selector));
    }
    return matches;
}
let elements = [document];
for (const step of args.steps) {
    if (step.selector) elements = [...new Set(elements.flatMap(el => deep(el, step.selector)))];
    if (step.text !== undefined) elements = elements.filter(el => (el.textContent || '').includes(step.text));
    if (step.name_pattern) {
        const pattern = new RegExp(step.name_pattern, step.ignore_case ? 'i' : '');
        elements = elements.filter(el => pattern.test(el.getAttribute('aria-label') || el.innerText || el.value || ''));
    }
    if (step.index !== undefined) elements = elements.slice(step.index, step.index + 1);
}
const el = elements[0];
function visible(el) {
    if (!el) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && el.getClientRects().length > 0;
}
'''


class FirefoxLocator:
    def __init__(self, page, steps):
        self.page, self.steps = page, steps

    def _eval(self, body, **args):
        return self.page.connection.script(self.page.context, _SELECT + body,
                                           dict(args, steps=self.steps))

    def locator(self, selector):
        return FirefoxLocator(self.page, self.steps + [{'selector': selector}])

    def filter(self, has_text):
        return FirefoxLocator(self.page, self.steps + [{'text': has_text}])

    def nth(self, index):
        return FirefoxLocator(self.page, self.steps + [{'index': index}])

    @property
    def first(self):
        return self.nth(0)

    def count(self):
        return self._eval('return elements.length;')

    def is_visible(self):
        return self._eval('return visible(el);')

    def get_attribute(self, name):
        return self._eval('return el ? el.getAttribute(args.name) : null;', name=name)

    def inner_text(self, **kwargs):
        return self._eval('return el ? el.innerText : "";')

    def wait_for(self, state='visible', timeout=10000):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            self.page.connection.heartbeat()
            if self.is_visible():
                return
            time.sleep(.2)
        raise FirefoxError('firefox_control_unavailable')

    def click(self, timeout=10000):
        self.wait_for(timeout=timeout)
        point = self._eval('''
            if (elements.length !== 1 || el.disabled) return null;
            el.scrollIntoView({block: 'center', inline: 'center'});
            const rect = el.getBoundingClientRect();
            return {x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2)};
        ''')
        if point is None:
            raise FirefoxError('firefox_control_unavailable')
        self.page.connection.check(self.page.context)
        self.page.connection.command('input.performActions', {'context': self.page.context,
            'actions': [{'type': 'pointer', 'id': 'pacioli-pointer',
                'parameters': {'pointerType': 'mouse'}, 'actions': [
                    dict(type='pointerMove', origin='viewport', duration=0, **point),
                    {'type': 'pointerDown', 'button': 0}, {'type': 'pointerUp', 'button': 0}]}]})

    def fill(self, value):
        self._eval('''
            if (elements.length !== 1 || !visible(el)) throw new Error('missing input');
            el.focus();
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(el, args.value);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return null;
        ''', value=value)

    def press(self, key):
        self._eval('el.focus(); return null;')
        self.page.press(key)


class FirefoxDownloadEvent:
    def __init__(self, page):
        self.page = page
        self.value = None

    def wait(self, timeout):
        connection = self.page.connection
        deadline = time.monotonic() + timeout
        started = None
        while time.monotonic() < deadline:
            connection.heartbeat()
            connection.check(self.page.context)
            connection.command('session.status', {})  # pump scoped events
            events, connection.events = connection.events, []
            for event in events:
                params = event['params']
                if params.get('context') != self.page.context:
                    continue
                if _origin(params.get('url', '')) not in connection.allowed_origins:
                    raise FirefoxError('unexpected_download_host')
                if event['method'] == 'browsingContext.downloadWillBegin':
                    if started is not None:
                        raise FirefoxError('ambiguous_download')
                    started = params
                elif (started and event['method'] == 'browsingContext.downloadEnd'
                      and params.get('download') == started.get('download')):
                    if params.get('status') != 'complete' or not params.get('filepath'):
                        raise FirefoxError('firefox_download_incomplete')
                    self.value = FirefoxDownload(started['suggestedFilename'], params['filepath'])
                    return
            time.sleep(.2)
        raise FirefoxError('firefox_download_timeout')


class FirefoxDownload:
    def __init__(self, name, path):
        self.suggested_filename = name
        self._path = path

    def read_content(self, limit):
        # Read only the exact file Firefox reported for our tab/download ID.
        # Never scan Downloads or remove a file from the user's browser profile.
        descriptor = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise FirefoxError('firefox_download_incomplete')
            return source.read(limit)
