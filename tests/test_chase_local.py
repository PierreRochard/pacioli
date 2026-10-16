"""Offline controller, policy, transport and real-Firefox fixture checks."""
import base64
from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import chase_local as local
from pacioli.functions import chase_local_controller as controller
from pacioli.functions.chase_local_bridge import LocalChaseError, read_qfx_download
from pacioli.functions.chase_local_model import LocalModel
from pacioli.functions.ofx_file_import import import_chase_qfx
from pacioli.models import ChaseBrowserRuns, Connections, OFXFileImports
from scripts.chase_native_host import read_message, write_message
from tests.test_chase_browser import clean_chase_connector, clean_chase_feed, _csrf
from tests.test_chase_firefox import firefox, attach
from tests.test_chase_qfx import _qfx

ROOT = Path(__file__).resolve().parents[1]
PATH = '/connections/chase/local/'
TASK = {'last4': '1234', 'start': '09/01/2026', 'end': '09/03/2026'}


def test_extension_background_guards():
    subprocess.run(['node','--test','--test-concurrency=1',
                    str(ROOT/'tests/chase_local_background.test.cjs')],check=True,capture_output=True,text=True)


def observation():
    return {'kind': 'form', 'token': 'fixture', 'controls': [
        {'id': 11, 'kind': 'account', 'value': 'Checking (...1234)', 'enabled': True},
        {'id': 22, 'kind': 'format', 'value': 'Quicken Web Connect (QFX)', 'enabled': True},
        {'id': 33, 'kind': 'from', 'value': TASK['start'], 'enabled': True},
        {'id': 44, 'kind': 'to', 'value': TASK['end'], 'enabled': True},
        {'id': 55, 'kind': 'download', 'value': 'Download', 'enabled': True}]}


@pytest.mark.parametrize('days', [1,2,3,30,731])
def test_split_has_exact_coverage_no_gaps_or_repeated_boundary(days):
    start = date(2024, 1, 1); end = start+timedelta(days=days)
    parts = controller.split_interval(start.isoformat(), end.isoformat())
    assert parts[0][0] == start.isoformat() and parts[1][1] == end.isoformat()
    assert date.fromisoformat(parts[0][1])+timedelta(days=1) == date.fromisoformat(parts[1][0])
    assert sum((date.fromisoformat(b)-date.fromisoformat(a)).days+1 for a,b in parts) == days+1


def test_single_day_limit_stops_instead_of_infinite_split():
    with pytest.raises(LocalChaseError, match='minimum_interval_exceeded'):
        controller.split_interval('2026-09-01','2026-09-01')


def test_independent_policy_verifies_identity_dates_and_format():
    s = observation()
    assert controller.expected_step(s,TASK) == ('download_qfx',55)
    s['controls'][0]['value'] = 'Savings (...5678)'
    s['controls'][1]['value'] = 'CSV'
    assert controller.expected_step(s,TASK) == ('select_account',11)
    s['controls'][0]['value'] = 'Checking (...1234)'
    assert controller.expected_step(s,TASK) == ('select_qfx_format',22)
    s['controls'][1]['value'] = 'QFX'
    s['controls'][2]['value'] = '2026-09-02'
    assert controller.expected_step(s,TASK) == ('set_date_range',33)
    s['controls'][-1]['enabled'] = False
    assert controller.expected_step(s,TASK) == ('wait',None)


@pytest.mark.parametrize('label', ['Quicken or Microsoft Money (QIF)',
    'QuickBooks Web Connect (QBO)', 'Spreadsheet (Excel, CSV)', 'Quicken'])
def test_quicken_name_alone_does_not_authorize_non_qfx_formats(label):
    s=observation();s['controls'][1]['value']=label
    assert controller.expected_step(s,TASK)==('select_qfx_format',22)


