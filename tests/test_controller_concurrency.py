import json
import threading
from dataclasses import replace

import pytest

from fanuc_workbench.bridge.config import BridgeConfig, RobotAccess
from fanuc_workbench.bridge.requests import RequestCancelled
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.core.models import RobotProfile
from test_native_workflow import FakeNative
from test_robot_bridge import MemoryRobot, ORIGINAL, PROPOSED, digest


@pytest.fixture
def controllers(tmp_path, monkeypatch):
    robots = {name: MemoryRobot() for name in ('r1','r2')}
    profiles = {name: RobotProfile(name, '192.0.2.'+str(index))
                for index,name in enumerate(robots,1)}
    access = {name: RobotAccess(profiles[name], writable_files=('md:MAIN.LS',),
        comment_service_url='http://'+profiles[name].host, comment_targets=('R[1]',),
        native_provider='frrobot', native_register_targets=('R[999]',)) for name in robots}
    source=tmp_path/'edits';source.mkdir();(source/'MAIN.LS').write_bytes(PROPOSED)
    for robot in robots.values():
        robot.files['md:NUMREG.VA']=b"[1] = 42 'Count'\r\n"
    by_host={profiles[name].host:robot for name,robot in robots.items()}
    bridge=RobotBridge(BridgeConfig(tmp_path/'state',source,access,allow_writes=True),lambda profile:by_host[profile.host])
    apis={name:FakeNative(robot) for name,robot in robots.items()}
    bridge.native.factory=lambda profile:apis[profile.name]
    comments=[]
    def setter(url,record,comment,**policy):
        comments.append(url)
        by_host[url.removeprefix('http://')].files['md:NUMREG.VA']=f"[1] = 42 '{comment}'\r\n".encode()
    monkeypatch.setattr('fanuc_workbench.bridge.comments.set_live_comment',setter)
    try:
        yield bridge,robots,apis,comments
    finally:
        bridge.close()


def prepare(bridge,robot_id,kind):
    if kind=='file':
        return bridge.prepare_upload(robot_id,'md:MAIN.LS','MAIN.LS',digest(ORIGINAL))
    if kind=='comment':
        captured=bridge.read_file(robot_id,'md:NUMREG.VA')
        return bridge.comments.prepare(robot_id,'md:NUMREG.VA','R[1]','New count',captured['sha256'])
    captured=bridge.native.read_register(robot_id,'R[999]')
    return bridge.native.prepare_register(robot_id,'R[999]',{'type':'integer','value':1},captured['sha256'])


def gate_writes(kind,robots,apis,monkeypatch,gate):
    if kind=='file':
        for robot in robots.values():
            original=robot.upload
            def upload(local,remote,original=original):
                gate();return original(local,remote)
            monkeypatch.setattr(robot,'upload',upload)
    elif kind=='native':
        for api in apis.values():
            original=api.call
            def call(operation,original=original,**arguments):
                if operation=='write_register':gate()
                return original(operation,**arguments)
            monkeypatch.setattr(api,'call',call)
    else:
        from fanuc_workbench.bridge import comments
        original=comments.set_live_comment
        def setter(*args,**kwargs):
            gate();return original(*args,**kwargs)
        monkeypatch.setattr(comments,'set_live_comment',setter)


def write_count(kind,robots,apis,comments):
    if kind=='file':return sum(robot.uploads for robot in robots.values())
    if kind=='native':return sum(api.writes for api in apis.values())
    return len(comments)


def test_other_controller_reads_complete_while_same_controller_requests_wait(controllers,monkeypatch):
    bridge,robots,_,_=controllers
    entered,release,waiting=threading.Event(),threading.Event(),threading.Event()
    original=robots['r1'].download_bounded
    count=0
    def blocked(*args):
        nonlocal count
        count+=1;entered.set();assert release.wait(5);return original(*args)
    monkeypatch.setattr(robots['r1'],'download_bounded',blocked)
    def second():
        waiting.set();return bridge.read_file('r1','md:MAIN.LS')
    first=bridge.requests.submit(bridge.read_file,'r1','md:MAIN.LS')
    try:
        assert entered.wait(2)
        same=bridge.requests.submit(second)
        assert waiting.wait(2)
        other=bridge.requests.submit(bridge.read_file,'r2','md:MAIN.LS')
        assert other.result(timeout=2)['sha256']==digest(ORIGINAL)
        assert count==1 and not same.done()
    finally:
        release.set()
    assert first.result(timeout=3)['sha256']==same.result(timeout=3)['sha256']
    assert count==2


