"""Schwab connector: structure probe privacy, host adapter, background guards."""
import json
from pathlib import Path
import subprocess

import pytest

from scripts.install_schwab_extension import host_source
from scripts.schwab_probe import sanitize, vocabulary
from tests.test_chase_firefox import attach, firefox


ROOT = Path(__file__).resolve().parents[1]
ORIGINS = "['https://client.schwab.com', 'https://www.schwab.com']"
PRIVATE = ('Pierre', 'Rochard', 'AAPL', 'VTSAX', '1,234.56', '98765', '321', 'Qualified Dividend', 'Grandma')

PAGE = '''<style>button,a,select,input{display:block;margin:10px;height:30px;width:300px}</style>
<header><a href="/app/accounts/summary">Summary</a><button aria-haspopup="menu">Pierre Rochard</button></header>
<h1>History</h1><h2>Brokerage ...321 Grandma Fund</h2>
<label for="acct">Account</label><select id="acct"><option>Brokerage ...321</option><option>Roth IRA ...98765</option></select>
<button id="exportBtn" class="sdps-button primary">Export</button>
<div role="dialog" aria-modal="true"><input type="radio" name="format" value="json" id="fmt-json" checked>
  <label for="fmt-json">JSON</label><input type="text" id="from-date" placeholder="From" value="01/02/2026">
  <button>Export</button></div>
<a href="/quote/AAPL">AAPL</a><a href="#">ALL</a>
<table><tr><td>09/01/2026</td><td>Qualified Dividend</td><td><a href="/quote/VTSAX">VTSAX</a></td>
<td><button>$1,234.56</button></td></tr></table>'''


def page_source(firefox):
    return (ROOT / 'extensions/schwab-local/content.js').read_text().replace(ORIGINS, json.dumps([firefox.origin]))


def test_probe_reports_structure_without_private_text(firefox):
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin + '/approved')

        def js(body, args=None):
            return connection.script(connection.tab, body, args)
        js('document.title="Schwab History Grandma";document.body.innerHTML=args.html;return true;', {'html': PAGE})
        js(page_source(firefox) + ';return true;')
        probe = js('return globalThis.pacioliSchwab.probe();')
        dump = json.dumps(probe)
        for secret in PRIVATE:
            assert secret not in dump and secret.lower() not in dump.lower()
        labels = [c['label'] for c in probe['controls']]
        assert 'export' in labels and 'json' in [c['labelled'] for c in probe['controls']]
        select = next(c for c in probe['controls'] if c['tag'] == 'select')
        assert select['labelled'] == 'account' and select['options'] == ['brokerage #', 'roth ira #']
        assert probe['skipped_data_controls'] >= 2 and probe['dialogs'] == 1
        assert '*' in labels  # the AAPL / ALL / name links are masked, not dropped
        assert sanitize(probe, vocabulary()) == probe

        js('document.body.innerHTML=\'<input type="password" style="width:200px;height:30px">Log in\';return true;')
        auth = js('return globalThis.pacioliSchwab.probe();')
        assert auth['kind'] == 'authentication' and auth['controls'] == []


def test_python_sanitizer_replaces_unexpected_words():
    words = vocabulary()
    dirty = {'kind': 'structure', 'title': 'history Pierre', 'controls': [
        {'tag': 'button', 'label': 'export', 'options': ['brokerage #', 'AAPL'], 'shadow': ['sdps-button', 'Bad Tag'],
         'box': [10, 20, 30, 40], 'enabled': True}]}
    clean = sanitize(dirty, words)
    assert clean['title'] == '?'
    assert clean['controls'][0]['options'] == ['brokerage #', '?']
    assert clean['controls'][0]['shadow'] == ['sdps-button', '?']
    assert clean['controls'][0]['label'] == 'export' and clean['controls'][0]['box'] == [10, 20, 30, 40]


def test_native_host_adapter_is_schwab_only():
    source = host_source(ROOT)
    assert 'pacioli-schwab-extension.sock' in source and 'pacioli-chase-extension.sock' not in source
    assert "'probe'" in source


def test_extension_background_guards():
    subprocess.run(['node', '--test', '--test-concurrency=1', str(ROOT / 'tests/schwab_local_background.test.cjs')],
                   check=True, capture_output=True, text=True)


