"""Durable live progress: real DB replay, streaming, imports and Firefox UI."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import chase_local as local, chase_local_events as events
from pacioli.functions import chase_browser as browser, chase_local_controller as controller
from pacioli.functions.chase_local_bridge import LocalChaseError
from pacioli.functions.chase_local_model import LocalModel
from pacioli.functions.ofx_file_import import import_chase_qfx
from pacioli.models import Users, ChaseLocalEvents
from tests.test_chase_browser import clean_chase_connector, _csrf
from tests.test_chase_native import clean_native_feed
from tests.test_chase_qfx import _qfx
from tests.test_chase_local import observation, PATH
from tests.test_chase_firefox import firefox, attach


@pytest.fixture
def progress_run(clean_chase_connector, client, admin_user, monkeypatch):
    client.post('/login/', data=admin_user)
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    monkeypatch.setattr(local, '_launch_systemd', lambda _: None)
    monkeypatch.setattr(local, 'accounts', lambda: [{'id':123,'account_number':'private-1234',
                                                   'last4':'1234','label':'Fixture account'}])
    return local.start(user.id, [123], '2026-09-01', '2026-09-03', True)


def test_event_replay_encryption_redaction_pagination_and_owner(progress_run, client, monkeypatch):
    run = progress_run
    monkeypatch.setattr(events, 'PAGE_SIZE', 2)
    events.record(run.id, 'page_ready', last4='1234', page='form',
                  password='do-not-store', image='do-not-store', account_number='private-1234',
                  action={'injected':'value'}, duration_ms=True, start='invalid')
    events.record(run.id, 'model_request')
    raw = db.session.execute(text('SELECT details FROM admin.chase_local_events ORDER BY id')).fetchall()
    assert all(value.startswith('fernet:') for value, in raw)
    decoded = [json.loads(row.details) for row in db.session.query(ChaseLocalEvents).all()]
    assert decoded[1] == {'last4':'1234', 'page':'form'}
    first = client.get(PATH+'status/').json
    assert first['has_more'] and len(first['events']) == 2
    assert 'private-' not in json.dumps(first) and 'do-not-store' not in json.dumps(first)
    second = client.get(PATH+f'status/?run_id={run.id}&after={first["cursor"]}').json
    assert not second['has_more'] and [e['kind'] for e in second['events']] == ['model_request']
    assert events.snapshot(run.requested_by_id + 1000, run.id, 0)['run'] is None
    # A cursor belonging to another run cannot suppress this run's history.
    assert client.get(PATH+'status/?run_id=0&after=999999').json['events'] == first['events']
    assert client.get(PATH+'status/?after=-1').status_code == 400
    assert client.get(PATH+'events/', headers={'Last-Event-ID':'malformed'}).status_code == 400
    client.post('/logout/')
    assert client.get(PATH+'status/').status_code == 302
    assert client.get(PATH+'events/').status_code == 302


def test_sse_replays_cursor_and_releases_slot_on_disconnect(progress_run, client):
    run = progress_run
    cursor = events.snapshot(run.requested_by_id)['cursor']
    events.record(run.id, 'worker_started')
    response = client.get(PATH+'events/', headers={'Last-Event-ID':f'{run.id}:{cursor}'}, buffered=False)
    assert response.mimetype == 'text/event-stream'
    assert response.headers['X-Accel-Buffering'] == 'no'
    assert response.headers['Cache-Control'] == 'no-store'
    chunks = iter(response.response)
    assert b'retry:' in next(chunks)
    packet = next(chunks).decode()
    assert 'worker_started' in packet and '"kind":"queued"' not in packet
    response.close()
    # Four streams may be active; ordinary requests retain four web threads.
    acquired = [events.STREAM_SLOTS.acquire(blocking=False) for _ in range(4)]
    try:
        assert all(acquired)
        assert client.get(PATH+'events/').status_code == 503
        assert client.get(PATH+'status/').status_code == 200
    finally:
        for acquired_slot in acquired:
            if acquired_slot: events.STREAM_SLOTS.release()


def test_stream_observes_new_commits_and_terminal_event(progress_run):
    run_id, user_id = progress_run.id, progress_run.requested_by_id
    stream = events.stream(user_id, run_id, 0)
    assert next(stream).startswith('retry:')
    assert 'queued' in next(stream)
    events.record(run_id, 'worker_started')
    assert 'worker_started' in next(stream)
    browser._set_run(run_id, 'completed')
    assert '"kind":"completed"' in next(stream)
    assert 'event: idle' in next(stream)
    stream.close()


def test_quiet_stream_sends_heartbeat_and_renews_authentication(progress_run, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(events.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(events.time, 'sleep', lambda _: None)
    stream = events.stream(progress_run.requested_by_id, progress_run.id, 0)
    next(stream); next(stream)
    clock[0] = 6
    assert 'event: heartbeat' in next(stream)
    clock[0] = 21
    assert 'event: reconnect' in next(stream)
    stream.close()


def test_cancel_failure_stale_and_resume_events_are_durable(progress_run):
    run = progress_run
    browser.cancel_run(run.id); browser.cancel_run(run.id)
    assert db.session.query(ChaseLocalEvents).filter_by(kind='cancel_requested').count() == 1
    browser._set_run(run.id, 'cancelled')
    local.resume(run.id, run.requested_by_id)
    assert local.checkpoint(run)['phase'] == 'queued'
    run.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.session.commit(); browser.expire_stale_runs()
    packet = events.snapshot(run.requested_by_id)
    assert [e['kind'] for e in packet['events']] == ['queued','cancel_requested','cancelled','resumed','failed']
    assert packet['run']['resumable'] and packet['run']['status'] == 'failed'


def test_json_start_preserves_csrf_and_traditional_forms(progress_run, client, app):
    prior = app.config['WTF_CSRF_ENABLED']; app.config['WTF_CSRF_ENABLED'] = True
    try:
        headers = {'Accept':'application/json'}
        assert client.post(PATH, data={'action':'cancel','run_id':progress_run.id}, headers=headers).status_code == 400
        token = _csrf(client.get(PATH).data)
        assert client.post(PATH+'?after=-1', data={'action':'cancel','run_id':progress_run.id,
            'csrf_token':token}, headers=headers).status_code == 400
        db.session.refresh(progress_run)
        assert not progress_run.cancel_requested
        response = client.post(PATH, data={'action':'cancel','run_id':progress_run.id,'csrf_token':token}, headers=headers)
        assert response.status_code == 200 and response.json['run']['cancel_requested']
        assert response.headers['Cache-Control'] == 'no-store'
    finally:
        app.config['WTF_CSRF_ENABLED'] = prior


def test_worker_records_authentication_stop_after_browser_cleanup(progress_run, monkeypatch):
    calls = []
    def command(op, **kwargs):
        calls.append(op)
        return {'ready':True,'version':2} if op == 'ping' else {'kind':'authentication', 'controls':[]}
    class FakeModel:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(local, 'FirefoxBridge', lambda **kwargs:SimpleNamespace(command=command))
    monkeypatch.setattr(local, 'LocalModel', FakeModel)
    local.run_local_worker(progress_run.id)
    packet = events.snapshot(progress_run.requested_by_id)
    kinds = [e['kind'] for e in packet['events']]
    assert kinds[-3:] == ['browser_release','browser_released','needs_attention']
    assert 'page_ready' in kinds and calls == ['ping','prepare','observe','release']
    assert packet['run']['remaining'] == 1 and packet['run']['completed'] == 0
    assert 'Complete Chase verification' in packet['run']['message']
    assert packet['events'][-1]['details']['reason'] == 'authentication_required'
    local.resume(progress_run.id, progress_run.requested_by_id)
    resumed = events.snapshot(progress_run.requested_by_id)
    assert resumed['run']['error_code'] is None
    assert 'Complete Chase verification' in resumed['events'][-2]['message']


def test_model_stream_emits_counts_before_completion_without_generated_text():
    reported = []; beats = []
    model = LocalModel(report=lambda kind, **kw:reported.append((kind, kw)), heartbeat=lambda:beats.append(True))
    source = BytesIO(b'{"message":{"content":"private-model-text"},"done":false}\n'
                     b'{"message":{"content":""},"done":true,"eval_count":1}\n')
    result = model.read_stream(source)
    assert result['message']['content'] == 'private-model-text'
    assert len(reported) == 2 and reported[0][1]['chunks'] == 1 and len(beats) == 2
    assert 'private-model-text' not in json.dumps(reported)
    for invalid in [b'',b'{}\n',b'{"error":"private-details"}\n',b'{"message":null}\n', b'x'*65537]:
        with pytest.raises(LocalChaseError, match='local_model_invalid_response'):
            model.read_stream(BytesIO(invalid))


def test_failed_progress_record_still_releases_gpu():
    def fail(kind, **kwargs):
        raise RuntimeError('progress storage failed')
    model = LocalModel(report=fail)
    model.loaded = True
    calls = []
    model.api = lambda *args: calls.append(args)
    with pytest.raises(RuntimeError, match='progress storage failed'):
        model.__exit__(None, None, None)
    assert calls == [('generate', {'model':'qwen3-vl:8b-instruct', 'keep_alive':0})]


def test_import_timeline_replay_and_rollback(clean_native_feed, tmp_path):
    number = uuid4().hex[:12]+'1234'; content = _qfx(number)
    initial = import_chase_qfx(content, reviewed_usd=True)
    path = tmp_path/'fixture.qfx'; path.write_bytes(content)
    job = {'last4':'1234','account_number':number,'account_id':initial['account_id'],
           'start':'2026-09-01','end':'2026-09-03'}
    bridge = SimpleNamespace(command=lambda op,**kw: {'state':'complete','path':str(path)} if op=='download' else observation())
    model = SimpleNamespace(propose=lambda *a: {'action':'download_qfx','target_id':55})
    seen = []
    state = {'jobs':[deepcopy(job)], 'done':[]}
    controller.Collector(bridge, model, state, lambda _:None, lambda:None, lambda *a:None,
        sleeper=lambda _:None, report=lambda kind,**kw:seen.append((kind,kw))).collect()
    kinds = [kind for kind,_ in seen]
    assert kinds.index('qfx_validated') < kinds.index('import_lock') < kinds.index('import_commit') < kinds.index('import_committed') < kinds.index('checkpoint_saved')
    committed = next(d for k,d in seen if k == 'import_committed')
    assert committed == {'duplicate':True,'transactions':2,'new_transactions':0,'existing_transactions':2}
    assert kinds.index('import_committed') < kinds.index('balance_captured') < kinds.index('opening_ready')
    balance = state['balances'][str(initial['account_id'])]
    assert balance['balance'] == '575.00' and balance['opening_candidate'] == '500.00'
    assert balance['fetched_balance_asof'].startswith('2026-09-03')
    assert number not in json.dumps(seen) and str(path) not in json.dumps(seen)
    before = db.session.execute(text('SELECT count(*) FROM ofx.stmttrn')).scalar()
    def fail_before_commit(kind, **kw):
        if kind == 'import_commit': raise RuntimeError('simulated failure before commit')
    with pytest.raises(RuntimeError):
        import_chase_qfx(content.replace(b'expense-1', b'expense-2'), reviewed_usd=True, progress=fail_before_commit)
    assert db.session.execute(text('SELECT count(*) FROM ofx.stmttrn')).scalar() == before


def test_firefox_live_timeline_reconnect_dedup_and_immediate_submit(progress_run, client, firefox):
    html = client.get(PATH).data.decode()
    script = (Path(__file__).parents[1]/'pacioli/static/chase-progress.js').read_text()
    packet = events.snapshot(progress_run.requested_by_id)
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin+'/approved')
        def js(body, args=None): return connection.script(connection.tab, body, args)
        js('''document.documentElement.innerHTML=args.html;
          window.fixturePacket=args.packet;window.fixtureFetches=[];window.fixtureStreams=[];
          window.fetch=async(url,options={})=>{
            window.fixtureFetches.push({url,method:options.method||'GET'});
            if(options.method==='POST')return new Promise(resolve=>window.fixtureResolve=resolve);
            return {ok:true,json:async()=>window.fixturePacket};
          };
          window.EventSource=class{
            constructor(url){this.url=url;this.listeners={};window.fixtureStreams.push(this);}
            addEventListener(kind,callback){this.listeners[kind]=callback;}
            close(){this.closed=true;}
          };return true;''', {'html':html,'packet':packet})
        js(script+';return true;')
        assert js('return document.querySelectorAll("#local-events li").length;') == 1
        events.record(progress_run.id,'model_request')
        changed = events.snapshot(progress_run.requested_by_id, progress_run.id, packet['cursor'])
        changed['run']['balances'] = [{'account_id':123, 'account_name':'<img src=x onerror="window.pwned=true">',
            'balance':'1234567890123456.78', 'balance_asof':'2026-09-03T12:00:00+00:00',
            'balance_imported_at':'2026-09-04T12:00:00+00:00', 'balance_is_current':False,
            'coverage_start':'2026-09-01', 'coverage_end':'2026-09-03',
            'signed_activity':None, 'opening_candidate':None, 'opening_date':'2026-09-01',
            'opening_blocker':'Coverage missing', 'coverage_gaps':[{'start':'2026-09-02','end':'2026-09-02'}]}]
        js('window.fixtureStreams.at(-1).listeners.progress({data:JSON.stringify(args)});return true;', changed)
        assert js('return document.querySelectorAll("#local-events li").length;') == 2
        balance_text = js('return document.querySelector("#local-balances").textContent;')
        assert '1234567890123456.78' in balance_text and 'Blocked' in balance_text
        assert 'Missing 2026-09-02 through 2026-09-02' in balance_text
        assert js('return document.querySelectorAll("#local-balances img").length;') == 0
        js('window.fixtureStreams.at(-1).listeners.progress({data:JSON.stringify(args)});return true;', packet)
        assert js('return document.querySelectorAll("#local-events li").length;') == 2
        # Reconnect replay is idempotent; page updates while the job is still active.
        js('window.fixtureStreams.at(-1).listeners.progress({data:JSON.stringify(args)});return true;', changed)
        assert js('return document.querySelectorAll("#local-events li").length;') == 2
        assert 'local model' in js('return document.querySelector("#local-step").textContent;')
        assert 'Live updates connected' in js('return document.querySelector("#local-live").textContent;')
        assert re.fullmatch(r'\d{2}:\d{2}:\d{2}[.,]\d{3}', js('return document.querySelector("#local-events time").textContent;'))
        js('window.fixturePacket=args;return true;', changed)
        js('document.querySelector("#local-cancel button").click();return true;')
        assert 'Sending your request' in js('return document.querySelector("#local-live").textContent;')
        assert js('return document.querySelector("#local-cancel button").disabled;')
        browser.cancel_run(progress_run.id)
        cancelled = events.snapshot(progress_run.requested_by_id)
        js('window.fixturePacket=args;window.fixtureResolve({ok:true,json:async()=>args});return true;', cancelled)
        assert 'Cancellation requested' in js('return document.querySelector("#local-message").textContent;')
        deadline = time.monotonic() + 5
        while js('return !!window.fixtureStreams.at(-1).closed;'):
            assert time.monotonic() < deadline
            time.sleep(.1)
        # Text is rendered safely, including a malicious server message.
        malicious = deepcopy(cancelled)
        malicious['events'].append({'id':999999,'at':datetime.now(timezone.utc).isoformat(),
            'kind':'page_ready','message':'<img src=x onerror="window.pwned=true">','details':{}})
        malicious['cursor'] = 999999
        js('window.fixtureStreams.at(-1).listeners.progress({data:JSON.stringify(args)});return true;', malicious)
        assert '<img src=x' in js('return document.querySelector("#local-events").textContent;')
        assert js('return document.querySelectorAll("#local-events img").length;') == 0
        assert js('return window.pwned===undefined;')
        # A dropped stream falls back to polling, then reconnects with a cursor.
        previous = js('return window.fixtureFetches.length;')
        js('window.fixturePacket=args;window.fixtureStreams.at(-1).onerror();return true;', cancelled)
        deadline = time.monotonic() + 5
        while js('return window.fixtureFetches.length;') == previous:
            assert time.monotonic() < deadline
            time.sleep(.1)
        assert js('return window.fixtureFetches.at(-1).url.includes("after=");')
