#!/usr/bin/env python
"""Owner-supervised Schwab structure probing in a dedicated, visible Firefox.

    schwab_browser.py launch            # separate Firefox, private profile
    schwab_browser.py login             # sign in with the login saved in Pacioli (prints status only)
    schwab_browser.py probe [URL]       # sanitized control structure of a Schwab page
    schwab_browser.py click INDEX LABEL # press one probed control (verified by masked label)

The Schwab login comes from Pacioli's encrypted store and is typed only into Schwab's own
sign-in form; it is never printed. Automation
listens on loopback only. Every script returns only vocabulary-masked structure
(see extensions/schwab-local/content.js) or booleans; page text, amounts and
account details never leave the page.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli.functions.firefox_bidi import FirefoxConnection
from scripts.schwab_probe import sanitize, vocabulary

PORT = 9333
ORIGINS = {'https://client.schwab.com', 'https://www.schwab.com'}
PROFILE = ROOT / '.local/schwab-browser/profile'
STATE = ROOT / '.local/schwab-browser/last-url'
START = 'https://client.schwab.com/'
LOGIN_URL = 'https://client.schwab.com/Areas/Access/Login'

ENUMERATE = r'''
const api = globalThis.pacioliSchwab;
const CONTROLS = 'a,button,select,input,textarea,[role="button"],[role="link"],[role="tab"],[role="menuitem"],' +
  '[role="menuitemradio"],[role="option"],[role="combobox"],[role="radio"],[role="checkbox"],[role="listbox"],' +
  '[aria-haspopup]';
const DATA = 'table,tr,[role="grid"],[role="table"],[role="row"],[role="gridcell"],[role="cell"]';
const root = el => el.shadowRoot || el.openOrClosedShadowRoot;
const all = (sel, r = document) => { const f = [...r.querySelectorAll(sel)];
  for (const el of r.querySelectorAll('*')) if (root(el)) f.push(...all(sel, root(el))); return f; };
const visible = el => { const b = el.getBoundingClientRect(), s = getComputedStyle(el);
  return b.width > 2 && b.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; };
const closest = (el, sel) => { for (let n = el; n; n = n.parentElement || n.getRootNode().host) if (n.matches?.(sel)) return n; return null; };
const seen = new Set(), found = [];
for (const el of all(CONTROLS)) {
  if (seen.has(el) || !visible(el)) continue; seen.add(el);
  if (el.type === 'password' || el.type === 'hidden' || closest(el, DATA)) continue;
  if (found.length >= 200) break;
  found.push(el);
}
'''


def endpoint_open():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=.5):
            return True
    except OSError:
        return False


def launch(args):
    if endpoint_open():
        print('Schwab Firefox is already running on the local automation port.')
        return
    os.umask(0o077)
    PROFILE.mkdir(mode=0o700, parents=True, exist_ok=True)
    binary = shutil.which('firefox') or '/snap/bin/firefox'
    # Without a graphical session nobody could see a window: run headless.
    headless = [] if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY') else ['--headless']
    subprocess.Popen([binary, '--new-instance', *headless, '--profile', str(PROFILE),
                      '--remote-debugging-port', str(PORT), START],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not endpoint_open():
        time.sleep(.5)
    print('Schwab Firefox started.' if endpoint_open() else 'Firefox did not open its automation port.')


def connect():
    return FirefoxConnection(PORT, allowed_origins=ORIGINS)


def page_script():
    return (ROOT / 'extensions/schwab-local/content.js').read_text()


def remember(url):
    STATE.write_text(url)


def open_page(connection, url):
    connection.page.goto(url)
    connection.page.bring_to_front()
    time.sleep(4)  # Schwab renders client-side after navigation.


def probe(args):
    url = args.url or (STATE.read_text() if STATE.exists() else START + 'app/accounts/history/#/')
    with connect() as connection:
        open_page(connection, url)
        result = connection.script(connection.tab, page_script() + ';return globalThis.pacioliSchwab.probe();')
        remember(connection.page.url)
    result = sanitize(result, vocabulary())
    output = json.dumps(result, indent=1, sort_keys=True)
    directory = ROOT / '.local/schwab/probes'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / 'probe-{}.json'.format(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))).write_text(output)
    print(output)


def click(args):
    """Press control INDEX of the probe enumeration, only if its masked label matches."""
    url = STATE.read_text() if STATE.exists() else START
    with connect() as connection:
        open_page(connection, url)
        body = page_script() + ENUMERATE + '''
            const el = found[args.index];
            if (!el) return {clicked: false, reason: 'no_control'};
            const label = api._mask(el.getAttribute('aria-label') || el.textContent || '');
            if (label !== args.label) return {clicked: false, reason: 'label_mismatch'};
            el.scrollIntoView({block: 'center'}); el.click();
            return {clicked: true};'''
        result = connection.script(connection.tab, body, {'index': args.index, 'label': args.label})
        time.sleep(4)
        after = connection.script(connection.tab, page_script() + ';return globalThis.pacioliSchwab.probe();')
        remember(connection.page.url)
    print(json.dumps({'click': result}, sort_keys=True))
    print(json.dumps(sanitize(after, vocabulary()), indent=1, sort_keys=True))


LOGIN_ORIGINS = ORIGINS | {'https://sws-gateway-nr.schwab.com', 'https://sws-gateway.schwab.com',
                           'https://lms.schwab.com'}

STATUS = r'''
const shown = el => { const b = el.getBoundingClientRect(); return b.width > 2 && b.height > 2; };
const code = [...document.querySelectorAll('input')].some(el => shown(el) &&
  (el.autocomplete === 'one-time-code' || /code|otp|pin/i.test(el.id + ' ' + el.name)));
const password = [...document.querySelectorAll('input[type="password"]')].some(shown);
return {code, password, app: location.origin === 'https://client.schwab.com' && /\/app\//i.test(location.pathname)};'''


def saved_login():
    from pacioli import create_app
    from pacioli.extensions import db
    from pacioli.models import BrokerageCredentials
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        row = db.session.get(BrokerageCredentials, 'schwab')
        login = (row.username, row.password) if row else None
        db.session.remove()
    return login


FOCUS = r'''
const shown = el => { const b = el.getBoundingClientRect(), s = getComputedStyle(el);
  return b.width > 2 && b.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; };
const inputs = [...document.querySelectorAll('input')].filter(shown);
const password = inputs.find(el => el.type === 'password');
const user = inputs.find(el => el !== password && ['text', 'email'].includes(el.type));
const target = args.field === 'password' ? password : user;
if (!password || !user || !target) return {found: false};
target.focus(); target.select?.();
return {found: true, focused: document.activeElement === target};'''

LENGTHS = r'''
const shown = el => { const b = el.getBoundingClientRect(); return b.width > 2 && b.height > 2; };
const inputs = [...document.querySelectorAll('input')].filter(shown);
const password = inputs.find(el => el.type === 'password');
const user = inputs.find(el => el !== password && ['text', 'email'].includes(el.type));
return {user: user ? user.value.length === args.user : false, password: password ? password.value.length === args.password : false};'''

SUBMIT = r'''
const shown = el => { const b = el.getBoundingClientRect(); return b.width > 2 && b.height > 2; };
const submit = [...document.querySelectorAll('button,input[type="submit"]')].filter(shown)
  .find(el => /^\s*log ?in\s*$/i.test(el.textContent || el.value || ''));
if (!submit) return {submitted: false};
submit.click();
return {submitted: true};'''


def type_text(connection, value):
    """Trusted key events into the focused field, as a person typing."""
    actions = []
    for char in value:
        actions += [{'type': 'keyDown', 'value': char}, {'type': 'keyUp', 'value': char}]
    connection.command('input.performActions', {'context': connection.tab, 'actions': [
        {'type': 'key', 'id': 'keyboard', 'actions': actions}]})
    connection.command('input.releaseActions', {'context': connection.tab})


def login_frame(connection):
    for frame in connection.page.frames:
        try:
            if script_in(connection, frame.context, FOCUS, {'field': 'user'}).get('found'):
                return frame.context
        except Exception:
            continue
    return None


def login(args):
    """Sign in with the login saved in Pacioli. Prints a status word only."""
    saved = saved_login()
    if saved is None:
        raise SystemExit('No Schwab login is saved. Use https://pacioli.lan/connections/schwab/ first.')
    with FirefoxConnection(PORT, allowed_origins=LOGIN_ORIGINS) as connection:
        connection.page.goto(LOGIN_URL)
        connection.page.bring_to_front()
        time.sleep(6)
        frame = login_frame(connection)
        if frame is None:
            status = 'login_form_not_found'
        else:
            for field, value in (('user', saved[0]), ('password', saved[1])):
                script_in(connection, frame, FOCUS, {'field': field})
                time.sleep(.3)
                type_text(connection, value)
            lengths = script_in(connection, frame, LENGTHS, {'user': len(saved[0]), 'password': len(saved[1])})
            saved = None
            if not (lengths.get('user') and lengths.get('password')):
                status = 'typing_not_accepted'  # Nothing is submitted.
            elif not script_in(connection, frame, SUBMIT, {}).get('submitted'):
                status = 'not_submitted'
            else:
                time.sleep(12)
                states = []
                for node in connection.page.frames:
                    try:
                        states.append(script_in(connection, node.context, STATUS, {}))
                    except Exception:
                        continue
                status = ('signed_in' if any(s.get('app') for s in states) else
                          'verification_required' if any(s.get('code') for s in states) else
                          'still_on_login' if any(s.get('password') for s in states) else 'unknown')
        remember(START + 'app/accounts/history/#/')
        if status not in ('signed_in', 'login_form_not_found', 'typing_not_accepted'):
            connection.close(close_tab=False)  # keep the page for diagnose
    print(status)


AUTH_WORDS = sorted(set(vocabulary()) | set("""
    again app approve authenticate call cannot code continue could couldn't device did email enter error
    forgot help id incorrect information invalid is it locked log login match mobile not password phone please
    recognize remember security send sent sign something submit text this try trust unable verify verification
    we went were wrong you your""".split()))

DIAGNOSE = r'''
const words = new Set(args.words);
const mask = value => String(value || '').trim().split(/\s+/).filter(Boolean).map(w => {
  if (/\d/.test(w)) return '#';
  const bare = w.toLowerCase().replace(/^[^a-z']+|[^a-z']+$/g, '');
  return bare && words.has(bare) ? bare : '*';
}).join(' ').replace(/(?:\* ?){2,}/g, '* ').trim().slice(0, 120);
const shown = el => { const b = el.getBoundingClientRect(), s = getComputedStyle(el);
  return b.width > 2 && b.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; };
return {
  inputs: [...document.querySelectorAll('input')].filter(shown).map(el => ({type: el.type,
    filled: !!el.value, autocomplete: mask(el.autocomplete), id: mask((el.id + ' ' + el.name).replace(/[-_.]/g, ' '))})),
  buttons: [...document.querySelectorAll('button,[role="button"],input[type="submit"],a')].filter(shown).slice(0, 40)
    .map(el => ({tag: el.tagName.toLowerCase(), label: mask(el.getAttribute('aria-label') || el.textContent || el.value),
                 enabled: !el.disabled})),
  alerts: [...document.querySelectorAll('[role="alert"],[aria-live],.error,.alert,[class*="error"]')].filter(shown)
    .slice(0, 10).map(el => mask(el.textContent)).filter(Boolean),
  headings: [...document.querySelectorAll('h1,h2,h3,[role="heading"],legend')].filter(shown).slice(0, 10)
    .map(el => mask(el.textContent)),
  frames: document.querySelectorAll('iframe').length};'''


def script_in(connection, context, body, args):
    """Run in any open tab's frame; the script itself refuses unless on an allowed origin."""
    declaration = '''function(serialized) {
        const args = JSON.parse(serialized);
        if (!args.origins.includes(location.origin)) throw new Error('wrong origin');
        return JSON.stringify((function() { BODY }).call(null));
    }'''.replace('BODY', body)
    values = dict(args, origins=sorted(LOGIN_ORIGINS))
    result = connection.command('script.callFunction', {
        'functionDeclaration': declaration, 'target': {'context': context},
        'arguments': [{'type': 'string', 'value': json.dumps(values)}],
        'awaitPromise': False, 'resultOwnership': 'none'})
    if result.get('type') != 'success':
        raise RuntimeError('script_failed')
    return json.loads(result['result'].get('value', 'null'))