def test_schwab_login_is_encrypted_and_never_shown(client, admin_user, app_context):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from pacioli.extensions import db
    assert client.get('/connections/schwab/').status_code == 302  # login required
    client.post('/login/', data=admin_user)
    response = client.post('/connections/schwab/', data={'action': 'save', 'username': 'fixture-user-77',
                                                         'password': 'fixture-pass-99'}, follow_redirects=True)
    assert b'saved encrypted' in response.data
    page = client.get('/connections/schwab/')
    assert page.headers['Cache-Control'] == 'private, no-store'
    assert b'fixture-user-77' not in page.data and b'fixture-pass-99' not in page.data
    raw = db.session.execute(text("SELECT username, password FROM admin.brokerage_credentials")).one()
    assert raw.username.startswith('fernet:') and raw.password.startswith('fernet:')
    assert 'fixture' not in raw.username + raw.password
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE admin.brokerage_credentials SET password='plaintext'"))
    db.session.rollback()
    client.post('/connections/schwab/', data={'action': 'forget'})
    assert db.session.execute(text("SELECT count(*) FROM admin.brokerage_credentials")).scalar() == 0


NAV = """<responsive-meganav></responsive-meganav>
<script>
const host = document.querySelector('responsive-meganav'), root = host.attachShadow({mode: 'open'});
root.innerHTML = '<style>a{display:block;height:30px;width:200px}</style>' +
  '<a href="#h" onclick="window.nav=\\'history\\'">Transaction History</a>' +
  '<a href="#r" onclick="window.nav=\\'rgl\\'">Realized Gain / Loss</a>' +
  '<a href="#p" onclick="window.nav=\\'positions\\'">Positions</a><a href="#t">Trade</a>';
</script>"""

HISTORY = """<style>button,select{display:block;margin:10px;height:30px;width:200px}
.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}</style>
<app-transaction-history>
<button id="accountSelectorButton" aria-haspopup="true">Brokerage ...321 Individual</button>
<button id="historyHeaderUtilityExportButton">Export</button>
<button id="historySearchButton" onclick="window.searched=1">Search</button>
<select id="dateRangeSelectHistory"><option value="1">Last 30 days</option><option value="9">All</option>
  <option value="10">Custom date range</option></select>
</app-transaction-history>
<app-export-modal><div role="dialog" aria-modal="true" id="dialog">
  <input type="radio" name="fmt" id="f-csv" value="CSV" class="visually-hidden" checked><label for="f-csv">CSV</label>
  <input type="radio" name="fmt" id="f-json" value="JSON" class="visually-hidden"><label for="f-json">JSON</label>
  <input type="radio" name="fmt" id="f-pdf" value="PDF" class="visually-hidden"><label for="f-pdf">PDF</label>
  <button onclick="window.cancelled=1">Cancel</button><button onclick="window.exported=1">Export</button>
</div></app-export-modal>"""

RGL = """<style>button,select{display:block;margin:10px;height:30px;width:200px}
.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}</style>
<app-rgl><button id="rglUtilityExportButton">Export</button>
<select id="dateRangeSelectRgl"><option>Current Year</option><option>Previous Calendar Year</option></select></app-rgl>
<app-export-overlay><div role="dialog">
  <input type="radio" id="summaryExportRadio" class="visually-hidden"><label>Export Summary</label>
  <input type="radio" id="detailsExportRadio" class="visually-hidden"><label>Export Details</label>
  <button>Cancel</button><button onclick="window.exported=1">Export</button></div></app-export-overlay>"""

POSITIONS = """<style>button{display:block;margin:10px;height:30px;width:200px}</style>
<app-holdings><button id="positionsUtilityExportButton">Export</button></app-holdings>
<app-export-confirmation><div role="dialog"><button>Cancel</button>
  <button onclick="window.exported=1">OK</button></div></app-export-confirmation>"""


