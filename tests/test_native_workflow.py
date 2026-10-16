from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from test_bridge_operations import bridge_case
from fanuc_workbench.services.native_programs import validate_source, verify_listing
from fanuc_workbench.services.native_values import fingerprint
from fanuc_workbench.services.native_controller import NativeApiError

SOURCE = b'/PROG FWBTEST\r\n/ATTR\r\nCOMMENT = "Workbench fixture";\r\n/MN\r\n   1: ! Native loader fixture ;\r\n/POS\r\n/END\r\n'


class FakeNative:
    def __init__(self, robot):
        self.robot = robot
        self.values = {'R[999]': {'kind': 'R', 'group': 1, 'index': 999, 'type': 'integer', 'value': 0},
            'PR[1,100]': {'kind': 'PR', 'group': 1, 'index': 100, 'initialized': True,
            'native_type': 9, 'representation': 'joint', 'user_frame': 255, 'user_tool': 255,
            'values': {'J1': 0.0, 'J2': 1.0}}}
        self.metadata, self.calls = {}, []
        self.writes = self.loads = self.closes = 0
        self.corrupt_write = self.fail_after_write = self.wrong_listing = False

    def call(self, operation, **args):
        self.calls.append(operation)
        if operation == 'read_register':
            return copy.deepcopy(self.values[args['target']])
        if operation == 'write_register':
            assert args['expected'] == self.values[args['target']]
            self.writes += 1
            self.values[args['target']] = copy.deepcopy(args['proposed'])
            if self.corrupt_write:
                self.values[args['target']]['value'] = 1234
            if self.fail_after_write:
                raise RuntimeError('simulated uncertain transport failure')
            return {'status': 'verified', 'value': copy.deepcopy(self.values[args['target']])}
        if operation == 'programs':
            return copy.deepcopy(list(self.metadata.values()))
        if operation == 'load_program':
            self.loads += 1
            name = args['name']
            data = Path(args['source']).read_bytes()
            self.metadata[name] = {'name': name, 'comment': 'Workbench fixture', 'bytes': len(data), 'protection': 0}
            self.robot.files['md:'+name+'.LS'] = data.replace(b'fixture', b'different') if self.wrong_listing else data
            self.robot.files['md:'+name+'.TP'] = b'COMPILED:' + data
            return {'status': 'native_loader_accepted', 'executed': False}
        if operation == 'sample':
            return {'timestamp': 1, 'provider': 'frrobot', 'values': {key: copy.deepcopy(self.values[key]) for key in args['expressions']}, 'errors': {}}
        if operation == 'status':
            return {'provider': 'frrobot', 'tasks': [], 'alarms': {}, 'position': {}, 'errors': {}}
        raise AssertionError(operation)

    def close(self):
        self.closes += 1


@pytest.fixture
def native_case(bridge_case):
    bridge, robot, config = bridge_case
    api = FakeNative(robot)
    access = replace(config.robots['r1'], native_provider='frrobot',
        native_register_targets=('R[999]', 'PR[1,100]'), native_programs=('FWBTEST',))
    bridge.config = replace(config, robots={'r1': access}, approval_mode='elicitation')
    bridge.native.factory = lambda profile: api
    return bridge, robot, api


def reg_plan(bridge, change=None):
    capture = bridge.native.read_register('r1', 'R[999]')
    return bridge.native.prepare_register('r1', 'R[999]', change or {'type': 'integer', 'value': 1}, capture['sha256'])


def program_plan(bridge):
    source = bridge.config.source_dir / 'FWBTEST.LS'
    source.write_bytes(SOURCE)
    capture = bridge.native.read_program('r1', 'FWBTEST')
    return bridge.native.prepare_program('r1', source.name, capture['sha256'])


def test_native_provider_is_opt_in_and_policy_precedes_api(bridge_case):
    bridge, _, _ = bridge_case
    called = []
    bridge.native.factory = lambda profile: called.append(profile)
    with pytest.raises(ValueError, match='disabled'):
        bridge.native.read_register('r1', 'R[999]')
    assert not called


def test_register_plan_is_frozen_requires_exact_confirmation_and_verifies(native_case):
    bridge, _, api = native_case
    change = {'type': 'integer', 'value': 1}
    plan = reg_plan(bridge, change)
    change['value'] = 999
    assert not api.writes and bridge.change_review(plan['plan_id'], plan['proposed_sha256'])['proposal']['proposed']['value']['value'] == 1
    for kwargs in [{}, {'client_approved': True}]:
        with pytest.raises(ValueError):
            bridge.commit_change(plan['plan_id'], '0'*64 if kwargs else plan['proposed_sha256'], **kwargs)
    result = bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert result['status'] == 'verified' and api.writes == 1 and api.values['R[999]']['value'] == 1
    with pytest.raises(ValueError):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert api.writes == 1


