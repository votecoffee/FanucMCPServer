from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from test_bridge_operations import bridge_case, imported
from test_connection_reload import reload_case, change_endpoint
from test_extension_runtime import release
from test_robot_bridge import ORIGINAL, PROPOSED, digest
from fanuc_workbench.bridge.extension_release import install_release


def stage(bridge, tmp_path, **kwargs):
    source = tmp_path / ('feature-' + kwargs.get('version', '1.0.0'))
    release(source, **kwargs)
    return install_release(bridge.extensions.root, source)


def descriptor(bridge, name='demo.read'):
    return bridge.extensions.describe(name)


def read(bridge, value='example', *, revision=None):
    return bridge.extensions.run_read('demo.read', {'value': value}, revision or descriptor(bridge)['revision'])['result']


def test_builtin_catalog_and_legacy_analysis_share_verified_context(bridge_case):
    bridge, robot, config = bridge_case
    assert not bridge.extensions.status()['loaded']
    rows = bridge.extensions.list()
    assert rows['total'] == 35 and len(rows['extensions']) == 1
    identifier = imported(bridge, robot, config)
    operation = descriptor(bridge, 'workbench.analysis.health')
    assert bridge.extensions.run_read(operation['id'], {'artifact_id': identifier}, operation['revision'])['result'] == bridge.operations.analyze(identifier, 'health')
    with pytest.raises(ValueError):
        bridge.extensions.run_read(operation['id'], {'artifact_id': '../outside'}, operation['revision'])
    (bridge.operations.store.root(identifier) / 'MAIN.LS').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='changed'):
        bridge.extensions.run_read(operation['id'], {'artifact_id': identifier}, operation['revision'])
    assert robot.connects == robot.uploads == 0


def test_reload_switches_operations_refuses_stale_revisions_and_noop_preserves_pid(bridge_case, tmp_path):
    bridge, robot, _ = bridge_case
    stage(bridge, tmp_path)
    first = bridge.extensions.reload()
    revision = descriptor(bridge)['revision']
    assert read(bridge)['value'] == 'example'
    bridge._plans['sentinel'] = object()
    assert bridge.extensions.reload()['status'] == 'unchanged'
    assert bridge.extensions.status()['extensions'][0]['worker_pid'] == first['extensions'][0]['worker_pid']
    assert 'sentinel' in bridge._plans
    stage(bridge, tmp_path, version='1.1.0', code="def handle(operation_id, arguments, context):\n    return {'value': 'updated'}\n")
    result = bridge.extensions.reload()
    assert result['status'] == 'reloaded' and result['invalidated_upload_plans'] == 1
    assert not result['mcp_tools_changed'] and not result['controller_contacted']
    assert read(bridge)['value'] == 'updated'
    with pytest.raises(ValueError, match='revision changed'):
        read(bridge, revision=revision)
    assert robot.connects == robot.uploads == 0


@pytest.mark.parametrize('failure', ['hash', 'self_test', 'audit', 'registry_race'])
def test_failed_candidate_preserves_active_worker_and_both_plan_types(bridge_case, tmp_path, monkeypatch, failure):
    bridge, robot, _ = bridge_case
    stage(bridge, tmp_path)
    first = bridge.extensions.reload()
    bridge._plans['file'] = object()
    bridge.comments.plans['comment'] = object()
    code = "def self_test(): return False\ndef handle(*args): return {}\n" if failure == 'self_test' else None
    stage(bridge, tmp_path, version='1.1.0', code=code)
    if failure == 'hash':
        (bridge.extensions.root / 'releases/demo/1.1.0/feature.py').write_text('tampered')
    elif failure == 'audit':
        original = bridge._audit
        def audit(action, **fields):
            if action == 'extensions_reloaded':
                raise OSError('private audit failure')
            return original(action, **fields)
        monkeypatch.setattr(bridge, '_audit', audit)
    elif failure == 'registry_race':
        from fanuc_workbench.bridge import extension_host
        original = extension_host.FeatureWorker
        def changed(*args, **kwargs):
            worker = original(*args, **kwargs)
            (bridge.extensions.root / 'registry.json').write_text('{"schema_version":1,"extensions":[]}')
            return worker
        monkeypatch.setattr(extension_host, 'FeatureWorker', changed)
    with pytest.raises(ValueError, match='preserved'):
        bridge.extensions.reload()
    assert bridge.extensions.status()['extensions'][0]['worker_pid'] == first['extensions'][0]['worker_pid']
    assert read(bridge)['value'] == 'example'
    assert 'file' in bridge._plans and 'comment' in bridge.comments.plans
    assert robot.uploads == 0