def test_custom_file_menu_selects_qfx_when_quicken_qif_is_also_present():
    menu={'kind':'form','token':'menu','controls':[
        {'kind':'option','id':1,'label':'Quicken Web Connect (QFX)'},
        {'kind':'option','id':2,'label':'Quicken or Microsoft Money (QIF)'},
        {'kind':'option','id':3,'label':'QuickBooks Web Connect (QBO)'}]}
    actions=[]
    b=SimpleNamespace(command=lambda op,**kw:menu if op=='observe' else actions.append(kw))
    c=controller.Collector(b,None,{},lambda x:None,lambda:None,lambda *a:None,sleeper=lambda x:None)
    c.choose(observation(),'format',TASK)
    assert actions[-1]['target_id']==1
    assert actions[-1]['value']=='Quicken Web Connect (QFX)'


def test_identity_and_coverage_before_import():
    job = {'account_number': 'bank1234', 'start': '2026-09-01','end':'2026-09-03'}
    controller.verify_qfx(_qfx('bank1234'),job)
    for content, code in [(_qfx('other1234'),'qfx_identity_mismatch'),
                          (_qfx('bank1234').replace(b'<CURDEF>USD',b'<CURDEF>EUR'),'qfx_identity_mismatch'),
                          (_qfx('bank1234').replace(b'<DTEND>20260903',b'<DTEND>20260902'),'qfx_coverage_unverified')]:
        with pytest.raises(LocalChaseError,match=code): controller.verify_qfx(content,job)


def test_native_protocol_bounds_and_no_path_guessing(tmp_path):
    stream=BytesIO(); write_message(stream,{'op':'ping'}); stream.seek(0)
    assert read_message(stream)=={'op':'ping'}
    with pytest.raises(ValueError): read_message(BytesIO(struct.pack('=I',99999999)))
    with pytest.raises(EOFError): read_message(BytesIO(b'\x03'))
    path=tmp_path/'test.qfx'; path.write_bytes(_qfx('bank1234'))
    assert read_qfx_download(str(path))==path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    link=tmp_path/'link.qfx'; link.symlink_to(path)
    with pytest.raises(OSError): read_qfx_download(str(link))


def test_model_rejects_unknown_actions_ids_and_truncated_json(monkeypatch):
    model=LocalModel(); monkeypatch.setattr(model,'resources',lambda:None)
    s=observation(); s['image']=base64.b64encode(b'\xff\xd8'+b'x'*30).decode()
    answers=[{'evidence':'x','action':'transfer','target_id':55},
             {'evidence':'x','action':'download_qfx','target_id':999},
             {'evidence':'x','action':'download_qfx','target_id':True}]
    for answer in answers:
        monkeypatch.setattr(model,'api',lambda *a, answer=answer, **k: {'message':{'content':json.dumps(answer)}})
        with pytest.raises(LocalChaseError,match='local_model_invalid_response'):model.propose(s,TASK)


def test_controller_rejects_model_disagreement_without_action():
    commands=[]
    bridge=SimpleNamespace(command=lambda op,**kw: commands.append(op) or observation())
    model=SimpleNamespace(propose=lambda *a:{'action':'select_account','target_id':11})
    state={'jobs':[{'last4':'1234','start':'2026-09-01','end':'2026-09-03'}],'done':[]}
    c=controller.Collector(bridge,model,state,lambda x:None,lambda:None,lambda *a:None,sleeper=lambda x:None)
    with pytest.raises(LocalChaseError,match='model_needs_review'):c.collect()
    assert commands==['observe'] and len(state['jobs'])==1


def test_preview_never_activates_controls():
    commands=[]
    bridge=SimpleNamespace(command=lambda op,**kw: commands.append(op) or observation())
    model=SimpleNamespace(propose=lambda *a:{'action':'download_qfx','target_id':55})
    state={'jobs':[{'last4':'1234','start':'2026-09-01','end':'2026-09-03'}],'done':[]}
    result=controller.Collector(bridge,model,state,lambda x:None,lambda:None,lambda *a:None,preview=True).collect()
    assert result['action']=='download_qfx' and commands==['observe']


