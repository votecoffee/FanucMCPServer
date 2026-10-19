import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys

import pytest

from fanuc_workbench.bridge.extension_release import install_release, load_registry, load_release
from fanuc_workbench.bridge.extensions_cli import install_builtin
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.extensions.snpx.feature import MAX_BYTES, handle, self_test
from fanuc_workbench.services.snpx_decode import decode_capture
from test_robot_bridge import MemoryRobot
from test_snpx import bridge_policy, controller


def document(raw, address=100):
    count = len(raw) // 2
    return {'provider': 'snpx', 'address': address, 'count': count, 'raw_hex': raw.hex(),
            'words_u16': list(struct.unpack('<' + 'H' * count, raw)),
            'words_i16': list(struct.unpack('<' + 'h' * count, raw)), 'errors': {}}


def saved(tmp_path, raw, **changes):
    path = tmp_path / 'sample.json'
    data = {**document(raw), **changes}
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def field(kind='f32', *, byte_order='little', word_order='low_first', **changes):
    return {'name': 'example', 'word_offset': 0, 'type': kind,
            'byte_order': byte_order, 'word_order': word_order, **changes}


@pytest.mark.parametrize('kind,code,value', [
    ('u16', 'H', 65535), ('i16', 'h', -32768), ('u32', 'I', 0xF1234567), ('i32', 'i', -2147483648),
    ('u64', 'Q', 18446744073709551615), ('i64', 'q', -9223372036854775808),
    ('f32', 'f', -1.25), ('f64', 'd', math.pi)])
@pytest.mark.parametrize('byte_order,word_order', [
    ('little', 'low_first'), ('little', 'high_first'), ('big', 'low_first'), ('big', 'high_first')])