@pytest.mark.parametrize('active', ['robot', 'job'])
def test_changed_reload_refuses_active_core_work(bridge_case, tmp_path, active):
    bridge, _, _ = bridge_case
    stage(bridge, tmp_path)
    bridge.extensions.reload()
    stage(bridge, tmp_path, version='1.1.0')
    if active == 'robot':
        bridge._active_robot_operations = 1
    else:
        bridge.operations.jobs.events['queued'] = threading.Event()
    try:
        with pytest.raises(ValueError, match='preserved'):
            bridge.extensions.reload()
        assert descriptor(bridge)['revision'].startswith('demo@1.0.0:')
    finally:
        bridge._active_robot_operations = 0
        bridge.operations.jobs.events.clear()


def test_inflight_read_finishes_on_retired_worker(bridge_case, tmp_path):
    bridge, _, _ = bridge_case
    marker, proceed = tmp_path / 'started', tmp_path / 'proceed'
    code = f"""from pathlib import Path
import time
def handle(operation_id, arguments, context):
    Path({str(marker)!r}).write_text('started')
    while not Path({str(proceed)!r}).exists(): time.sleep(.01)
    return {{'value': 'old'}}
"""
    stage(bridge, tmp_path, code=code)
    bridge.extensions.reload()
    old = bridge.extensions.current.workers['demo']
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(read, bridge)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        try:
            assert marker.exists()
            stage(bridge, tmp_path, version='1.1.0')
            bridge.extensions.reload()
            assert read(bridge)['value'] == 'example'
            assert bridge.extensions.status()['retiring_generations'] == 1 and old.healthy
        finally:
            proceed.write_text('continue')
        assert pending.result(timeout=10)['value'] == 'old'
    assert not old.healthy and not bridge.extensions.retired


def test_dead_worker_requires_explicit_reload_and_recovers_same_release(bridge_case, tmp_path):
    bridge, _, _ = bridge_case
    stage(bridge, tmp_path, code="import os\ndef handle(operation_id, arguments, context):\n    if arguments['value'] == 'crash': os._exit(2)\n    return {'value': 'recovered'}\n")
    bridge.extensions.reload()
    pid = bridge.extensions.current.workers['demo'].pid
    with pytest.raises(RuntimeError):
        read(bridge, 'crash')
    with pytest.raises(RuntimeError, match='reload'):
        read(bridge)
    assert bridge.extensions.reload()['status'] == 'reloaded'
    assert bridge.extensions.current.workers['demo'].pid != pid
    assert read(bridge)['value'] == 'recovered'


def prepare_descriptor():
    return {'id': 'demo.propose', 'description': 'Compute an application file proposal', 'effect': 'prepare',
        'proposal_kind': 'file_replace', 'input_schema': {'type': 'object', 'properties': {
            name: {'type': 'string'} for name in ['robot_id', 'path', 'expected_sha256']},
            'required': ['robot_id', 'path', 'expected_sha256'], 'additionalProperties': False},
        'output_schema': {'type': 'object'}}


def proposal_code(extra=''):
    return f"""import base64,hashlib
from pathlib import Path
def handle(operation_id, arguments, context):
    original = Path(context['original']['file']).read_bytes()
    assert hashlib.sha256(original).hexdigest() == context['original']['sha256']
    data = original.replace(b'Original', b'Reviewed')
    return {{'data_base64': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest(){extra}}}
"""