def test_controller_waits_for_navigation_but_bounds_unknown_pages():
    for final, expected_calls in [(observation(), 3), ({'kind':'unknown','controls':[]}, 11),
                                  ({'kind':'authentication','controls':[]}, 3),
                                  ({'kind':'error','controls':[]}, 3)]:
        calls=[]
        def command(op):
            calls.append(op)
            return {'kind':'unknown','controls':[]} if len(calls)<3 else final
        c=controller.Collector(SimpleNamespace(command=command),None,{},lambda x:None,
                               lambda:None,lambda *a:None,sleeper=lambda x:None)
        if final['kind']=='form': assert c.observe()==final
        else:
            with pytest.raises(LocalChaseError): c.observe()
        assert calls==['observe']*expected_calls


def test_bank_failure_during_download_stops_without_timeout_or_import():
    calls=[];observations=iter([observation(), {'kind':'error','controls':[]}])
    def command(op,**kw):
        calls.append(op)
        return next(observations) if op=='observe' else {'state':'waiting'}
    state={'jobs':[{'last4':'1234','start':'2026-09-01','end':'2026-09-03'}],'done':[]}
    c=controller.Collector(SimpleNamespace(command=command),
        SimpleNamespace(propose=lambda *a:{'action':'download_qfx','target_id':55}),state,
        lambda _:pytest.fail('No checkpoint may advance'),lambda:None,lambda *a:None,sleeper=lambda _:None)
    with pytest.raises(LocalChaseError,match='bank_export_failed'):c.collect()
    assert calls==['observe','act','download','observe'] and len(state['jobs'])==1


def test_observation_retries_layout_changes_but_never_retries_actions():
    calls=[]
    def command(op,**kw):
        calls.append(op)
        if len(calls)<3 or op=='act':raise LocalChaseError('stale_observation')
        return observation()
    c=controller.Collector(SimpleNamespace(command=command),None,{},lambda _:None,
        lambda:None,lambda *a:None,sleeper=lambda _:None)
    assert c.observe()==observation() and calls==['observe']*3
    with pytest.raises(LocalChaseError,match='stale_observation'):
        c.act(observation(),'download_qfx',55,TASK)
    assert calls==['observe']*3+['act']


def test_controller_navigates_only_the_requested_account_and_preview_does_not():
    accounts={'kind':'accounts','token':'account-snapshot','controls':[
        {'kind':'open_account','id':1,'value':'Savings (...5678)'},
        {'kind':'open_account','id':2,'value':'Checking (...1234)'}]}
    for preview in (False,True):
        commands=[]
        observations=iter([accounts,{'kind':'authentication','controls':[]}])
        def command(op,**kw):
            commands.append((op,kw))
            return next(observations) if op=='observe' else {'acted':True}
        state={'jobs':[{'last4':'1234','start':'2026-09-01','end':'2026-09-03'}],'done':[]}
        collector=controller.Collector(SimpleNamespace(command=command),None,state,
            lambda x:None,lambda:None,lambda *a:None,preview=preview,sleeper=lambda x:None)
        with pytest.raises(LocalChaseError,match='open_export_for_preview' if preview else 'authentication_required'):
            collector.collect()
        if preview: assert commands==[('observe',{})]
        else:
            assert commands[1][1]['action']=='open_account'
            assert commands[1][1]['target_id']==2