def test_export_flow_recognizers(firefox):
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin + '/approved')

        def js(body, args=None):
            return connection.script(connection.tab, body, args)

        def load(html):
            js('document.body.innerHTML=args.html;for (const s of document.body.querySelectorAll("script")) '
               '{const n=document.createElement("script");n.textContent=s.textContent;s.replaceWith(n);}'
               'window.nav=undefined;window.exported=undefined;window.cancelled=undefined;return true;',
               {'html': NAV + html})
            js(page_source(firefox) + ';return true;')

        def observe(task=None):
            return js('return globalThis.pacioliSchwab.observe(args.task);', {'task': task})

        def act(observed, kind, action, value=None, pick=None):
            target = [c for c in observed['controls'] if c['kind'] == kind and (pick is None or pick(c))]
            assert len(target) == 1, (kind, observed['controls'])
            return js('try { return globalThis.pacioliSchwab.act(args); } catch (e) { return {error: e.message}; }',
                      {'action': action, 'token': observed['token'], 'target_id': target[0]['id'], 'value': value})
        load(HISTORY)
        seen = observe({'account_digits': '321'})
        assert seen['kind'] == 'history_export' and seen['capture'] is True and seen['account_match'] is True
        assert 'account_digits' not in seen
        assert observe({'account_digits': '999'})['account_match'] is False
        assert observe({'register_account': True})['account_digits'] == '321'
        seen = observe()
        assert sorted(c['kind'] for c in seen['controls']) == [
            'account', 'apply_search', 'date_range', 'export_cancel', 'export_submit', 'nav', 'nav', 'nav',
            'open_export', 'tx_format', 'tx_format']
        assert act(seen, 'apply_search', 'apply_search') == {'acted': True} and js('return window.searched;') == 1
        seen = observe()
        assert act(seen, 'tx_format', 'choose_format', 'json', lambda c: c['value'] == 'json') == {'acted': True}
        assert js('return document.getElementById("f-json").checked;')
        seen = observe()
        assert act(seen, 'tx_format', 'choose_format', 'pdf', lambda c: c['value'] == 'csv')['error'] == 'invalid_target'
        seen = observe()
        assert act(seen, 'date_range', 'choose_date_range', 'all') == {'acted': True}
        assert js('return document.getElementById("dateRangeSelectHistory").value;') == '9'
        seen = observe()
        assert act(seen, 'date_range', 'choose_date_range', 'next year')['error'] == 'invalid_target'
        seen = observe()
        assert act(seen, 'open_export', 'download_export')['error'] == 'invalid_target'
        seen = observe()
        assert act(seen, 'nav', 'navigate', 'rgl', lambda c: c['value'] == 'rgl') == {'acted': True}
        assert js('return window.nav;') == 'rgl'
        seen = observe()
        assert act(seen, 'nav', 'navigate', 'rgl', lambda c: c['value'] == 'history')['error'] == 'invalid_target'
        # Closing the dialog hides its radios from the recognizer view.
        js('document.getElementById("dialog").remove();return true;')
        assert observe()['kind'] == 'history' and not [c for c in observe()['controls'] if c['kind'] == 'tx_format']

        load(RGL)
        seen = observe()
        assert seen['kind'] == 'rgl_export'
        assert act(seen, 'rgl_details', 'choose_details') == {'acted': True}
        assert js('return document.getElementById("detailsExportRadio").checked;')
        seen = observe()
        dates = next(c for c in seen['controls'] if c['kind'] == 'date_range')
        assert dates['options'] == ['current year', 'previous calendar year']
        assert act(seen, 'export_submit', 'download_export') == {'acted': True} and js('return window.exported;') == 1

        load(POSITIONS)
        seen = observe()
        assert seen['kind'] == 'positions_export'
        assert act(seen, 'export_submit', 'download_export') == {'acted': True} and js('return window.exported;') == 1


class FakeBridge:
    """Scripted page states; records every command the collector sends."""

    def __init__(self, states, downloads):
        self.states, self.downloads, self.sent = list(states), list(downloads), []

    def command(self, op, **kwargs):
        self.sent.append((op, kwargs.get('action'), kwargs.get('value')))
        if op == 'observe':
            return dict(self.states.pop(0) if len(self.states) > 1 else self.states[0], token='t')
        if op == 'download':
            return self.downloads.pop(0)
        return {'acted': True}


class FakeModel:
    def __init__(self, action='download_export'):
        self.action, self.asked = action, 0

    def confirm(self, observation, job, target_id):
        self.asked += 1
        return {'evidence': 'fixture', 'action': self.action, 'target_id': target_id if self.action == 'download_export' else None}


def page(kind, *controls, match=True):
    return {'kind': kind, 'account_match': match, 'controls': [dict(id=i, enabled=True, **c) for i, c in enumerate(controls)]}


def test_collector_runs_transactions_export(tmp_path):
    from pacioli.functions.schwab_local import JOBS, SchwabCollector
    download = tmp_path / 'History.json'
    download.write_bytes(b'{"BrokerageTransactions": []}')
    nav = [dict(kind='nav', value=v) for v in ('history', 'rgl', 'positions')]
    last_months = dict(kind='date_range', value='last # months', options=['last # months', 'all'])
    states = [page('positions', *nav, dict(kind='open_export')),
              page('history', *nav, dict(kind='open_export'), last_months),
              page('history', *nav, dict(kind='open_export'), last_months),
              page('history', *nav, dict(kind='open_export'), dict(kind='apply_search'),
                   dict(kind='date_range', value='all', options=['all'])),
              page('history', *nav, dict(kind='open_export'), dict(kind='date_range', value='all', options=['all'])),
              page('history_export', dict(kind='tx_format', value='csv', checked=True),
                   dict(kind='tx_format', value='json', checked=False), dict(kind='export_submit')),
              page('history_export', dict(kind='tx_format', value='json', checked=True), dict(kind='export_submit'))]
    bridge = FakeBridge(states, [{'state': 'waiting'}, {'state': 'complete', 'path': str(download)}])
    model = FakeModel()
    stored = SchwabCollector(bridge, model, account_digits='321', store=tmp_path / 'store',
                             sleep=lambda s: None).collect(JOBS[0])
    actions = [(a, v) for op, a, v in bridge.sent if op == 'act']
    assert actions == [('navigate', 'history'), ('choose_date_range', 'all'), ('apply_search', None),
                       ('open_export', None), ('choose_format', 'json'), ('download_export', None)]
    assert model.asked == 1 and stored.read_bytes() == download.read_bytes()
    assert oct(stored.stat().st_mode & 0o777) == '0o600'