@pytest.mark.parametrize('kind',['file','comment','native'])
def test_distinct_controller_commits_reach_vendor_writes_concurrently(controllers,monkeypatch,kind):
    bridge,robots,apis,comments=controllers
    plans=[prepare(bridge,name,kind) for name in robots]
    barrier=threading.Barrier(2)
    gate_writes(kind,robots,apis,monkeypatch,lambda:barrier.wait(timeout=3))
    futures=[bridge.requests.submit(bridge.commit_change,plan['plan_id'],plan['proposed_sha256']) for plan in plans]
    assert all(future.result(timeout=5)['status']=='verified' for future in futures)
    assert write_count(kind,robots,apis,comments)==2


@pytest.mark.parametrize('kind',['file','comment','native'])
def test_overlapping_commits_cannot_use_the_same_plan_twice(controllers,kind):
    bridge,robots,apis,comments=controllers
    plan=prepare(bridge,'r1',kind)
    barrier=threading.Barrier(3)
    def commit():
        barrier.wait(timeout=3)
        return bridge.commit_change(plan['plan_id'],plan['proposed_sha256'])
    futures=[bridge.requests.submit(commit) for _ in range(2)]
    barrier.wait(timeout=3)
    results=[]
    for future in futures:
        try:results.append(future.result(timeout=5))
        except ValueError:pass
    assert len(results)==1 and results[0]['status']=='verified'
    assert write_count(kind,robots,apis,comments)==1


@pytest.mark.parametrize('kind',['file','comment','native'])
def test_client_cancellation_during_write_keeps_readback_and_does_not_retry(controllers,monkeypatch,kind):
    bridge,robots,apis,comments=controllers
    plan=prepare(bridge,'r1',kind)
    entered,release=threading.Event(),threading.Event()
    def gate():
        entered.set();assert release.wait(5)
    gate_writes(kind,robots,apis,monkeypatch,gate)
    future=bridge.requests.submit(bridge.commit_change,plan['plan_id'],plan['proposed_sha256'])
    try:
        assert entered.wait(2)
        assert not future.cancel()
    finally:
        release.set()
    assert future.result(timeout=5)['status']=='verified'
    assert write_count(kind,robots,apis,comments)==1
    records=[json.loads(line) for line in (bridge.config.state_dir/'audit.jsonl').read_text().splitlines()]
    assert any(row.get('request_id')==future.request_id and row['action'].endswith('verified') for row in records)


def test_plan_cancellation_can_win_during_native_preflight_without_a_write(controllers,monkeypatch):
    bridge,_,apis,_=controllers
    plan=prepare(bridge,'r1','native')
    entered,release=threading.Event(),threading.Event()
    original=apis['r1'].call
    def blocked(operation,**kwargs):
        if operation=='read_register':entered.set();assert release.wait(5)
        return original(operation,**kwargs)
    monkeypatch.setattr(apis['r1'],'call',blocked)
    future=bridge.requests.submit(bridge.commit_change,plan['plan_id'],plan['proposed_sha256'])
    try:
        assert entered.wait(2)
        cancelled=bridge.requests.submit(bridge.cancel_change,plan['plan_id'],control=True).result(timeout=1)
        assert cancelled['status']=='cancelled'
    finally:
        release.set()
    with pytest.raises(ValueError,match='Unknown'):
        future.result(timeout=3)
    assert not apis['r1'].writes


def test_cancelled_request_waiting_for_an_alias_does_not_access_the_controller(controllers):
    bridge,robots,_,_=controllers
    bridge.config=replace(bridge.config,robots={**bridge.config.robots,'alias':bridge.config.robots['r1']})
    entered=threading.Event()
    def read():
        entered.set();return bridge.read_file('alias','md:MAIN.LS')
    with bridge._robot_lock('r1'):
        before=robots['r1'].connects
        future=bridge.requests.submit(read)
        assert entered.wait(2)
        future.cancel()
        with pytest.raises(RequestCancelled):future.result(timeout=2)
        assert robots['r1'].connects==before


def test_native_provider_creation_is_parallel_for_distinct_controllers(controllers):
    bridge,_,apis,_=controllers
    barrier=threading.Barrier(2)
    def factory(profile):
        barrier.wait(timeout=3);return apis[profile.name]
    bridge.native.factory=factory
    futures=[bridge.requests.submit(bridge.native.status,name) for name in ('r1','r2')]
    assert {future.result(timeout=5)['robot_id'] for future in futures}=={'r1','r2'}