def test_real_firefox_account_navigation_and_custom_option_classification(firefox):
    source=(ROOT/'extensions/chase-local/content.js').read_text().replace(
        "['https://secure.chase.com', 'https://www.chase.com', 'https://chase.com']",json.dumps([firefox.origin]))
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin+'/approved')
        def js(body,args=None):return connection.script(connection.tab,body,args)
        def observe():return js('return globalThis.pacioliChase.observe();')
        def act(s,control,action,task=TASK):
            return js('try { return globalThis.pacioliChase.act(args); } catch(e) { return {error:e.message}; }',
                      {'token':s['token'],'target_id':control['id'],'action':action,'task':task,'value':control['label']})
        js('history.replaceState({}, "", "#/dashboard/overview");document.body.innerHTML=args.html;return true;',
           {'html':'''<style>a,button,[role=option]{display:block;margin:15px;height:40px;width:500px}</style>
             <a href="#/dashboard/summary/fixture" aria-label="View account details" onclick="window.accountClicks=(window.accountClicks||0)+1">Checking (…1234)</a>
             <button>Savings (...5678)</button><button>Transfer money</button>
             <div role="link">Rewards for Checking (...1234)</div>
             <button id="select-account_options_id-selector-no-label">Checking (...1234)</button>'''})
        js(source+';return true;')
        s=observe();assert s['kind']=='accounts'
        controls=[c for c in s['controls'] if c['kind']=='open_account'];assert len(controls)==2
        assert act(s,controls[1],'open_account')['error']=='account_not_allowed'
        assert act(s,controls[0],'open_account')=={'acted':True}
        assert js('return window.accountClicks;')==1

        js('history.replaceState({}, "", "#/dashboard/transactions/downloads/fixture");document.body.innerHTML=args.html;return true;',
           {'html':'<button style="width:300px;height:40px" onclick="window.exportClicks=1">Download other activity</button><button>Go back to accounts</button>'})
        s=observe();assert s['kind']=='overview'
        assert len(s['controls'])==1 and s['controls'][0]['kind']=='open_export'
        assert act(s,s['controls'][0],'open_export')=={'acted':True}
        assert js('return window.exportClicks;')==1

        js('history.replaceState({}, "", "#/dashboard/summary/fixture/DDA/CHK");document.body.innerHTML=args.html;return true;',
           {'html': '''<button id="quick-action-download-activity-tooltip" style="width:40px;height:40px"
              aria-label="There's no account activity showing to download."
              onclick="window.exportClicks++" disabled><svg width="20" height="20"></svg></button>
              <button>Transfer money</button>'''})
        s=observe();assert s['kind']=='overview' and len(s['controls'])==1
        assert s['controls'][0]['kind']=='open_export' and not s['controls'][0]['enabled']
        assert act(s,s['controls'][0],'open_export')['error']=='invalid_target'
        js('document.querySelector("button").disabled=false;return true;')
        s=observe();assert act(s,s['controls'][0],'open_export')=={'acted':True}
        assert js('return window.exportClicks;')==2
        js('history.replaceState({}, "", "#/dashboard/other");return true;')
        assert observe()['controls']==[]

        js('document.body.innerHTML=args.html;return true;', {'html':'''<style>button,[role=option]{display:block;margin:15px;height:40px;width:500px}</style>
          <button id="downloadButton">Download</button>
          <button id="select-showing-activity-selector-no-label">Previous selection</button>
          <input type="radio" name="showing-activity-date-range" style="width:30px;height:30px">
          <div role="option" id="showing-activity-date-range-option" onclick="window.optionClicks=(window.optionClicks||0)+1">Choose a date range Choose a date range</div>
          <div role="option" id="account-selector-option">Checking (...1234) Checking (...1234)</div>'''})
        s=observe();assert s['kind']=='form'
        assert len([c for c in s['controls'] if c['kind']=='range'])==1
        options=[c for c in s['controls'] if c['kind']=='option']
        assert [c['label'] for c in options]==['Choose a date range','Checking (...1234)']
        assert act(s,options[0],'choose_option')=={'acted':True}
        assert js('return window.optionClicks;')==1