@pytest.mark.parametrize('failure', ['stale', 'disabled', 'target', 'tampered_capture'])
def test_register_preparation_and_commit_enforce_policy_and_preserved_reads(native_case, failure):
    bridge, _, api = native_case
    capture = bridge.native.read_register('r1', 'R[999]')
    if failure == 'stale':
        api.values['R[999]']['value'] = 2
    elif failure == 'disabled':
        bridge.config = replace(bridge.config, allow_writes=False)
    elif failure == 'target':
        bridge.config = replace(bridge.config, robots={'r1': replace(bridge.config.robots['r1'], native_register_targets=())})
    else:
        Path(capture['local_path']).write_bytes(b'tampered')
    with pytest.raises(ValueError):
        bridge.native.prepare_register('r1', 'R[999]', {'type': 'integer', 'value': 1}, capture['sha256'])
    assert api.writes == 0 and not bridge.native.plans


def test_native_target_drift_at_commit_is_refused(native_case):
    bridge, _, api = native_case
    plan = reg_plan(bridge)
    api.values['R[999]']['value'] = 2
    with pytest.raises(ValueError, match='changed after approval'):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert api.writes == 0


@pytest.mark.parametrize('mode', ['corrupt_write', 'fail_after_write'])
def test_uncertain_native_writes_consume_plans_and_are_never_retried(native_case, mode):
    bridge, _, api = native_case
    plan = reg_plan(bridge)
    setattr(api, mode, True)
    with pytest.raises(RuntimeError, match='plan consumed'):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    with pytest.raises(ValueError):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert api.writes == 1


def test_native_vendor_failure_keeps_diagnostics_in_preserved_evidence(native_case, monkeypatch):
    bridge, _, api = native_case
    plan = reg_plan(bridge)
    original = api.call
    response = {'ok': False, 'error_type': 'VendorCallError',
        'vendor_call': {'operation': 'call', 'member': 'Update', 'vendor_code': -123}}
    def call(operation, **args):
        if operation == 'write_register':
            raise NativeApiError(operation, response)
        return original(operation, **args)
    monkeypatch.setattr(api, 'call', call)
    with pytest.raises(RuntimeError, match='plan consumed'):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    evidence = [json.loads(p.read_text()) for p in bridge.config.state_dir.rglob('failure.json')]
    assert evidence[-1]['native_api']['vendor_call']['member'] == 'Update'
    assert plan['plan_id'] not in bridge.native.plans


def test_pr_edits_preserve_metadata_and_cancel_uses_generic_route(native_case):
    bridge, _, api = native_case
    capture = bridge.native.read_register('r1', 'PR[1,100]')
    plan = bridge.native.prepare_register('r1', 'PR[1,100]', {'values': {'J1': 5}}, capture['sha256'])
    proposed = plan['proposal']['proposed']['value']
    assert proposed['user_frame'] == proposed['user_tool'] == 255 and proposed['values']['J2'] == 1
    assert bridge.cancel_change(plan['plan_id'])['status'] == 'cancelled'
    assert not api.writes


def test_program_deployment_freezes_source_and_verifies_tp_and_ls(native_case):
    bridge, robot, api = native_case
    plan = program_plan(bridge)
    assert api.loads == 0 and not robot.uploads
    (bridge.config.source_dir / 'FWBTEST.LS').write_bytes(b'changed after review')
    result = bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert api.loads == 1 and result['status'] == 'verified' and not result['executed']
    assert robot.files['md:FWBTEST.LS'] == SOURCE
    assert result['listing_verification']['matches'] and result['capture']['value']['tp_sha256']


def test_program_replacement_preserves_old_bytes_and_refuses_changed_tp(native_case):
    bridge, robot, api = native_case
    plan = program_plan(bridge)
    bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    plan = program_plan(bridge)
    old = robot.files['md:FWBTEST.TP']
    assert Path(plan['review_path']).parent.joinpath('FWBTEST.TP').read_bytes() == old
    robot.files['md:FWBTEST.TP'] += b'changed'
    with pytest.raises(ValueError, match='changed after approval'):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert api.loads == 1