@pytest.mark.parametrize('model_action, code', [('request_user_help', 'model_needs_review'),
                                                ('request_user_authentication', 'authentication_required')])
def test_collector_never_exports_without_model_agreement(tmp_path, model_action, code):
    from pacioli.functions.chase_local_bridge import LocalChaseError
    from pacioli.functions.schwab_local import JOBS, SchwabCollector
    states = [page('positions', dict(kind='open_export')),
              page('positions_export', dict(kind='export_submit'))]
    bridge = FakeBridge(states, [])
    with pytest.raises(LocalChaseError, match=code):
        SchwabCollector(bridge, FakeModel(model_action), account_digits='321', store=tmp_path,
                        sleep=lambda s: None).collect(JOBS[3])
    assert ('act', 'download_export', None) not in bridge.sent


def test_collector_stops_on_wrong_account_or_sign_in(tmp_path):
    from pacioli.functions.chase_local_bridge import LocalChaseError
    from pacioli.functions.schwab_local import JOBS, SchwabCollector
    for state, code in ((page('history', dict(kind='open_export'), match=False), 'account_mismatch'),
                        ({'kind': 'authentication', 'controls': []}, 'authentication_required')):
        bridge = FakeBridge([state], [])
        with pytest.raises(LocalChaseError, match=code):
            SchwabCollector(bridge, FakeModel(), account_digits='321', store=tmp_path, sleep=lambda s: None).collect(JOBS[0])
        assert not [op for op, *_ in bridge.sent if op == 'act']


SELECTOR = """<style>button,li,select{display:block;margin:6px;height:28px;width:260px}</style>
<app-rgl><sdps-account-selector>
  <button id="accountSelectorBtn" aria-haspopup="true" onclick="window.opened=1">Contributory ...321</button>
</sdps-account-selector>
<div class="account-list"><a id="account-selector-header-0-account-1" href="#" onclick="window.picked='123'">Individual ...123</a>
  <a id="account-selector-header-0-account-2" href="#" onclick="window.picked='321'">Contributory ...321</a></div>
<select id="dateRangeSelectRgl"><option>Current Year</option><option>Previous Calendar Year</option></select>
<button id="lookupButton" onclick="window.searched=1">Search</button><button id="rglExportButton">Export</button></app-rgl>"""


def test_account_switching_and_rgl_search(firefox):
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin + '/approved')

        def js(body, args=None):
            return connection.script(connection.tab, body, args)
        js('document.body.innerHTML=args.html;return true;', {'html': SELECTOR})
        js(page_source(firefox) + ';return true;')

        def observe(task=None):
            return js('return globalThis.pacioliSchwab.observe(args.task);', {'task': task})

        def act(observed, kind, action, value=None, pick=None):
            target = [c for c in observed['controls'] if c['kind'] == kind and (pick is None or pick(c))]
            assert len(target) == 1, (kind, observed['controls'])
            return js('try { return globalThis.pacioliSchwab.act(args); } catch (e) { return {error: e.message}; }',
                      {'action': action, 'token': observed['token'], 'target_id': target[0]['id'], 'value': value})
        listed = observe({'list_accounts': True})
        options = [c for c in listed['controls'] if c['kind'] == 'account_option']
        assert [(o['digits'], o['type']) for o in options] == [('123', 'Individual'), ('321', 'Contributory')]
        plain = observe()
        assert all('digits' not in c for c in plain['controls'])
        assert act(plain, 'account', 'open_accounts') == {'acted': True} and js('return window.opened;') == 1
        seen = observe({'account_digits': '123'})
        assert [c.get('matches') for c in seen['controls'] if c['kind'] == 'account_option'] == [True, False]
        assert act(seen, 'account_option', 'choose_account', '999', lambda c: c['matches'])['error'] == 'invalid_target'
        seen = observe({'account_digits': '123'})
        assert act(seen, 'account_option', 'choose_account', '123', lambda c: c['matches']) == {'acted': True}
        assert js('return window.picked;') == '123'
        seen = observe()
        assert seen['kind'] == 'rgl' and act(seen, 'apply_search', 'apply_search') == {'acted': True}
        assert js('return window.searched;') == 1