def test_real_firefox_control_ids_stale_pages_and_download_guards(firefox):
    html='''<style>input,select,button {display:block;margin:15px;width:350px;height:45px}</style>
    <select id="downloadActivityAccounts"><option value="a">Checking (...1234)</option><option value="b">Savings (...5678)</option></select>
    <select id="fileType"><option value="qfx">Quicken Web Connect (QFX)</option><option value="csv">Spreadsheet (CSV)</option><option value="qif">Quicken or Microsoft Money (QIF)</option></select>
    <label for="fromDate">From</label><input id="fromDate" value="09/01/2026">
    <label for="toDate">To</label><input id="toDate" value="09/03/2026">
    <button id="downloadButton" onclick="window.downloadClicks=(window.downloadClicks||0)+1">Download</button>
    <button id="payment" onclick="window.paymentClicks=1">Pay card</button>'''
    source=(ROOT/'extensions/chase-local/content.js').read_text()
    # Only the isolated test copy allows the loopback fake bank.
    source=source.replace("['https://secure.chase.com', 'https://www.chase.com', 'https://chase.com']",json.dumps([firefox.origin]))
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin+'/approved')
        connection.script(connection.tab,'document.body.innerHTML=args.html;return true;',{'html':html})
        connection.script(connection.tab,source+';return true;')
        def js(body,args=None):return connection.script(connection.tab,body,args)
        def observe():return js('return globalThis.pacioliChase.observe();')
        def act(s,kind,action,task=TASK,**extra):
            target=next(c['id'] for c in s['controls'] if c['kind']==kind)
            return js('try { return globalThis.pacioliChase.act(args.c); } catch(e) { return {error:e.message}; }',
                      {'c':dict(token=s['token'],target_id=target,action=action,task=task,**extra)})
        s=observe(); assert len(s['controls'])==5
        assert controller.expected_step(s,TASK)[0]=='download_qfx'
        js("document.body.insertAdjacentHTML('afterbegin', '<p>If you have more than 1,200 transactions to display, create more than one report.</p>');return true;")
        s=observe();assert s['kind']=='form'
        js("document.body.insertAdjacentHTML('afterbegin', '<button id=fixture-limit>Change date range</button>');return true;")
        assert observe()['kind']=='limit'
        js("document.getElementById('fixture-limit').remove();return true;")
        s=observe();assert s['kind']=='form'
        assert all(c['label']!='Pay card' for c in s['controls'])
        assert act(s,'download','download_qfx')=={'acted':True}
        assert js('return window.downloadClicks;')==1
        assert act(s,'download','download_qfx')['error']=='stale_observation'
        s=observe()
        js("document.getElementById('fileType').value='csv';return true;")
        assert act(s,'download','download_qfx')['error']=='stale_observation'
        s=observe()
        assert act(s,'download','download_qfx')['error']=='export_mismatch'
        s=observe()
        assert act(s,'format','select_qfx_format',value='qfx')=={'acted':True}
        s=observe()
        assert act(s,'format','select_qfx_format',value='qif')['error']=='option_not_allowed'
        js("document.getElementById('fileType').value='qif';return true;")
        s=observe()
        assert act(s,'download','download_qfx')['error']=='export_mismatch'
        s=observe()
        assert act(s,'from','set_date_range',value='09/01/2000')['error']=='date_not_allowed'
        js("document.body.innerHTML='<input type=password>';return true;")
        s=observe(); assert s['kind']=='authentication' and s['controls']==[]
        js('document.body.innerHTML=args.html;return true;',
           {'html': "<h1>We've signed you out of your account.</h1><a>Sign in</a>"})
        s=observe(); assert s['kind']=='authentication' and s['controls']==[]
        js("history.pushState({}, '', '/logout');document.body.innerHTML='<h1>Goodbye</h1>';return true;")
        s=observe(); assert s['kind']=='authentication' and s['controls']==[]


