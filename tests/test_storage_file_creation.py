from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from test_robot_bridge import MemoryRobot, digest
from fanuc_workbench.bridge.config import BridgeConfig, RobotAccess
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.core.models import RobotProfile


BASELINE = b'FANUC Workbench TCP fixture v1\r\n'
UPDATED = b'FANUC Workbench TCP fixture v2\r\n'


class StorageRobot(MemoryRobot):
    fail_listing = False

    def list_dir(self, path):
        if self.fail_listing:
            raise OSError('transport detail with secret-password')
        return [r for r in super().list_dir(path) if r.path.startswith(path)]


@pytest.fixture
def storage_case(tmp_path):
    source = tmp_path/'edits'
    source.mkdir()
    (source/'FWB_TCP.TXT').write_bytes(BASELINE)
    robot = StorageRobot()
    access = RobotAccess(RobotProfile('Test storage', '192.0.2.1', roots=['md:','ud1:']),
                         writable_files=('ud1:FWB_TCP.TXT',), creatable_files=('ud1:FWB_TCP.TXT',))
    config = BridgeConfig(tmp_path/'state', source, {'r1':access}, allow_writes=True, approval_mode='elicitation')
    with RobotBridge(config, lambda _:robot) as bridge:
        yield bridge, robot, config


def prepare(bridge):
    return bridge.prepare_file_create('r1','ud1:FWB_TCP.TXT','FWB_TCP.TXT')


def test_create_approval_exact_bytes_and_subsequent_reviewed_replacement(storage_case):
    bridge, robot, config = storage_case
    plan = prepare(bridge)
    assert robot.uploads == 0 and plan['operation']=='create' and plan['original_sha256'] is None
    assert plan['diff'].startswith('--- absent') and plan['atomic_no_clobber'] is False
    assert Path(plan['absence_evidence_path']).exists()
    with pytest.raises(ValueError, match='confirmation'):
        bridge.commit_upload(plan['plan_id'],plan['proposed_sha256'])
    (config.source_dir/'FWB_TCP.TXT').write_bytes(b'changed after preparation')
    result = bridge.commit_upload(plan['plan_id'],plan['proposed_sha256'],client_approved=True)
    assert result['operation']=='create' and result['sha256']==digest(BASELINE)
    assert robot.files['ud1:FWB_TCP.TXT']==BASELINE
    assert json.loads(Path(result['preflight_path']).read_text())['status']=='absent'
    with pytest.raises(ValueError, match='consumed'):
        bridge.commit_upload(plan['plan_id'],plan['proposed_sha256'],client_approved=True)
    (config.source_dir/'FWB_TCP.TXT').write_bytes(UPDATED)
    change = bridge.prepare_upload('r1','ud1:FWB_TCP.TXT','FWB_TCP.TXT',result['sha256'])
    replaced = bridge.commit_upload(change['plan_id'],change['proposed_sha256'],client_approved=True)
    assert Path(replaced['backup_path']).read_bytes()==BASELINE
    assert robot.files['ud1:FWB_TCP.TXT']==UPDATED


def test_existing_target_case_variant_and_preflight_race_are_refused(storage_case):
    bridge, robot, _ = storage_case
    plan = prepare(bridge)
    robot.files['ud1:fwb_tcp.txt'] = b'created by another client'
    with pytest.raises(RuntimeError, match='not_attempted'):
        bridge.commit_upload(plan['plan_id'],plan['proposed_sha256'],client_approved=True)
    assert robot.uploads==0
    with pytest.raises(FileExistsError):
        prepare(bridge)


def test_missing_listing_is_not_treated_as_absence(storage_case):
    bridge, robot, _ = storage_case
    robot.fail_listing=True
    with pytest.raises(RuntimeError) as exc:
        prepare(bridge)
    assert 'secret-password' not in str(exc.value) and robot.uploads==0 and not bridge._plans


@pytest.mark.parametrize('failure', ['fail_upload','corrupt_upload'])
def test_create_failure_is_uncertain_consumed_and_never_retried(storage_case,failure):
    bridge, robot, _ = storage_case
    plan=prepare(bridge)
    setattr(robot,failure,True)
    with pytest.raises(RuntimeError,match='uncertain'):
        bridge.commit_upload(plan['plan_id'],plan['proposed_sha256'],client_approved=True)
    assert robot.uploads==1
    with pytest.raises(ValueError,match='consumed'):
        bridge.commit_upload(plan['plan_id'],plan['proposed_sha256'],client_approved=True)


def test_create_audit_failure_happens_before_upload(storage_case,monkeypatch):
    bridge,robot,_=storage_case
    plan=prepare(bridge)
    original=bridge._audit
    def fail_start(action,**fields):
        if action=='upload_started': raise OSError('disk full')
        return original(action,**fields)
    monkeypatch.setattr(bridge,'_audit',fail_start)
    with pytest.raises(RuntimeError,match='not_attempted'):
        bridge.commit_upload(plan['plan_id'],plan['proposed_sha256'],client_approved=True)
    assert robot.uploads==0


@pytest.mark.parametrize('target',['md:TEST.TXT','ud1:TEST.LS','ud1:TEST.TP','ud1:TEST.PC'])
def test_creation_policy_excludes_programs_and_md_even_with_direct_config(storage_case,target):
    bridge,robot,config=storage_case
    access=replace(config.robots['r1'],creatable_files=(target,))
    with RobotBridge(replace(config,robots={'r1':access}),lambda _:robot) as other:
        with pytest.raises(ValueError,match='TXT/CSV'):
            other.prepare_file_create('r1',target,'FWB_TCP.TXT')
    assert robot.connects==0


def test_create_requires_its_own_allowlist_and_write_switch(storage_case):
    bridge,robot,config=storage_case
    with pytest.raises(ValueError,match='creatable_files'):
        bridge.prepare_file_create('r1','ud1:OTHER.TXT','FWB_TCP.TXT')
    with RobotBridge(replace(config,allow_writes=False),lambda _:robot) as readonly:
        with pytest.raises(ValueError,match='disabled'):
            prepare(readonly)
    assert robot.connects==0


def test_creation_config_rejects_outside_roots_and_programs(tmp_path):
    path=tmp_path/'bridge.json'
    data={'state_dir':'state','source_dir':'edits','robots':{'r1':{
        'profile':{'host':'192.0.2.1','roots':['md:','ud1:']},'creatable_files':['ud1:FWB_TCP.TXT']}}}
    path.write_text(json.dumps(data))
    assert BridgeConfig.load(path).robots['r1'].creatable_files==('ud1:FWB_TCP.TXT',)
    for target in ('mc:TEST.TXT','ud1:TEST.LS','md:TEST.TXT'):
        data['robots']['r1']['creatable_files']=[target]
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError): BridgeConfig.load(path)