def test_extension_proposal_uses_frozen_core_plan_and_client_confirmation(reload_case, tmp_path):
    bridge, robot, _, _, _ = reload_case
    stage(bridge, tmp_path, code=proposal_code(), operations=[prepare_descriptor()])
    op = descriptor(bridge, 'demo.propose')
    capture = bridge.read_file('r1', 'md:MAIN.LS')
    args = {'robot_id': 'r1', 'path': 'md:MAIN.LS', 'expected_sha256': capture['sha256']}
    with pytest.raises(ValueError, match='prepare_change'):
        bridge.extensions.run_read(op['id'], args, op['revision'])
    plan = bridge.extensions.prepare(op['id'], args, op['revision'])
    assert robot.uploads == 0 and plan['proposed_sha256'] == digest(PROPOSED)
    with pytest.raises(ValueError, match='elicitation'):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'])
    # Approval commits frozen core bytes; no feature callback is used.
    bridge.extensions.current.workers['demo'].close()
    result = bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert result['status'] == 'verified' and robot.files['md:MAIN.LS'] == PROPOSED


@pytest.mark.parametrize('bad', ['target', 'disabled', 'missing_capture', 'extra_output'])
def test_extension_proposals_cannot_bypass_core_policy(reload_case, tmp_path, bad):
    bridge, robot, _, _, _ = reload_case
    stage(bridge, tmp_path, code=proposal_code(", 'path': 'md:OTHER.LS'" if bad == 'extra_output' else ''), operations=[prepare_descriptor()])
    op = descriptor(bridge, 'demo.propose')
    capture = bridge.read_file('r1', 'md:MAIN.LS')
    args = {'robot_id': 'r1', 'path': 'md:MAIN.LS', 'expected_sha256': capture['sha256']}
    if bad == 'target':
        args['path'] = 'md:OTHER.LS'
    elif bad == 'disabled':
        bridge.config = replace(bridge.config, allow_writes=False)
    elif bad == 'missing_capture':
        args['expected_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        bridge.extensions.prepare(op['id'], args, op['revision'])
    assert not bridge._plans and robot.uploads == 0


def test_schemas_are_enforced_and_reads_cannot_prepare(bridge_case, tmp_path):
    bridge, _, _ = bridge_case
    stage(bridge, tmp_path)
    op = descriptor(bridge)
    with pytest.raises(ValueError):
        bridge.extensions.run_read(op['id'], {'value': 123}, op['revision'])
    with pytest.raises(ValueError, match='Read operations'):
        bridge.extensions.prepare(op['id'], {'value': 'x'}, op['revision'])
    with pytest.raises(ValueError, match='Core prepared'):
        bridge.extensions.run_read('core.files.replace', {}, 'core@1')
    changed = copy.deepcopy(op)
    changed = {key: value for key, value in changed.items() if key not in {'revision', 'extension_id'}}
    changed['output_schema'] = {'type': 'object', 'required': ['missing']}
    stage(bridge, tmp_path, version='1.1.0', operations=[changed])
    bridge.extensions.reload()
    with pytest.raises(ValueError, match='schema'):
        read(bridge)


def test_description_and_storage_creation_proposals_use_core_preparers(reload_case, tmp_path):
    bridge, robot, _, _, _ = reload_case
    comment = prepare_descriptor()
    comment.update(id='demo.comment', proposal_kind='comment_update')
    fields = ['robot_id', 'listing_path', 'target', 'expected_sha256']
    comment['input_schema'] = {'type': 'object', 'properties': {key: {'type': 'string'} for key in fields},
                               'required': fields, 'additionalProperties': False}
    create = prepare_descriptor()
    create.update(id='demo.create', proposal_kind='file_create')
    del create['input_schema']['properties']['expected_sha256']
    create['input_schema']['required'].remove('expected_sha256')
    code = """import base64,hashlib
def handle(operation_id, arguments, context):
    if operation_id == 'demo.comment': return {'comment': 'Reviewed'}
    data = b'Created offline\\r\\n'
    return {'data_base64': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest()}
"""
    stage(bridge, tmp_path, code=code, operations=[comment, create])
    old = bridge.config.robots['r1']
    bridge.config = replace(bridge.config, robots={'r1': replace(old,
        profile=replace(old.profile, roots=['md:', 'ud1:']), creatable_files=('ud1:NEW.TXT',))})
    capture = bridge.read_file('r1', 'md:NUMREG.VA')
    op = descriptor(bridge, 'demo.comment')
    plan = bridge.extensions.prepare(op['id'], {'robot_id': 'r1', 'listing_path': 'md:NUMREG.VA',
        'target': 'R[1]', 'expected_sha256': capture['sha256']}, op['revision'])
    assert bridge.comments.plans[plan['plan_id']].comment == 'Reviewed'
    assert bridge.change_review(plan['plan_id'], plan['proposed_sha256'])['target'] == 'R[1]'
    bridge.cancel_change(plan['plan_id'])
    op = descriptor(bridge, 'demo.create')
    plan = bridge.extensions.prepare(op['id'], {'robot_id': 'r1', 'path': 'ud1:NEW.TXT'}, op['revision'])
    assert plan['original_sha256'] is None and plan['proposed_sha256'] == digest(b'Created offline\r\n')
    assert bridge._plans[plan['plan_id']].payload == b'Created offline\r\n'
    bridge.cancel_change(plan['plan_id'])
    assert robot.uploads == 0


def test_core_catalog_prepares_the_same_reviewed_file_plan(reload_case):
    bridge, robot, _, _, _ = reload_case
    operation = descriptor(bridge, 'core.files.replace')
    plan = bridge.extensions.prepare(operation['id'], {'robot_id': 'r1', 'path': 'md:MAIN.LS',
        'source_file': 'MAIN.LS', 'expected_sha256': digest(ORIGINAL)}, operation['revision'])
    assert bridge._plans[plan['plan_id']].payload == PROPOSED and robot.uploads == 0
    bridge.cancel_change(plan['plan_id'])


def test_successful_reload_invalidates_real_file_and_comment_plans(reload_case, tmp_path):
    bridge, robot, _, _, _ = reload_case
    stage(bridge, tmp_path)
    bridge.extensions.reload()
    file_plan = bridge.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', digest(ORIGINAL))
    capture = bridge.read_file('r1', 'md:NUMREG.VA')
    comment = bridge.comments.prepare('r1', 'md:NUMREG.VA', 'R[1]', 'New', capture['sha256'])
    stage(bridge, tmp_path, version='1.1.0')
    result = bridge.extensions.reload()
    assert result['invalidated_upload_plans'] == result['invalidated_comment_plans'] == 1
    for plan in (file_plan, comment):
        with pytest.raises(ValueError):
            bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert robot.uploads == 0


def test_same_mcp_session_discovers_new_operation_without_tool_schema_changes(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    config = tmp_path / 'bridge.json'
    config.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}}))
    root = tmp_path / 'state/extensions'
    source = tmp_path / 'v1'
    first = release(source)
    install_release(root, source)

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(config)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(parameters) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            before = [tool.model_dump() for tool in (await session.list_tools()).tools]
            async def call(name, args):
                result = await session.call_tool(name, args)
                assert not result.isError, result
                return json.loads(result.content[0].text)
            original = await call('describe_operation', {'operation_id': 'demo.read'})
            old_pid = (await call('list_operations', {}))['extensions'][0]['worker_pid']
            source2 = tmp_path / 'v2'
            new = copy.deepcopy(first['operations'][0])
            new['id'] = 'demo.extra'
            release(source2, version='1.1.0', operations=[first['operations'][0], new])
            install_release(root, source2)
            assert (await call('reload_extensions', {}))['status'] == 'reloaded'
            current = await call('describe_operation', {'operation_id': 'demo.extra'})
            result = await call('run_read_operation', {'operation_id': current['id'], 'arguments': {'value': 'new operation'}, 'revision': current['revision']})
            assert result['result']['value'] == 'new operation'
            assert old_pid != (await call('list_operations', {}))['extensions'][0]['worker_pid']
            assert before == [tool.model_dump() for tool in (await session.list_tools()).tools]
            stale = await session.call_tool('run_read_operation', {'operation_id': 'demo.read', 'arguments': {'value': 'old'}, 'revision': original['revision']})
            assert stale.isError
            assert len(before) == 48
    asyncio.run(scenario())