def test_all_types_and_orders_preserve_bits(tmp_path, kind, code, value, byte_order, word_order):
    # Build fixtures from struct's canonical big-endian encoding, independently
    # of the decoder's integer composition.
    canonical = struct.pack('>' + code, value)
    words = [canonical[index:index + 2] for index in range(0, len(canonical), 2)]
    if word_order == 'low_first':
        words.reverse()
    raw = b''.join(word[::-1] if byte_order == 'little' else word for word in words)
    path = saved(tmp_path, raw)
    original = path.read_bytes()
    result = decode_capture(path, [field(kind, byte_order=byte_order, word_order=word_order)])
    row = result['items'][0]
    assert row['values'] == [str(value) if kind in {'u64', 'i64'} else value]
    assert row['raw_hex'] == [raw.hex()] and not row['errors']
    assert row['value_encoding'] == ('decimal_string' if kind in {'u64', 'i64'} else 'number')
    assert result['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['available'] and not result['mapping_verified'] and not result['capture_provenance_verified']
    assert not result['controller_contacted'] and not result['scaling_applied']
    assert path.read_bytes() == original
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('raw,byte_order,word_order', [
    ('3f800000', 'big', 'high_first'), ('803f0000', 'little', 'high_first'),
    ('00003f80', 'big', 'low_first'), ('0000803f', 'little', 'low_first')])
def test_literal_ieee_one_vectors(tmp_path, raw, byte_order, word_order):
    result = decode_capture(saved(tmp_path, bytes.fromhex(raw)), [field(byte_order=byte_order, word_order=word_order)])
    assert result['items'][0]['values'] == [1.0]


def test_arrays_offsets_overlaps_and_raw_word_addresses(tmp_path):
    raw = struct.pack('<H3i', 0xABCD, -1, 12345, -2147483648)
    result = decode_capture(saved(tmp_path, raw), [field('i32', word_offset=1, count=3),
                           field('u16', name='label', word_offset=0),
                           field('u32', name='overlap', word_offset=1)])
    row, label, overlap = result['items']
    assert row['values'] == [-1, 12345, -2147483648]
    assert row['word_addresses'] == [101, 103, 105]
    assert label['values'] == [0xABCD]
    assert row['raw_hex'][0] == 'ffffffff'
    assert overlap['values'] == [4294967295]


@pytest.mark.parametrize('kind,code,value', [(kind, code, value)
    for kind, code in [('f32', 'f'), ('f64', 'd')] for value in [math.inf, -math.inf, math.nan]])
def test_nonfinite_floats_are_null_and_reported(tmp_path, kind, code, value):
    raw = struct.pack('<' + code, value)
    result = decode_capture(saved(tmp_path, raw), [field(kind)])
    row = result['items'][0]
    assert row['values'] == [None] and row['raw_hex'] == [raw.hex()]
    assert row['errors'] == [{'element': 0, 'reason': 'nonfinite_float'}]
    assert result['available']  # Capture bytes are available; interpretation is not finite.
    json.dumps(result, allow_nan=False)


def test_negative_zero_keeps_its_sign(tmp_path):
    result = decode_capture(saved(tmp_path, bytes.fromhex('00000080')), [field()])
    assert math.copysign(1, result['items'][0]['values'][0]) == -1


@pytest.mark.parametrize('fields', [[], None, [field(word_offset=True)], [field(count=True)],
    [field(byte_order='native')], [field(word_order='auto')], [field(type='position')],
    [field(word_offset=-1)], [field(count=0)], [field('f64', word_offset=127)],
    [field(), field()], [field(name='../path')], [field(extra='unsupported')],
    [{key: value for key, value in field().items() if key != 'byte_order'}],
    [field('u16', name=f'n{index}', count=128) for index in range(5)]])
def test_invalid_options_are_refused_before_file_access(fields):
    with pytest.raises(ValueError):
        decode_capture(Path('not-a-controller-file'), fields)


@pytest.mark.parametrize('changes', [
    {'provider': 'ftp'}, {'address': 0}, {'address': True}, {'address': 65535}, {'count': 129},
    {'count': True}, {'count': 1}, {'errors': []}, {'raw_hex': '0000'}, {'raw_hex': '0000000g'},
    {'raw_hex': '00 00 00'}, {'words_u16': [0, True]}, {'words_i16': [0, 1]},
    {'words_u16': None}, {'errors': {'words': 'unavailable'}}, {'raw_hex': None}])
def test_inconsistent_or_unsupported_captures_are_refused(tmp_path, changes):
    path = saved(tmp_path, bytes(4), **changes)
    with pytest.raises(ValueError):
        decode_capture(path, [field()])


def test_unavailable_capture_never_decodes_missing_bytes_as_zero(tmp_path):
    path = saved(tmp_path, bytes(8), errors={'words': 'unavailable'}, raw_hex=None, words_u16=None, words_i16=None)
    result = decode_capture(path, [field('u64')])
    assert not result['available']
    assert result['items'][0]['values'] == [None] and result['items'][0]['raw_hex'] == [None]
    assert result['items'][0]['errors'] == [{'element': 0, 'reason': 'capture_unavailable'}]


def test_size_duplicates_nonfinite_json_and_span_limits(tmp_path):
    path = saved(tmp_path, bytes(2))
    with pytest.raises(ValueError, match='outside'):
        decode_capture(path, [field()])
    for data in [b'{"provider":"snpx","provider":"snpx"}', b'{"number":NaN}']:
        path.write_bytes(data)
        with pytest.raises(ValueError):
            decode_capture(path, [field()])
    path.write_bytes(b' ' * (MAX_BYTES + 1))
    with pytest.raises(ValueError, match='256 KiB'):
        decode_capture(path, [field()])


def test_decoder_release_is_self_contained_and_stages_without_controller_contact(tmp_path):
    assert self_test()
    root = tmp_path / 'extensions'
    result = install_builtin(root, 'snpx')
    assert result['status'] == 'staged_only' and not result['controller_contacted']
    assert {row['id'] for row in load_registry(root)} == {'workbench.analysis', 'workbench.snpx'}
    release = root / 'releases/workbench.snpx/1.3.0'
    manifest, _ = load_release(release, result['manifest_sha256'])
    assert manifest['host_api'] == 1 and manifest['operations'][0]['effect'] == 'read'
    assert set(manifest['files']) == {'feature.py', 'decoder.py', 'wire.py', 'io_evidence.py', 'LICENSE.txt', 'APACHE-2.0.txt', 'NOTICE.txt'}
    with pytest.raises(ValueError, match='Unknown'):
        handle('unsupported', {}, {})


def test_live_synthetic_capture_then_offline_extension_decode(controller, tmp_path):
    port, seen, replies, _ = controller
    replies[8, 0] = struct.pack('<fi', 1.25, -12)
    path, _ = bridge_policy(tmp_path, port)
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        capture = bridge.snpx.read_words('r1', 1, 4)
        assert len(seen) == 3
        install_builtin(bridge.extensions.root, 'snpx')
        bridge.extensions.reload()
        descriptor = bridge.extensions.describe('workbench.snpx.decode_words')
        result = bridge.extensions.run_read(descriptor['id'], {'artifact_id': capture['artifact_id'], 'path': 'sample.json',
            'fields': [field(), field('i32', name='counter', word_offset=2)]}, descriptor['revision'])['result']
        assert [row['values'] for row in result['items']] == [[1.25], [-12]]
        assert result['source_sha256'] == hashlib.sha256((bridge.operations.store.root(capture['artifact_id']) / 'sample.json').read_bytes()).hexdigest()
        assert not result['controller_contacted'] and not result['mapping_verified']
    assert len(seen) == 3 and robot.connects == robot.uploads == 0


def test_same_mcp_session_adds_and_updates_decoder_without_tool_changes(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    folder = tmp_path / 'edits/fixture'
    folder.mkdir(parents=True)
    path = saved(folder, struct.pack('<fQ', 1.0, 2 ** 64 - 1))

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]
            assert len(tools) == 48

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.is_error, response
                return json.loads(response.content[0].text)

            await call('list_operations', {})
            imported = await call('import_workspace', {'source_folder': 'fixture'})
            release = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions/snpx'
            install_release(tmp_path / 'state/extensions', release)
            reloaded = await call('reload_extensions', {})
            assert not reloaded['mcp_tools_changed'] and not reloaded['controller_contacted']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.snpx.decode_words'})
            arguments = {'artifact_id': imported['artifact_id'], 'path': 'sample.json',
                         'fields': [field(), field('u64', name='serial', word_offset=2)]}
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': arguments}
            result = (await call('run_read_operation', request))['result']
            assert [row['values'] for row in result['items']] == [[1.0], ['18446744073709551615']]
            assert result['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
            # A second immutable local release activates through the same MCP session.
            updated = tmp_path / 'updated-release'
            shutil.copytree(release, updated)
            code = updated / 'feature.py'
            code.write_bytes(code.read_bytes() + b'\n# Synthetic update for reload coverage.\n')
            manifest = json.loads((updated / 'extension.json').read_text())
            manifest['version'] = '1.0.1'
            manifest['files']['feature.py'] = hashlib.sha256(code.read_bytes()).hexdigest()
            (updated / 'extension.json').write_text(json.dumps(manifest))
            install_release(tmp_path / 'state/extensions', updated)
            await call('reload_extensions', {})
            assert (await session.call_tool('run_read_operation', request)).is_error
            request['revision'] = (await call('describe_operation', {'operation_id': descriptor['id']}))['revision']
            assert (await call('run_read_operation', request))['result']['items'][0]['values'] == [1.0]
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools] == tools
            bad = {**request, 'arguments': {**arguments, 'path': '../outside.json'}}
            assert (await session.call_tool('run_read_operation', bad)).is_error
            # Captured-file tampering is refused by the host before feature dispatch.
            catalogs = json.loads((tmp_path / 'state/catalog' / (imported['artifact_id'] + '.json')).read_text())
            (Path(catalogs['root']) / 'sample.json').write_text('{}')
            assert (await session.call_tool('run_read_operation', request)).is_error

    asyncio.run(scenario())
