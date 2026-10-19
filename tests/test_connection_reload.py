from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from test_bridge_operations import finished
from test_robot_bridge import MemoryRobot, ORIGINAL, PROPOSED, digest
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge


@pytest.fixture
def reload_case(tmp_path):
    document = {'state_dir': 'state', 'source_dir': 'edits', 'allow_writes': True,
        'approval_mode': 'elicitation', 'robots': {'r1': {'profile': {'name': 'Test', 'host': '192.0.2.1'},
        'writable_files': ['md:MAIN.LS'], 'comment_service_url': 'http://192.0.2.1:3080',
        'comment_targets': ['R[1]']}}}
    path = tmp_path / 'bridge.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    (tmp_path / 'edits').mkdir()
    (tmp_path / 'edits/MAIN.LS').write_bytes(PROPOSED)
    robot = MemoryRobot()
    robot.files['md:NUMREG.VA'] = b"[1] = 0 'Old'\r\n"
    contacted = []
    def backend(profile):
        contacted.append(profile.host)
        return robot
    with RobotBridge.from_config(path, backend) as bridge:
        yield bridge, robot, path, document, contacted


def change_endpoint(path, original):
    updated = copy.deepcopy(original)
    updated['robots']['r1']['profile'].update(host='192.0.2.2', port=2121, timeout=5)
    updated['robots']['r1']['comment_service_url'] = 'http://192.0.2.2:4080'
    path.write_text(json.dumps(updated), encoding='utf-8')
    return updated


@pytest.mark.parametrize('field,value',[
    ('max_concurrent_requests',2),('max_pending_requests',3),('robot_lock_timeout_seconds',.5),
    ('write_scope','all_supported'),
])
def test_capacity_changes_require_host_reconnect(reload_case,field,value):
    bridge,_,path,document,contacted=reload_case
    original=bridge.config
    document[field]=value;path.write_text(json.dumps(document))
    with pytest.raises(ValueError,match='restart'):
        bridge.reload_connections()
    assert bridge.config is original and not contacted


def test_refresh_uses_only_bound_file_and_invalidates_old_target_plans(reload_case):
    bridge, robot, path, document, contacted = reload_case
    upload = bridge.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', digest(ORIGINAL))
    capture = bridge.read_file('r1', 'md:NUMREG.VA')
    comment = bridge.comments.prepare('r1', 'md:NUMREG.VA', 'R[1]', 'Reviewed', capture['sha256'])
    connections_before = robot.connects
    change_endpoint(path, document)
    assert bridge.list_robots()['robots'][0]['host'] == '192.0.2.1'
    result = bridge.reload_connections()
    assert result['status'] == 'reloaded' and result['changed_robots'] == ['r1']
    assert result['invalidated_upload_plans'] == result['invalidated_comment_plans'] == 1
    assert robot.connects == connections_before and robot.uploads == 0
    assert not result['controller_contacted'] and result['requires_fresh_reads']
    assert bridge.config.robots['r1'].profile.port == 2121
    for plan in [upload, comment]:
        with pytest.raises(ValueError):
            bridge.commit_upload(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    with pytest.raises(ValueError):
        bridge.comments.commit(comment['plan_id'], comment['proposed_sha256'], client_approved=True)
    with pytest.raises(ValueError, match='intact preserved read'):
        bridge.reference_capture('r1', 'md:NUMREG.VA', capture['sha256'])
    assert Path(capture['local_path']).read_bytes() == robot.files['md:NUMREG.VA']
    assert bridge.read_file('r1', 'md:MAIN.LS')['sha256'] == digest(ORIGINAL)
    assert contacted[-1] == '192.0.2.2'
    events = [json.loads(line) for line in (bridge.config.state_dir / 'audit.jsonl').read_text().splitlines()]
    event = next(row for row in events if row['action'] == 'connections_reloaded')
    assert event['connections'][0]['before']['host'] == '192.0.2.1'
    assert event['connections'][0]['after']['host'] == '192.0.2.2'
    assert 'password' not in json.dumps(event)


def test_refresh_audit_failure_leaves_old_configuration_and_plans(reload_case, monkeypatch):
    bridge, robot, path, document, _ = reload_case
    plan = bridge.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', digest(ORIGINAL))
    change_endpoint(path, document)
    def fail(*args, **kwargs):
        raise OSError('Audit disk unavailable')
    monkeypatch.setattr(bridge, '_audit', fail)
    with pytest.raises(OSError):
        bridge.reload_connections()
    assert bridge.config.robots['r1'].profile.host == '192.0.2.1'
    assert plan['plan_id'] in bridge._plans and robot.uploads == 0


def test_failed_lock_acquisition_does_not_leave_refresh_busy(reload_case):
    bridge, robot, path, document, _ = reload_case
    from fanuc_workbench.bridge.storage import RobotLocks, RobotBusyError
    other = RobotLocks(bridge.config.state_dir / 'robot-locks')
    with other.acquire('192.0.2.1'):
        with pytest.raises(RobotBusyError):
            bridge.read_file('r1', 'md:MAIN.LS')
    change_endpoint(path, document)
    assert bridge.reload_connections()['status'] == 'reloaded'
    assert robot.connects == robot.uploads == 0


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(allow_writes=False),
    lambda d: d.update(approval_mode='chat'),
    lambda d: d.update(source_dir='other'),
    lambda d: d.update(state_dir='other'),
    lambda d: d.update(max_file_bytes=1024),
    lambda d: d['robots']['r1'].update(writable_files=['md:OTHER.LS']),
    lambda d: d['robots']['r1'].update(comment_targets=['R[2]']),
    lambda d: d['robots']['r1'].update(comment_max_bytes=32),
    lambda d: d['robots']['r1'].update(password_env='OTHER_PASSWORD'),
    lambda d: d['robots']['r1']['profile'].update(username='other'),
    lambda d: d['robots']['r1']['profile'].update(roots=['md:', 'ud1:']),
    lambda d: d['robots']['r1']['profile'].update(transport='sftp'),
    lambda d: d['robots']['r1'].update(comment_service_url='https://192.0.2.1:3080'),
    lambda d: d['robots']['r1'].update(comment_service_url=None),
    lambda d: d['robots'].update(r2=copy.deepcopy(d['robots']['r1'])),
    lambda d: d['robots'].pop('r1'),
])
def test_refresh_refuses_policy_changes_and_preserves_pending_plans(reload_case, mutation):
    bridge, robot, path, document, _ = reload_case
    upload = bridge.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', digest(ORIGINAL))
    before, count = bridge.config, robot.connects
    mutation(document)
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='cannot change'):
        bridge.reload_connections()
    assert bridge.config is before and upload['plan_id'] in bridge._plans
    assert robot.connects == count and robot.uploads == 0