def test_real_firefox_nested_shadow_dates_and_internal_overlay(firefox):
    source=(ROOT/'extensions/chase-local/content.js').read_text().replace(
        "['https://secure.chase.com', 'https://www.chase.com', 'https://chase.com']",json.dumps([firefox.origin]))
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin+'/approved')
        def js(body,args=None):return connection.script(connection.tab,body,args)
        js('''document.body.innerHTML='<button id="downloadButton">Download</button><fixture-field></fixture-field>';
          const outer=document.querySelector('fixture-field').attachShadow({mode:'open'});
          outer.innerHTML='<fixture-input></fixture-input>';
          const inner=outer.querySelector('fixture-input').attachShadow({mode:'open'});
          inner.innerHTML='<style>:host{display:block;position:relative;margin:30px;width:400px;height:50px} input{box-sizing:border-box;width:400px;height:50px}</style><input id="fromDate">';
          window.fixtureInputRoot=inner;
          document.addEventListener('input',()=>window.fixtureInputEvents=(window.fixtureInputEvents||0)+1);
          return true;''')
        js(source+';return true;')
        def attempt():
            s=js('return globalThis.pacioliChase.observe();')
            field=next(c for c in s['controls'] if c['kind']=='from')
            assert field['shadow_hosts']==['fixture-input','fixture-field']
            return js('try { return globalThis.pacioliChase.act(args); } catch(e) { return {error:e.message}; }',
                {'token':s['token'],'target_id':field['id'],'action':'set_date_range','task':TASK,'value':TASK['start']})
        assert attempt()=={'acted':True}
        assert js('return window.fixtureInputRoot.querySelector("input").value;')==TASK['start']
        assert js('return window.fixtureInputEvents;')==1
        js('''const cover=document.createElement('div');cover.style.cssText='position:absolute;inset:0;background:white;z-index:100';
          window.fixtureInputRoot.append(cover);return true;''')
        assert attempt()['error']=='control_obscured'


def test_local_run_route_csrf_encrypted_checkpoint_resume(clean_chase_connector,client,admin_user,app,monkeypatch):
    assert client.get(PATH).status_code==302
    client.post('/login/',data=admin_user)
    fake=[{'id':123,'account_number':'secret-bank-1234','last4':'1234','label':'Chase checking 1234'}]
    monkeypatch.setattr(local,'accounts',lambda:fake)
    launches=[];monkeypatch.setattr(local,'_launch_systemd',launches.append)
    prior=app.config['WTF_CSRF_ENABLED'];app.config['WTF_CSRF_ENABLED']=True
    try:
        page=client.get(PATH);assert page.status_code==200 and page.headers['Cache-Control']=='no-store'
        assert b'secret-bank' not in page.data
        data={'action':'start','account_id':'123','start':'2026-09-01','end':'2026-09-03','reviewed_usd':'yes'}
        assert client.post(PATH,data=data).status_code==400
        data['csrf_token']=_csrf(page.data);assert client.post(PATH,data=data).status_code==302
        run=db.session.query(ChaseBrowserRuns).one();assert launches==[run.id]
        raw=db.session.execute(text('SELECT diagnostics FROM admin.chase_browser_runs WHERE id=:id'),{'id':run.id}).scalar()
        assert raw.startswith('fernet:') and 'secret-bank' not in raw
        status=client.get(PATH+'status/').json['run'];assert status['active'] and 'secret-bank' not in json.dumps(status)
        run.status='needs_attention';run.error_code='authentication_required';db.session.commit()
        page=client.get(PATH)
        client.post(PATH,data={'action':'resume','run_id':run.id,'csrf_token':_csrf(page.data)})
        db.session.refresh(run);assert run.status=='queued' and launches==[run.id,run.id]
    finally:app.config['WTF_CSRF_ENABLED']=prior


def test_real_import_resume_is_idempotent(clean_chase_feed,tmp_path,monkeypatch):
    number=uuid4().hex[:12]+'1234'; content=_qfx(number)
    initial=import_chase_qfx(content,reviewed_usd=True)
    new=content.replace(b'deposit-1',b'deposit-2').replace(b'expense-1',b'expense-2')
    path=tmp_path/'fixture.qfx';path.write_bytes(new)
    job={'last4':'1234','account_number':number,'account_id':initial['account_id'],'start':'2026-09-01','end':'2026-09-03'}
    commands=[]
    def command(op,**kw):
        commands.append(op)
        return {'state':'complete','path':str(path)} if op=='download' else observation()
    bridge=SimpleNamespace(command=command)
    model=SimpleNamespace(propose=lambda *a:{'action':'download_qfx','target_id':55})
    for expected in (False,True):
        state={'jobs':[deepcopy(job)],'done':[]}
        result=controller.Collector(bridge,model,state,lambda x:None,lambda:None,lambda *a:None,sleeper=lambda x:None).collect()
        assert result['completed'] and state['done'][0]['duplicate'] is expected
    count=db.session.execute(text('SELECT COUNT(*) FROM ofx.stmttrn WHERE acctfrom_id=:id'),{'id':initial['account_id']}).scalar()
    assert count==4


