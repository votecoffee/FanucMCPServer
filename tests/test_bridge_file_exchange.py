from __future__ import annotations

import base64
import os
from dataclasses import replace
from pathlib import Path

import pytest

from test_robot_bridge import setup, ORIGINAL, PROPOSED, digest
from fanuc_workbench.bridge.service import RobotBridge


def encoded(data):
    return base64.b64encode(data).decode('ascii')


def test_exact_binary_ranges_use_retained_bytes_and_reject_tampering(setup):
    bridge, robot, config = setup
    payload = bytes(range(256)) * 300
    robot.files['md:BINARY.PC'] = payload
    capture = bridge.read_file('r1', 'md:BINARY.PC')
    chunks, offset = [], 0
    while offset is not None:
        result = bridge.read_captured_file('r1', 'md:BINARY.PC', capture['sha256'], offset, 65536)
        assert result['controller_contacted'] is False
        chunks.append(base64.b64decode(result['base64'], validate=True))
        offset = result['next_offset']
    assert b''.join(chunks) == payload and robot.connects == 1
    robot.files['md:BINARY.PC'] = b'new live content'
    assert bridge.read_captured_file('r1', 'md:BINARY.PC', capture['sha256'])['sha256'] == digest(payload)
    assert robot.connects == 1
    Path(capture['local_path']).write_bytes(b'changed retained bytes')
    with pytest.raises(ValueError, match='intact preserved read'):
        bridge.read_captured_file('r1', 'md:BINARY.PC', capture['sha256'])
    assert robot.connects == 1


@pytest.mark.parametrize('offset,length', [(-1, 1), (False, 1), (0, 0), (0, 65537), (0, True)])
def test_capture_ranges_validate_before_access(setup, offset, length):
    bridge, robot, _ = setup
    with pytest.raises(ValueError, match='offset'):
        bridge.read_captured_file('r1', 'md:MAIN.LS', digest(ORIGINAL), offset, length)
    assert robot.connects == 0


def test_capture_requires_known_robot_path_and_hash(setup):
    bridge, robot, _ = setup
    capture = bridge.read_file('r1', 'md:MAIN.LS')
    for robot_id, path, sha in [('r1', 'md:ERRALL.LS', capture['sha256']),
                                ('r1', 'md:MAIN.LS', '0' * 64),
                                ('unknown', 'md:MAIN.LS', capture['sha256']),
                                ('r1', 'mc:MAIN.LS', capture['sha256'])]:
        with pytest.raises(ValueError):
            bridge.read_captured_file(robot_id, path, sha)
    assert robot.connects == 1


def test_staged_binary_bytes_are_unique_local_and_usable_by_existing_workflow(setup):
    bridge, robot, config = setup
    binary = bytes(range(256))
    first = bridge.stage_file('MAIN.PC', encoded(binary), digest(binary))
    second = bridge.stage_file('MAIN.PC', encoded(b'other'), digest(b'other'))
    assert first['source_file'] != second['source_file']
    assert (config.source_dir / first['source_file']).read_bytes() == binary
    assert (config.source_dir / second['source_file']).read_bytes() == b'other'
    assert first['controller_written'] is first['controller_contacted'] is False
    assert robot.connects == robot.uploads == 0
    proposal = bridge.stage_file('MAIN.LS', encoded(PROPOSED), digest(PROPOSED))
    plan = bridge.prepare_upload('r1', 'md:MAIN.LS', proposal['source_file'], digest(ORIGINAL))
    assert plan['proposed_sha256'] == digest(PROPOSED)
    assert robot.uploads == 0
    bridge.cancel_upload(plan['plan_id'])


@pytest.mark.parametrize('name', ['../MAIN.LS', 'a/MAIN.LS', r'a\MAIN.LS', 'C:MAIN.LS',
                                  '/MAIN.LS', 'MAIN.LS.', 'CON.TXT', 'LPT9.LS',
                                  'MAIN.SV', 'MAIN.LS:stream', 'bad\n.TXT', 'x' * 129 + '.LS'])
def test_stage_rejects_paths_devices_and_unsupported_names(setup, name):
    bridge, robot, config = setup
    with pytest.raises(ValueError, match='filename'):
        bridge.stage_file(name, encoded(PROPOSED), digest(PROPOSED))
    assert not (config.source_dir / 'staged').exists() and robot.connects == 0


@pytest.mark.parametrize('payload,sha', [('', digest(b'')), ('!!!!', digest(b'x')),
                                        ('é===', digest(b'x')), ('eA==\n', digest(b'x')),
                                        (encoded(b'x'), '0' * 64), (encoded(b'x'), 'invalid')])
def test_stage_rejects_invalid_encoding_or_hash_before_writing(setup, payload, sha):
    bridge, robot, config = setup
    with pytest.raises(ValueError):
        bridge.stage_file('MAIN.TXT', payload, sha)
    assert not (config.source_dir / 'staged').exists() and robot.connects == 0


def test_stage_honors_file_limit_and_read_only_controller_policy(setup):
    bridge, robot, config = setup
    with RobotBridge(replace(config, max_file_bytes=4, allow_writes=False), lambda _: robot) as small:
        with pytest.raises(ValueError, match='limit'):
            small.stage_file('MAIN.TXT', encoded(b'12345'), digest(b'12345'))
        result = small.stage_file('MAIN.LS', encoded(b'1234'), digest(b'1234'))
        assert result['max_staging_bytes'] == 4
        with pytest.raises(ValueError, match='disabled'):
            small.prepare_upload('r1', 'md:MAIN.LS', result['source_file'], digest(ORIGINAL))
    with pytest.raises(ValueError, match='limit'):
        bridge.stage_file('MAIN.TXT', encoded(b'x' * 1048577), digest(b'x' * 1048577))
    assert robot.connects == robot.uploads == 0


def test_stage_rejects_redirected_directory(setup, tmp_path):
    bridge, robot, config = setup
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        link = config.source_dir / 'staged'
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('The current filesystem does not permit directory links')
    with pytest.raises(ValueError, match='redirect'):
        bridge.stage_file('MAIN.TXT', encoded(b'x'), digest(b'x'))
    assert not list(outside.iterdir()) and robot.connects == 0