def test_unchanged_and_invalid_file_do_not_consume_plan(reload_case):
    bridge, robot, path, _, _ = reload_case
    upload = bridge.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', digest(ORIGINAL))
    assert bridge.reload_connections()['status'] == 'unchanged'
    for invalid in ('{', '{}', 'null'):
        path.write_text(invalid, encoding='utf-8')
        with pytest.raises(ValueError, match='could not be validated'):
            bridge.reload_connections()
        assert upload['plan_id'] in bridge._plans
        assert bridge.config.robots['r1'].profile.host == '192.0.2.1'
    path.unlink()
    with pytest.raises(ValueError, match='could not be validated'):
        bridge.reload_connections()
    assert robot.uploads == 0


def test_reload_refuses_active_robot_operation_and_job(reload_case):
    bridge, robot, path, document, _ = reload_case
    change_endpoint(path, document)
    entered, release = threading.Event(), threading.Event()
    def operation():
        with bridge._robot_lock('r1'):
            entered.set()
            assert release.wait(5)
    worker = threading.Thread(target=operation)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ValueError, match='operation is active'):
            bridge.reload_connections()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    entered.clear()
    release.clear()
    def work(context):
        entered.set()
        assert release.wait(5)
        return {}
    job = bridge.operations.jobs.start('test', 'r1', work)
    try:
        assert entered.wait(5)
        with pytest.raises(ValueError, match='Read jobs are active'):
            bridge.reload_connections()
    finally:
        release.set()
    assert finished(bridge.operations.jobs, job['job_id'])['status'] == 'complete'
    assert bridge.reload_connections()['status'] == 'reloaded'
    assert robot.connects == robot.uploads == 0


def test_unbound_python_bridge_does_not_guess_config_path(tmp_path):
    with RobotBridge(BridgeConfig(tmp_path / 'state', tmp_path / 'source')) as bridge:
        assert not bridge.operations.capabilities()['connection_refresh']['available']
        with pytest.raises(ValueError, match='No configuration file'):
            bridge.reload_connections()


def test_real_mcp_refresh_does_not_need_server_restart(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    path = tmp_path / 'bridge.json'
    document = {'state_dir': 'state', 'source_dir': 'edits', 'robots': {
        'r1': {'profile': {'name': 'Test', 'host': '192.0.2.1'}}}}
    path.write_text(json.dumps(document), encoding='utf-8')
    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(path)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            async def call(name):
                result = await client.call_tool(name, {})
                assert not result.isError, result.content
                return json.loads(result.content[0].text)
            assert (await call('list_robots'))['robots'][0]['host'] == '192.0.2.1'
            document['robots']['r1']['profile']['host'] = '192.0.2.2'
            path.write_text(json.dumps(document), encoding='utf-8')
            refreshed = await call('reload_connections')
            assert refreshed['status'] == 'reloaded' and not refreshed['controller_contacted']
            assert (await call('list_robots'))['robots'][0]['host'] == '192.0.2.2'
    asyncio.run(scenario())