def test_limit_error_subdivides_before_retry_and_preserves_checkpoint(monkeypatch):
    state={'jobs':[{'last4':'1234','start':'2026-09-01','end':'2026-09-03'}],'done':[]}
    saved=[];calls=[]
    responses=iter([{'kind':'limit','token':'one','controls':[{'kind':'change_range','id':7}]},
                    {'kind':'authentication','controls':[]}])
    def command(op,**kw):
        calls.append((op,kw))
        return next(responses) if op=='observe' else {'acted':True}
    c=controller.Collector(SimpleNamespace(command=command),None,state,
        lambda value:saved.append(deepcopy(value)),lambda:None,lambda *a:None,sleeper=lambda x:None)
    with pytest.raises(LocalChaseError,match='authentication_required'):c.collect()
    assert [(j['start'],j['end']) for j in saved[0]['jobs']]==[('2026-09-01','2026-09-02'),('2026-09-03','2026-09-03')]
    assert calls[1][1]['action']=='reduce_date_range'


@pytest.mark.skipif(os.environ.get('PACIOLI_TEST_LOCAL_MODEL')!='1',reason='Opt-in serial GPU integration test')
def test_production_model_contract_with_firefox_screens(firefox):
    html='''<style>body{font:24px sans-serif} input,select,button{font:22px sans-serif;display:block;margin:15px;width:600px;height:48px}</style>
      <h1>TEST BANK - SYNTHETIC</h1>
      <select id="downloadActivityAccounts"><option value="a">Checking (...1234)</option><option value="b">Savings (...5678)</option></select>
      <select id="fileType"><option value="qfx">Quicken Web Connect (QFX)</option><option value="csv">Spreadsheet (CSV)</option></select>
      <label for="fromDate">From</label><input id="fromDate" value="09/01/2026">
      <label for="toDate">To</label><input id="toDate" value="09/03/2026">
      <button id="downloadButton">Download</button>'''
    source=(ROOT/'extensions/chase-local/content.js').read_text().replace(
        "['https://secure.chase.com', 'https://www.chase.com', 'https://chase.com']",json.dumps([firefox.origin]))
    progress = []
    with attach(firefox) as connection, LocalModel(report=lambda kind, **details: progress.append((kind, details))) as model:
        connection.page.goto(firefox.origin+'/approved')
        connection.script(connection.tab,'document.body.innerHTML=args.html;return true;',{'html':html})
        connection.script(connection.tab,source+';return true;')
        for script,expected in [('', 'download_qfx'),
            ("document.getElementById('fileType').value='csv';",'select_qfx_format'),
            ("document.getElementById('downloadActivityAccounts').value='b';",'select_account'),
            ("document.getElementById('downloadActivityAccounts').value='a';document.getElementById('fileType').value='qfx';document.getElementById('fromDate').value='09/02/2026';",'set_date_range')]:
            if script: connection.script(connection.tab,script+'return true;')
            observed=connection.script(connection.tab,'return globalThis.pacioliChase.observe();')
            observed['image']=connection.command('browsingContext.captureScreenshot',{'context':connection.tab,'origin':'viewport'})['data']
            before = len(progress)
            answer=model.propose(observed,TASK)
            print('Synthetic production prompt:',expected,answer,
                  [(c['kind'], c['value'], c['box']) for c in observed['controls']],flush=True)
            assert answer['action']==expected
            assert (answer['action'],answer['target_id'])==controller.expected_step(observed,TASK)
            request_progress = progress[before:]
            assert request_progress[0][0] == 'model_request'
            assert request_progress[-1][0] == 'model_response'
            chunks = [details['chunks'] for kind, details in request_progress if kind == 'model_output']
            assert len(chunks) >= 2 and chunks[0] == 1 and chunks[-1] > chunks[0]