def diagnose(args):
    """Sanitized state of open Schwab sign-in tabs. Reads no values and navigates nowhere."""
    from urllib.parse import urlsplit
    words = set(AUTH_WORDS)

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str) and not all(w in words or w in ('*', '#') for w in value.split()):
            return '?'
        return value
    with FirefoxConnection(PORT, allowed_origins=LOGIN_ORIGINS) as connection:
        report = []
        for top in connection.command('browsingContext.getTree', {})['contexts']:
            if top['context'] == connection.tab:
                continue
            def visit(node, depth=0):
                parts = urlsplit(node['url'])
                origin = '{}://{}'.format(parts.scheme, parts.netloc)
                entry = {'depth': depth, 'origin': origin if origin.endswith('schwab.com') else 'other'}
                if origin in LOGIN_ORIGINS:
                    try:
                        entry.update(clean(script_in(connection, node['context'], DIAGNOSE, {'words': AUTH_WORDS})))
                    except Exception:
                        entry['readable'] = False
                report.append(entry)
                for child in node.get('children') or []:
                    visit(child, depth + 1)
            visit(top)
    print(json.dumps(report, indent=1))


def where(args):
    """Scheme, host and path of each open top-level tab; no query strings or fragments."""
    from urllib.parse import urlsplit
    with FirefoxConnection(PORT, allowed_origins=LOGIN_ORIGINS) as connection:
        for top in connection.command('browsingContext.getTree', {'maxDepth': 0})['contexts']:
            if top['context'] != connection.tab:
                parts = urlsplit(top['url'])
                print('{}://{}{}'.format(parts.scheme, parts.netloc, parts.path))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('launch').set_defaults(handler=launch)
    commands.add_parser('login').set_defaults(handler=login)
    commands.add_parser('diagnose').set_defaults(handler=diagnose)
    commands.add_parser('where').set_defaults(handler=where)
    command = commands.add_parser('probe')
    command.add_argument('url', nargs='?')
    command.set_defaults(handler=probe)
    command = commands.add_parser('click')
    command.add_argument('index', type=int)
    command.add_argument('label')
    command.set_defaults(handler=click)
    args = parser.parse_args()
    if getattr(args, 'url', None) and not any(args.url.startswith(o + '/') for o in ORIGINS):
        parser.error('Only Schwab pages can be probed')
    args.handler(args)


if __name__ == '__main__':
    main()
