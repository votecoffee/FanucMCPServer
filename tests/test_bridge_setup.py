from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from test_bridge_operations import bridge_case, imported, finished
from fanuc_workbench.bridge.doctor import diagnose
from fanuc_workbench.services.workspace_metadata import FILES


@pytest.mark.parametrize('kind,data', [
    ('checklist', [{'name': 'Backup reviewed', 'done': True, 'note': 'Offline evidence'}]),
    ('plc_map', {'DI[1]': 'PLC.Ready', 'DO[2]': 'PLC.Start'}),
    ('handshake', [{'name': 'Ready', 'robot_signal': 'DI[1]', 'plc_tag': 'PLC.Ready',
                    'direction': 'input', 'expected_peer': 'DO[2]'}]),
])
def test_metadata_versions_use_gui_format_and_preserve_source(bridge_case, kind, data):
    bridge, robot, config = bridge_case
    source = imported(bridge, robot, config)
    assert not bridge.operations.read_metadata(source, kind)['present']
    result = bridge.operations.write_metadata(source, kind, data)
    assert result['artifact_id'] != source and result['controller_written'] is False
    assert bridge.operations.read_metadata(result['artifact_id'], kind)['data'] == data
    assert not (bridge.operations.store.root(source) / FILES[kind]).exists()
    bridge.operations.store.verify(source)
    bridge.operations.store.verify(result['artifact_id'])
    assert robot.connects == robot.uploads == 0


@pytest.mark.parametrize('kind,data', [
    ('checklist', [{'name': 'Repeated'}, {'name': 'Repeated'}]),
    ('checklist', [{'name': 'Wrong bool', 'done': 'yes'}]),
    ('plc_map', {'../file': 'Tag'}),
    ('plc_map', {'DI[1]': 'Line\nBreak'}),
    ('handshake', [{'name': 'Bad', 'robot_signal': 'DI[*]'}]),
])
def test_metadata_rejects_invalid_rows(bridge_case, kind, data):
    bridge, robot, config = bridge_case
    with pytest.raises(ValueError):
        bridge.operations.write_metadata(imported(bridge, robot, config), kind, data)
    assert robot.connects == 0


def test_projects_preserve_versions_and_reject_duplicate_backups(bridge_case):
    bridge, robot, config = bridge_case
    workspace = imported(bridge, robot, config)
    empty = bridge.operations.project('create', name='Test Cell')['project_id']
    saved = bridge.operations.project('add_workspace', project_id=empty, robot_id='r1',
                                     workspace_id=workspace, metadata={'software': 'V8.33'})
    assert bridge.operations.project('read', project_id=empty)['robots'] == []
    row = saved['robots'][0]
    assert row['name'] == 'r1' and row['software'] == 'V8.33'
    copy = bridge.operations.store.root(saved['project_id']) / row['backups'][0] / 'MAIN.LS'
    assert copy.read_bytes() == robot.files['md:MAIN.LS']
    with pytest.raises(FileExistsError):
        bridge.operations.project('add_workspace', project_id=saved['project_id'],
                                  robot_id='r1', workspace_id=workspace)
    assert robot.connects == robot.uploads == 0


def test_probe_redacts_connection_failures_and_fleet_records_jobs(bridge_case):
    bridge, robot, _ = bridge_case
    assert bridge.operations.probe('r1')['reachable']
    robot.fail_connect = True
    result = bridge.operations.probe('r1')
    assert not result['reachable'] and 'secret' not in json.dumps(result)
    robot.fail_connect = False
    job = bridge.operations.start_fleet_backup(['r1'], attempt_ascii=False)['jobs'][0]
    assert finished(bridge.operations.jobs, job['job_id'])['status'] == 'complete'
    with pytest.raises(ValueError):
        bridge.operations.start_fleet_backup(['r1', 'r1'])
    assert robot.uploads == 0


def test_doctor_uses_real_mcp_without_contacting_a_controller(tmp_path):
    config = tmp_path / 'bridge.json'
    config.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {},
                                  'approval_mode': 'elicitation'}), encoding='utf-8')
    result = asyncio.run(diagnose(config))
    assert result['status'] == 'ok' and result['tool_count'] == 51
    assert result['controller_contacted'] is False and result['gui_imported'] is False
    assert 'workspace_project' in result['tools'] and 'probe_robot' in result['tools']


def test_doctor_measures_server_imports_when_the_caller_has_gui_modules(tmp_path):
    import PySide6
    config = tmp_path / 'bridge.json'
    config.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}}))
    result = asyncio.run(diagnose(config))
    assert result['caller_gui_imported'] is True
    assert result['gui_imported'] is False and result['controller_contacted'] is False


