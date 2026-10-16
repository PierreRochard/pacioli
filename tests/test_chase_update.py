"""Incremental one-button updates: real database replays, races and coverage."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from io import BytesIO
import json
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import chase_local as local, chase_local_controller as controller
from pacioli.functions.chase_local_bridge import LocalChaseError
from pacioli.functions.ofx_file_import import import_chase_qfx
from pacioli.models import ChaseBrowserRuns, ChaseLocalPreferences, Users, ChaseExportCoverage, OFXFileImports
from tests.test_chase_browser import clean_chase_connector, clean_chase_feed, _csrf
from tests.test_chase_qfx import _qfx
from tests.test_chase_local import observation, PATH
from scripts.chase_native_host import read_message, write_message


def test_native_bridge_sends_large_messages_when_socket_writes_are_partial():
    class ShortSocket(BytesIO):
        def write(self, data):
            return super().write(data[:997])
    stream=ShortSocket()
    expected={'result':{'image':'x'*500000,'kind':'form'}}
    write_message(stream,expected)
    stream.seek(0)
    assert read_message(stream)==expected


def test_transaction_bounds_require_verified_request_and_remain_in_source(clean_chase_feed):
    number=uuid4().hex[:12]+'1234';content=_qfx(number)
    job={'account_number':number,'start':'2026-08-31','end':'2026-09-04'}
    with pytest.raises(LocalChaseError,match='qfx_coverage_unverified'):
        controller.verify_qfx(content,job)
    controller.verify_qfx(content,job,verified_export=True)
    for bad in (content.replace(b'<DTSTART>20260901',b'<DTSTART>20260902'),
                content.replace(b'<DTPOSTED>20260903',b'<DTPOSTED>20260905')):
        with pytest.raises(LocalChaseError,match='qfx_coverage_unverified'):
            controller.verify_qfx(bad,job,verified_export=True)
    initial=import_chase_qfx(content,reviewed_usd=True)
    interval=(date(2026,8,31),date(2026,9,4))
    for _ in range(2):
        result=import_chase_qfx(content,reviewed_usd=True,allowed_account_ids=[initial['account_id']],verified_interval=interval)
        assert result['duplicate']
    source=db.session.query(OFXFileImports).filter_by(sha256=initial['sha256']).one()
    assert source.coverage_start.date()==date(2026,9,1) and source.coverage_end.date()==date(2026,9,3)
    evidence=db.session.query(ChaseExportCoverage).filter_by(file_import_id=source.id).all()
    assert len(evidence)==1 and (evidence[0].start,evidence[0].end)==interval
    with pytest.raises(ValueError,match='outside'):
        import_chase_qfx(content,reviewed_usd=True,allowed_account_ids=[initial['account_id']],
                         verified_interval=(date(2026,9,2),date(2026,9,4)))
    assert db.session.query(ChaseExportCoverage).filter_by(file_import_id=source.id).count()==1


def test_import_and_verified_interval_roll_back_together(clean_chase_feed):
    number=uuid4().hex[:12]+'1234';initial=import_chase_qfx(_qfx(number),reviewed_usd=True)
    content=_qfx(number).replace(b'expense-1',b'expense-2')
    with pytest.raises(ValueError,match='outside'):
        import_chase_qfx(content,reviewed_usd=True,allowed_account_ids=[initial['account_id']],
                         verified_interval=(date(2026,9,2),date(2026,9,4)))
    assert db.session.execute(text('SELECT COUNT(*) FROM ofx.stmttrn WHERE acctfrom_id=:id'),{'id':initial['account_id']}).scalar()==2
    assert db.session.query(OFXFileImports).filter_by(ofx_account_id=initial['account_id']).count()==1


def test_native_bridge_rejects_zero_byte_write():
    with pytest.raises(OSError,match='message_write_failed'):
        write_message(SimpleNamespace(write=lambda _:0,flush=lambda:None),{'op':'ping'})


def test_incremental_dates_fill_gaps_and_overlap_without_reexporting_all_history():
    intervals=[(date(2024,9,1),date(2025,1,1)),
               (date(2025,1,10),date(2026,9,20)),
               (date(2026,9,18),date(2026,9,24))]
    assert local.incremental_ranges(intervals,date(2026,9,25),7)==[
        (date(2025,1,2),date(2025,1,9)),(date(2026,9,18),date(2026,9,25))]
    assert local.incremental_ranges([(date(2026,9,25),date(2026,9,25))],date(2026,9,25),7)==[
        (date(2026,9,25),date(2026,9,25))]
    # A nearby missing date coalesces with the recent overlap.
    assert local.incremental_ranges([(date(2026,9,1),date(2026,9,20)),
        (date(2026,9,22),date(2026,9,24))],date(2026,9,25),7)==[(date(2026,9,18),date(2026,9,25))]


@pytest.mark.parametrize('intervals', [[],[(None,date(2026,9,1))],
    [(date(2026,9,2),date(2026,9,1))],[(date(2027,1,1),date(2027,1,2))]])
def test_unknown_coverage_never_becomes_silent_success(intervals):
    with pytest.raises(LocalChaseError,match='coverage_missing'):
        local.incremental_ranges(intervals,date(2026,9,25),7)


def test_one_button_persists_settings_reuses_and_resumes_job(
        clean_chase_connector,clean_chase_feed,client,admin_user,app,monkeypatch):
    initial=import_chase_qfx(_qfx(uuid4().hex[:12]+'1234'),reviewed_usd=True)
    aid=initial['account_id']
    client.post('/login/',data=admin_user)
    launches=[];monkeypatch.setattr(local,'_launch_systemd',launches.append)
    old=app.config['WTF_CSRF_ENABLED'];app.config['WTF_CSRF_ENABLED']=True
    try:
        page=client.get(PATH)
        assert b'Update Chase' in page.data
        assert client.post(PATH,data={'action':'update'}).status_code==400
        token=_csrf(page.data)
        assert client.post(PATH,data={'action':'preferences','account_id':aid,'overlap_days':7,'csrf_token':token}).status_code==302
        prefs=db.session.query(ChaseLocalPreferences).one();assert prefs.account_ids==[aid]
        data={'action':'update','csrf_token':token}
        for _ in range(2):assert client.post(PATH,data=data).status_code==302
        run=db.session.query(ChaseBrowserRuns).one();assert launches==[run.id]
        state=local.checkpoint(run);assert state['incremental'] and state['jobs'][0]['account_id']==aid
        assert state['jobs'][0]['start']=='2026-09-01' and state['jobs'][0]['end']==date.today().isoformat()
        run.status='needs_attention';run.error_code='authentication_required';db.session.commit()
        assert client.post(PATH,data=data).status_code==302
        db.session.refresh(run);assert run.status=='queued' and launches==[run.id,run.id]
        assert local.checkpoint(run)['jobs']==state['jobs']
        # Settings cannot silently change already approved, resumed work.
        assert client.post(PATH,data={'action':'preferences','account_id':aid,'overlap_days':1,'csrf_token':token}).status_code==302
        assert local.checkpoint(run)['jobs']==state['jobs']
        run.status='completed';run.diagnostics=json.dumps(dict(state,jobs=[],done=[]));db.session.commit()
        assert client.post(PATH,data=data).status_code==302
        assert db.session.query(ChaseBrowserRuns).count()==2
        newer=db.session.query(ChaseBrowserRuns).order_by(ChaseBrowserRuns.id.desc()).first()
        assert local.checkpoint(newer)['jobs'][0]['start']=='2026-09-03'
        for invalid in ('0','91','no'):
            with pytest.raises(LocalChaseError,match='invalid_preferences'):local.save_preferences(prefs.user_id,[aid],invalid)
    finally:app.config['WTF_CSRF_ENABLED']=old


def test_concurrent_update_clicks_launch_one_worker(
        clean_chase_connector,clean_chase_feed,client,admin_user,app,monkeypatch):
    initial=import_chase_qfx(_qfx(uuid4().hex[:12]+'1234'),reviewed_usd=True)
    client.post('/login/',data=admin_user)
    user_id=db.session.query(Users).filter_by(email=admin_user['email']).one().id
    local.save_preferences(user_id,[initial['account_id']],7)
    launches=[];monkeypatch.setattr(local,'_launch_systemd',launches.append)
    barrier=Barrier(2)
    def click():
        with app.app_context():
            barrier.wait(timeout=10)
            try:return local.update(user_id).id
            finally:db.session.remove()
    # Two lightweight database requests inside one serial test session.
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(lambda _:click(),range(2)))
    assert ids[0]==ids[1] and launches==[ids[0]]


def test_outdated_extension_pauses_before_model_or_download(
        clean_chase_connector,clean_chase_feed,client,admin_user,monkeypatch):
    initial=import_chase_qfx(_qfx(uuid4().hex[:12]+'1234'),reviewed_usd=True)
    client.post('/login/',data=admin_user)
    user_id=db.session.query(Users).filter_by(email=admin_user['email']).one().id
    monkeypatch.setattr(local,'_launch_systemd',lambda _:None)
    local.save_preferences(user_id,[initial['account_id']],7)
    run=local.update(user_id)
    calls=[]
    def command(op,**kw):
        calls.append(op);return {'ready':True,'version':1}
    monkeypatch.setattr(local,'FirefoxBridge',lambda **kw:SimpleNamespace(command=command))
    def no_model():raise AssertionError('Old extensions must not load the model')
    monkeypatch.setattr(local,'LocalModel',no_model)
    local.run_local_worker(run.id)
    db.session.refresh(run)
    assert run.status=='needs_attention' and run.error_code=='extension_update_required'
    assert calls==['ping','release']


def test_import_commit_then_checkpoint_crash_replays_without_duplicate_transactions(
        clean_chase_feed,tmp_path):
    number=uuid4().hex[:12]+'1234';initial=import_chase_qfx(_qfx(number),reviewed_usd=True)
    new=_qfx(number).replace(b'expense-1',b'expense-2')
    path=tmp_path/'fixture.qfx';path.write_bytes(new)
    job={'last4':'1234','account_number':number,'account_id':initial['account_id'],'start':'2026-09-01','end':'2026-09-03'}
    bridge=SimpleNamespace(command=lambda op,**kw:{'state':'complete','path':str(path)} if op=='download' else observation())
    model=SimpleNamespace(propose=lambda *a:{'action':'download_qfx','target_id':55})
    def crash(_):raise RuntimeError('simulated process crash after database commit')
    state={'jobs':[deepcopy(job)],'done':[]}
    with pytest.raises(RuntimeError,match='simulated process crash'):
        controller.Collector(bridge,model,state,crash,lambda:None,lambda*a:None,sleeper=lambda _:None).collect()
    replay={'jobs':[deepcopy(job)],'done':[]}
    controller.Collector(bridge,model,replay,lambda _:None,lambda:None,lambda*a:None,sleeper=lambda _:None).collect()
    assert replay['done'][0]['duplicate'] is True
    assert db.session.execute(text('SELECT COUNT(*) FROM ofx.stmttrn WHERE acctfrom_id=:id'),{'id':initial['account_id']}).scalar()==3


def test_concurrent_overlapping_files_keep_each_fitid_once(clean_chase_feed,app):
    number=uuid4().hex[:12]+'1234';initial=import_chase_qfx(_qfx(number),reviewed_usd=True)
    new=_qfx(number).replace(b'expense-1',b'expense-2')
    barrier=Barrier(2)
    def upload(content):
        with app.app_context():
            barrier.wait(timeout=10)
            try:return import_chase_qfx(content,reviewed_usd=True)['duplicate']
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(upload,[new,new+b'\n']))
    assert outcomes==[False,False]
    assert db.session.execute(text('SELECT COUNT(*) FROM ofx.stmttrn WHERE acctfrom_id=:id'),{'id':initial['account_id']}).scalar()==3


def test_resume_on_later_day_refreshes_completed_accounts_and_retains_original_choices(
        clean_chase_connector, clean_chase_feed, client, admin_user, monkeypatch):
    first = import_chase_qfx(_qfx(uuid4().hex[:12]+'1234'), reviewed_usd=True)['account_id']
    second = import_chase_qfx(_qfx(uuid4().hex[:12]+'5678'), reviewed_usd=True)['account_id']
    client.post('/login/', data=admin_user)
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    monkeypatch.setattr(local, '_launch_systemd', lambda _: None)
    class Clock(date):
        @classmethod
        def today(cls): return cls(2026,9,24)
    monkeypatch.setattr(local, 'date', Clock)
    local.save_preferences(user.id, [first,second], 7)
    run = local.update(user.id)
    state = local.checkpoint(run)
    state['done'] = [dict(state['jobs'].pop(0), duplicate=False)]
    run.diagnostics = json.dumps(state); run.status = 'needs_attention'; db.session.commit()
    # Changing preferences cannot alter the accounts approved for a saved run.
    local.save_preferences(user.id, [second], 1)
    Clock.today = classmethod(lambda cls: cls(2026,9,26))
    local.update(user.id)
    state = local.checkpoint(run)
    assert {job['account_id'] for job in state['jobs']} == {first,second}
    assert all(job['end'] == '2026-09-26' for job in state['jobs'])
    assert state['overlap_days'] == 7 and state['target_date'] == '2026-09-26'
    assert len(state['done']) == 1


def test_planner_uses_verified_bounds_when_source_dates_are_missing(clean_chase_feed):
    result = import_chase_qfx(_qfx(uuid4().hex[:12]+'1234'), reviewed_usd=True)
    aid = result['account_id']
    source = db.session.query(OFXFileImports).filter_by(sha256=result['sha256']).one()
    db.session.add(ChaseExportCoverage(file_import_id=source.id, start=date(2026,8,31), end=date(2026,9,4)))
    source.coverage_start = source.coverage_end = None
    db.session.commit()
    jobs = local._update_jobs(local.approved_accounts([aid]), date(2026,9,6), 1)
    # Include the most recent known transaction so a quiet account yields QFX.
    assert [(job['start'],job['end']) for job in jobs] == [('2026-09-03','2026-09-06')]


def test_quiet_account_refresh_retains_gaps_and_deduplicates_known_activity(clean_chase_feed):
    content = _qfx(uuid4().hex[:12]+'1234')
    initial = import_chase_qfx(content, reviewed_usd=True)
    aid = initial['account_id']
    today = date(2026,9,26)
    ranges = [(date(2026,8,20),date(2026,8,22)), (date(2026,9,19),today)]
    assert local._balance_refresh_ranges(aid, ranges, today) == [
        ranges[0], (date(2026,9,3),today)]
    # A widened refresh may overlap a gap; merge it without dropping any day.
    assert local._balance_refresh_ranges(aid, [(date(2026,9,5),date(2026,9,7)), ranges[1]], today) == [
        (date(2026,9,3),today)]
    # A new source with the same transactions still captures the current balance.
    refreshed = content.replace(b'<DTASOF>20260903', b'<DTASOF>20260926')
    result = import_chase_qfx(refreshed, reviewed_usd=True, allowed_account_ids=[aid],
                             verified_interval=(date(2026,9,1),today))
    assert result['new_transactions'] == 0 and result['existing_transactions'] == 2
    assert result['summary']['balance_asof'].date() == today
    assert result['summary']['coverage_contiguous']


def test_resume_repairs_saved_quiet_interval_without_repeating_completed_work(
        clean_chase_connector, clean_chase_feed, client, admin_user, monkeypatch):
    aid = import_chase_qfx(_qfx(uuid4().hex[:12]+'1234'), reviewed_usd=True)['account_id']
    client.post('/login/', data=admin_user)
    user = db.session.query(Users).filter_by(email=admin_user['email']).one()
    monkeypatch.setattr(local, '_launch_systemd', lambda _: None)
    class Clock(date):
        @classmethod
        def today(cls): return cls(2026,9,26)
    monkeypatch.setattr(local, 'date', Clock)
    local.save_preferences(user.id, [aid], 7)
    run = local.update(user.id)
    state = local.checkpoint(run)
    completed = dict(state['jobs'][0], start='2026-09-01', end='2026-09-18', duplicate=True)
    state['done'] = [completed]
    state['jobs'][0]['start'] = '2026-09-19'
    run.diagnostics = json.dumps(state); run.status = 'needs_attention'; db.session.commit()
    local.update(user.id)
    state = local.checkpoint(run)
    assert state['done'] == [completed]
    assert [(job['start'],job['end']) for job in state['jobs']] == [('2026-09-03','2026-09-26')]
    assert state['jobs'][0]['account_id'] == aid