def test_native_load_with_different_listing_is_not_reported_verified(native_case):
    bridge, _, api = native_case
    plan = program_plan(bridge)
    api.wrong_listing = True
    with pytest.raises(RuntimeError, match='verification is incomplete'):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert api.loads == 1 and plan['plan_id'] not in bridge.native.plans


def test_core_catalog_carries_native_operations_without_new_mcp_tools(native_case):
    bridge, _, api = native_case
    op = bridge.extensions.describe('core.registers.read')
    captured = bridge.extensions.run_read(op['id'], {'robot_id': 'r1', 'target': 'R[999]'}, op['revision'])['result']
    op = bridge.extensions.describe('core.registers.update')
    plan = bridge.extensions.prepare(op['id'], {'robot_id': 'r1', 'target': 'R[999]',
        'change': {'type': 'integer', 'value': 1}, 'expected_sha256': captured['sha256']}, op['revision'])
    assert plan['plan_id'] in bridge.native.plans and not api.writes
    with pytest.raises(ValueError):
        bridge.extensions.run_read(op['id'], {}, op['revision'])


def test_native_sample_records_typed_evidence_and_existing_watch_can_use_it(native_case):
    bridge, _, api = native_case
    sample = bridge.operations.sample('r1', ['R[999]'])
    assert sample['values']['R[999]'] == 0 and sample['source'] == 'native FRRobot TCP API'
    bridge.operations.store.verify(sample['artifact_id'])
    assert not api.writes


def test_native_audit_failure_prevents_vendor_write_and_preserves_plan(native_case, monkeypatch):
    bridge, _, api = native_case
    plan = reg_plan(bridge)
    original = bridge._audit
    def audit(action, **fields):
        if action == 'native_commit_started':
            raise OSError('audit unavailable')
        return original(action, **fields)
    monkeypatch.setattr(bridge, '_audit', audit)
    with pytest.raises(OSError):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert not api.writes and plan['plan_id'] in bridge.native.plans


def test_listing_verification_retains_instruction_and_position_differences():
    altered = SOURCE.replace(b'   1:', b'  99:')
    assert verify_listing(SOURCE, altered)['matches']
    sized = SOURCE.replace(b'/ATTR', b'/ATTR\r\nPROG_SIZE = 0;')
    assert verify_listing(sized, sized.replace(b'PROG_SIZE = 0', b'PROG_SIZE = 1234'))['matches']
    assert not verify_listing(SOURCE, SOURCE.replace(b'fixture', b'changed'))['matches']
    assert validate_source(SOURCE)['program'] == 'FWBTEST'
    for bad in [SOURCE.replace(b'/END', b''), SOURCE.replace(b'FWBTEST', b'../X'), SOURCE+b'/END']:
        with pytest.raises(ValueError):
            validate_source(bad)


def test_endpoint_refresh_closes_native_session_and_invalidates_plans_and_reads(native_case, tmp_path):
    bridge, _, api = native_case
    plan = reg_plan(bridge)
    access = bridge.config.robots['r1']
    document = {'state_dir': str(bridge.config.state_dir), 'source_dir': str(bridge.config.source_dir),
        'allow_writes': True, 'approval_mode': 'elicitation', 'robots': {'r1': {
        'profile': access.profile.to_dict(), 'writable_files': list(access.writable_files),
        'native_provider': 'frrobot', 'native_register_targets': list(access.native_register_targets),
        'native_programs': list(access.native_programs)}}}
    path = tmp_path / 'native.json'
    path.write_text(json.dumps(document))
    bridge.config_path = path
    document['robots']['r1']['profile']['host'] = '192.0.2.2'
    path.write_text(json.dumps(document))
    result = bridge.reload_connections()
    assert result['invalidated_native_plans'] == 1 and api.closes == 1
    assert not bridge.native.clients and not bridge.native.receipts and not bridge.native.plans
    with pytest.raises(ValueError):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)


def test_extension_reload_invalidates_native_approval(native_case, tmp_path):
    from test_extension_runtime import release
    from fanuc_workbench.bridge.extension_release import install_release
    bridge, _, api = native_case
    bridge.extensions.list()
    plan = reg_plan(bridge)
    source = tmp_path / 'feature'
    release(source)
    install_release(bridge.extensions.root, source)
    result = bridge.extensions.reload()
    assert result['invalidated_native_plans'] == 1 and not bridge.native.plans
    with pytest.raises(ValueError):
        bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True)
    assert not api.writes