def test_plugin_launcher_handles_paths_with_spaces(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = tmp_path / 'Plugin With Spaces'
    (root / 'server').mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / 'plugins/fanuc-mcp-server/server/launch.py'
    launcher = root / 'server/launch.py'
    shutil.copyfile(source, launcher)
    config = root / 'Config With Spaces.json'
    config.write_text(json.dumps({'state_dir':'state', 'source_dir':'edits', 'robots':{}}), encoding='utf-8')
    (root/'runtime.json').write_text(json.dumps({'python':sys.executable, 'config':str(config)}), encoding='utf-8')

    async def check():
        params = StdioServerParameters(command=sys.executable, args=['-B',str(launcher)],
                                      env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
        async with stdio_client(params) as (reader,writer), ClientSession(reader,writer) as client:
            await client.initialize()
            assert len((await client.list_tools()).tools) == 51
    asyncio.run(check())


def test_bundle_verifier_rejects_tampering_and_external_paths(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'packaging/bridge/setup_bundle.py'
    spec = importlib.util.spec_from_file_location('bundle_setup', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = tmp_path / 'payload.whl'
    payload.write_bytes(b'wheel')
    manifest = {'compatibility': module.compatibility(), 'files': [
        {'path': payload.name, 'sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}]}
    marker = tmp_path / 'BUNDLE_MANIFEST.json'
    marker.write_text(json.dumps(manifest), encoding='utf-8')
    module.verify_bundle(tmp_path)
    payload.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash mismatch'):
        module.verify_bundle(tmp_path)
    manifest['files'][0]['path'] = '../external.whl'
    marker.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='external file'):
        module.verify_bundle(tmp_path)


def test_controller_directory_pagination_has_no_lost_entries(bridge_case):
    bridge, robot, _ = bridge_case
    for i in range(1010):
        robot.files[f'md:DATA{i:04d}.TXT'] = b'data'
    first = bridge.list_files('r1', 'md:')
    second = bridge.list_files('r1', 'md:', first['next_offset'])
    assert first['possibly_truncated'] and not second['possibly_truncated']
    paths = [row['path'] for row in first['entries'] + second['entries']]
    assert len(paths) == len(set(paths)) == len(robot.files)


def test_ftp_list_fallback_avoids_per_file_commands():
    from fanuc_workbench.backends.ftp import FanucFtpBackend, _unix_listing
    from fanuc_workbench.core.models import RobotProfile
    from ftplib import error_perm

    class Wire:
        def pwd(self): return 'md:'
        def cwd(self, path): assert path == 'md:'
        def mlsd(self): raise error_perm('500 Unsupported')
        def retrlines(self, command, callback):
            assert command == 'LIST'
            for line in ['-rw-rw-rw- 1 noone nogroup 0 sep 10 2026 main.ls',
                         'drwxrwxrwx 1 noone nogroup 0 sep 10 2026 Folder with spaces']:
                callback(line)
        def nlst(self): pytest.fail('Recognized LIST must not probe files individually')

    backend = FanucFtpBackend(RobotProfile('test', 'unused'))
    backend.ftp = Wire()
    entries = backend.list_dir('md:')
    assert entries[0].is_dir and entries[1].path == 'md:main.ls'
    assert _unix_listing('md:', ['unknown format']) is None


def test_position_listing_leading_decimal_axes_are_read_and_editable(tmp_path):
    from fanuc_workbench.parsers.data import parse_position_registers, set_position_register, parse_frames
    path = tmp_path / 'POSREG.VA'
    original = ("[1,1] = 'Home' Group: 1\r\n"
                " J1 = 29.718 deg J2 = 9.600 deg J3 = -27.600 deg\r\n"
                " J4 = -.000 deg J5 = -11.342 deg J6 = .000 deg\r\n").encode()
    path.write_bytes(original)
    record = parse_position_registers(path)[0]
    assert set(record.values) == {f'J{i}' for i in range(1,7)}
    assert record.values['J4'] == record.values['J6'] == 0
    set_position_register(record, values={'J4': 1.25, 'J6': -.5})
    after = parse_position_registers(path)[0]
    assert after.values['J4'] == 1.25 and after.values['J6'] == -.5
    assert b'\r\n' in path.read_bytes()
    frame = tmp_path / 'FRAME.DG'
    frame.write_text('User Frame\n.5 -.5 +.0 1. 1e-2 0 Fixture\n')
    assert parse_frames(frame)[0].values['P'] == .01
