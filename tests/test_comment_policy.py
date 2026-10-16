import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

import pytest

from test_bridge_operations import bridge_case
from test_robot_bridge import digest
from test_live_comments import record
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.services.live_comments import validate_live_comment,build_comset_url,target_for_record,set_live_comment


@pytest.mark.parametrize('text',['x'*17,'Café','control\x7f','line\nfeed'])
def test_default_comment_policy_refuses_before_network(text):
    with pytest.raises(ValueError): validate_live_comment(text)


def test_comment_policy_is_byte_limited_and_encoding_explicit():
    target=target_for_record(record('Numeric Register','R[999]',index=999))
    latin=build_comset_url('127.0.0.5:3080',target,'Café',encoding='latin-1',max_bytes=4)
    assert 'Caf%E9' in latin
    with pytest.raises(ValueError,match='4-byte'):
        build_comset_url('127.0.0.5:3080',target,'Café',encoding='utf-8',max_bytes=4)
    validate_live_comment('',max_bytes=16)


def test_shared_comment_request_is_direct_and_has_no_value_parameter(monkeypatch):
    import urllib.request
    calls=[]
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): pass
    class Opener:
        def open(self,request,timeout):
            calls.append(request.full_url)
            return Response()
    def build(*handlers):
        proxy=next(h for h in handlers if isinstance(h,urllib.request.ProxyHandler))
        assert proxy.proxies=={}
        return Opener()
    monkeypatch.setattr('fanuc_workbench.services.live_comments.build_opener',build)
    set_live_comment('http://127.0.0.5:3080',record('Numeric Register','R[999]',index=999),'FWB TCP TEST')
    assert parse_qs(urlsplit(calls[0]).query)=={'sComment':['FWB TCP TEST'],'sIndx':['999'],'sFc':['1']}


def test_comment_length_is_checked_before_controller_access(bridge_case):
    bridge,robot,config=bridge_case
    access=replace(config.robots['r1'],comment_service_url='http://192.0.2.1',comment_targets=('R[1]',))
    with RobotBridge(replace(config,robots={'r1':access}),lambda _:robot) as guarded:
        with pytest.raises(ValueError,match='16-byte'):
            guarded.comments.prepare('r1','md:NUMREG.VA','R[1]','x'*17,digest(robot.files['md:NUMREG.VA']))
        assert robot.connects==0


def test_prepared_comment_binds_service_and_encoding_policy(bridge_case,monkeypatch):
    bridge,robot,config=bridge_case
    access=replace(config.robots['r1'],comment_service_url='http://192.0.2.1:3080',comment_targets=('R[1]',))
    calls=[]
    monkeypatch.setattr('fanuc_workbench.bridge.comments.set_live_comment',lambda *a,**k:calls.append(a))
    with RobotBridge(replace(config,robots={'r1':access}),lambda _:robot) as guarded:
        plan=guarded.comments.prepare('r1','md:NUMREG.VA','R[1]','New count',digest(robot.files['md:NUMREG.VA']))
        assert plan['encoding']=='ascii' and plan['max_bytes']==16
        review=guarded.comments.review(plan['plan_id'],plan['proposed_sha256'])
        assert review['service_url']=='http://192.0.2.1:3080'
        guarded.config=replace(config,robots={'r1':replace(access,comment_encoding='utf-8')})
        with pytest.raises(ValueError,match='policy changed'):
            guarded.comments.commit(plan['plan_id'],plan['proposed_sha256'])
        assert not calls


def test_comment_readback_reports_observed_value_drift(bridge_case,monkeypatch):
    bridge,robot,config=bridge_case
    access=replace(config.robots['r1'],comment_service_url='http://192.0.2.1',comment_targets=('R[1]',))
    def update(*args,**kwargs): robot.files['md:NUMREG.VA']=b"[1] = 43 'New count'\r\n"
    monkeypatch.setattr('fanuc_workbench.bridge.comments.set_live_comment',update)
    with RobotBridge(replace(config,robots={'r1':access}),lambda _:robot) as guarded:
        plan=guarded.comments.prepare('r1','md:NUMREG.VA','R[1]','New count',digest(robot.files['md:NUMREG.VA']))
        result=guarded.comments.commit(plan['plan_id'],plan['proposed_sha256'])
        assert result['value_write_requested'] is False
        assert result['observed_display_value_before']=='42' and result['observed_display_value_after']=='43'
        assert result['observed_display_value_unchanged'] is False and result['readback_is_atomic'] is False


def test_unrelated_live_values_can_change_without_invalidating_description_review(bridge_case,monkeypatch):
    bridge,robot,config=bridge_case
    access=replace(config.robots['r1'],comment_service_url='http://192.0.2.1',comment_targets=('R[1]',))
    def update(*args,**kwargs): robot.files['md:NUMREG.VA']=b"[1] = 43 'New count'\r\n[2] = 3 'Counter'\r\n"
    monkeypatch.setattr('fanuc_workbench.bridge.comments.set_live_comment',update)
    with RobotBridge(replace(config,robots={'r1':access}),lambda _:robot) as guarded:
        reviewed=guarded.read_file('r1','md:NUMREG.VA')
        robot.files['md:NUMREG.VA']+=b"[2] = 1 'Counter'\r\n"
        plan=guarded.comments.prepare('r1','md:NUMREG.VA','R[1]','New count',reviewed['sha256'])
        assert plan['preparation_listing_sha256']!=reviewed['sha256']
        assert plan['stale_check_scope']=='target identity and description'
        assert (Path(plan['backup_path']).parent/'comment_plan.json').is_file()
        robot.files['md:NUMREG.VA']=b"[1] = 43 'Count'\r\n[2] = 2 'Counter'\r\n"
        result=guarded.comments.commit(plan['plan_id'],plan['proposed_sha256'])
        assert result['status']=='verified' and result['observed_display_value_unchanged']


@pytest.mark.parametrize('tamper',['target_comment','reference_bytes','unknown_reference'])
def test_dynamic_listing_reference_cannot_authorize_an_unreviewed_target(bridge_case,tamper):
    bridge,robot,config=bridge_case
    access=replace(config.robots['r1'],comment_service_url='http://192.0.2.1',comment_targets=('R[1]',))
    with RobotBridge(replace(config,robots={'r1':access}),lambda _:robot) as guarded:
        reviewed=guarded.read_file('r1','md:NUMREG.VA')
        robot.files['md:NUMREG.VA']+=b"[2] = 1 'Counter'\r\n"
        expected=reviewed['sha256']
        if tamper=='target_comment':
            robot.files['md:NUMREG.VA']=b"[1] = 42 'Changed by someone'\r\n"
        elif tamper=='reference_bytes':
            Path(reviewed['local_path']).write_bytes(b'edited preserved reference')
        else:
            expected='f'*64
        with pytest.raises(ValueError):
            guarded.comments.prepare('r1','md:NUMREG.VA','R[1]','New count',expected)
        assert not guarded.comments.plans


@pytest.mark.parametrize('options',[{'comment_max_bytes':0},{'comment_max_bytes':True},
    {'comment_encoding':'guess'},{'comment_service_url':'http://192.0.2.1:70000'}])
def test_invalid_comment_policy_is_rejected_at_configuration_load(tmp_path,options):
    path=tmp_path/'config.json'
    path.write_text(json.dumps({'state_dir':'state','source_dir':'edits','robots':{
        'r1':{'profile':{'host':'192.0.2.1'},**options}}}))
    with pytest.raises(ValueError): BridgeConfig.load(path)
