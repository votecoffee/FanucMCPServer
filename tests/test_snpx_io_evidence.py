import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

import pytest

from fanuc_workbench.bridge.extension_release import install_release, load_release
from fanuc_workbench.extensions.snpx_v1_2_0.wire import inspect_evidence as previous_inspector
from fanuc_workbench.extensions.snpx_v1_3_0.feature import self_test
from fanuc_workbench.services.snpx_evidence import inspect_evidence, inspect_io_evidence
from test_snpx import bridge_policy, controller
from fanuc_workbench.bridge.service import RobotBridge
from test_snpx_evidence import INIT, ACK, SESSION, READ


# Independent expected segment/index/bit vectors, including every port's boundary.
CASES = [
    ('DI[5000]', 0x48, 4992, 8, b'\x80', True, 'bool'),
    ('DO[9]', 0x46, 8, 8, b'\x01', True, 'bool'),
    ('RI[1000]', 0x48, 5992, 8, b'\x80', True, 'bool'),
    ('RO[1]', 0x46, 5000, 8, b'\x01', True, 'bool'),
    ('UI[1]', 0x48, 6000, 8, b'\x01', True, 'bool'),
    ('UO[1000]', 0x46, 6992, 8, b'\x80', True, 'bool'),
    ('GI[1000]', 0x0C, 999, 1, b'\xff\xff', 65535, 'u16'),
    ('GO[1]', 0x0A, 0, 1, b'\x00\x80', -32768, 'i16'),
    ('AI[1000]', 0x0C, 1999, 1, b'\x00\x00', 0, 'u16'),
    ('AO[1]', 0x0A, 1000, 1, b'\xff\xff', -1, 'i16'),
]


def evidence(cases):
    wire = [{'request_hex': INIT.hex(), 'response_hex': ACK.hex()},
            {'request_hex': SESSION.hex(), 'response_hex': SESSION.hex()}]
    values, formats = {}, {}
    for index, (name, segment, start, count, payload, value, format_name) in enumerate(cases, 2):
        request = bytearray(READ)
        struct.pack_into('<H', request, 2, index)
        request[30], request[43] = index, segment
        struct.pack_into('<HH', request, 44, start, count)
        response = bytearray(56)
        struct.pack_into('<HH', response, 0, 3, index)
        response[30:32], response[40:42] = bytes((index, 0xD4)), bytes((1, 1))
        response[44:44 + len(payload)] = payload
        wire.append({'request_hex': request.hex(), 'response_hex': response.hex()})
        values[name], formats[name] = value, format_name
    return {'provider': 'snpx', 'timestamp': 0, 'robot_id': 'r1', 'host': '192.0.2.1', 'port': 60008,
            'values': values, 'value_formats': formats, 'errors': {}, 'scaling_applied': False}, wire


def saved(tmp_path, cases=CASES, *, capture_change=None, wire_change=None, bind_hash=True):
    capture, wire = evidence(cases)
    if wire_change:
        wire_change(wire)
    wire_path = tmp_path / 'wire.json'
    wire_path.write_text(json.dumps(wire), encoding='utf-8')
    if bind_hash:
        capture['wire_sha256'] = hashlib.sha256(wire_path.read_bytes()).hexdigest()
    if capture_change:
        capture_change(capture)
    capture_path = tmp_path / 'sample.json'
    capture_path.write_text(json.dumps(capture), encoding='utf-8')
    return capture_path, wire_path


def names(cases=CASES):
    return [row[0] for row in cases]


def test_all_ports_decode_exact_bits_and_signed_unsigned_words_with_explicit_limits(tmp_path):
    paths = saved(tmp_path)
    result = inspect_io_evidence(*paths, names())
    assert result['all_values_match_under_supplied_order'] and result['request_order_matches_supplied_expressions']
    assert result['summary'] == {'matched': 10, 'mismatch': 0, 'unavailable': 0}
    assert [row['decoded_wire_value'] for row in result['items']] == [row[5] for row in CASES]
    assert result['io_value_binding_checked'] and not result['expression_order_verified']
    assert not result['value_format_metadata_verified'] and not result['mapping_verified']
    assert not result['capture_provenance_verified'] and not result['controller_compatibility_verified']
    assert not result['physical_identity_verified'] and not result['capture_is_atomic']
    assert not result['controller_contacted'] and not result['controller_written']
    assert result['wire_evidence']['zero_payload_read_exchanges'] == 1


def test_digital_neighbor_bits_and_shared_addresses_use_only_the_requested_bit(tmp_path):
    cases = [('DI[1]', 0x48, 0, 8, b'\x80', False, 'bool'),
             ('DI[8]', 0x48, 0, 8, b'\x80', True, 'bool')]
    result = inspect_io_evidence(*saved(tmp_path, cases), names(cases))
    assert result['all_values_match_under_supplied_order'] and result['shared_request_addresses']
    assert all(row['shared_request_address'] for row in result['items'])
    assert not result['expression_order_verified']
    assert [row['expected_request']['bit_offset'] for row in result['items']] == [0, 7]


def test_json_object_order_is_not_used_as_read_order_and_aliases_keep_original_keys(tmp_path):
    cases = [(' di [ 01 ] ', 0x48, 0, 8, b'\x01', True, 'bool'), CASES[6]]
    def reverse(capture):
        capture['values'] = dict(reversed(list(capture['values'].items())))
        capture['value_formats'] = dict(reversed(list(capture['value_formats'].items())))
    result = inspect_io_evidence(*saved(tmp_path, cases, capture_change=reverse), names(cases))
    assert result['all_values_match_under_supplied_order']
    assert result['items'][0]['target'] == 'DI[1]' and result['items'][0]['expression'] == ' di [ 01 ] '


@pytest.mark.parametrize('value,error,status', [(False, None, 'mismatch'), (0, None, 'unavailable'),
    (None, None, 'unavailable'), (True, '', 'unavailable'), (True, 'read failed', 'unavailable')])
def test_digital_mismatches_and_unavailable_values_are_distinct(tmp_path, value, error, status):
    def changed(capture):
        capture['values']['DO[9]'] = value
        if error is not None:
            capture['errors']['DO[9]'] = error
    result = inspect_io_evidence(*saved(tmp_path, CASES[1:2], capture_change=changed), ['DO[9]'])
    row = result['items'][0]
    assert row['status'] == status and row['decoded_wire_value'] is True
    assert not result['all_values_match_under_supplied_order']
    assert row['matches_under_supplied_order'] is (False if status == 'mismatch' else None)


@pytest.mark.parametrize('value', [False, 65535])
def test_signed_word_values_require_correct_type_and_range(tmp_path, value):
    result = inspect_io_evidence(*saved(tmp_path, CASES[7:8],
        capture_change=lambda capture: capture['values'].update({'GO[1]': value})), ['GO[1]'])
    row = result['items'][0]
    assert row['status'] == 'unavailable' and row['decoded_wire_value'] == -32768
    assert 'capture_value_type_or_range_invalid' in row['issues']


def test_request_reordering_and_extra_reads_cannot_pass_the_aggregate(tmp_path):
    paths = saved(tmp_path, CASES[:2])
    result = inspect_io_evidence(*paths, list(reversed(names(CASES[:2]))))
    assert result['summary']['unavailable'] == 2 and not result['request_order_matches_supplied_expressions']
    extra = inspect_io_evidence(*saved(tmp_path, CASES[:1], wire_change=lambda wire: wire.append(wire[-1])), names(CASES[:1]))
    assert extra['summary']['matched'] == 1 and not extra['all_values_match_under_supplied_order']
    assert extra['issues'] == ['read_count_differs_from_expression_count']


def test_failed_read_keeps_valid_prefix_and_marks_later_targets_unavailable(tmp_path):
    def fail(wire):
        reply = bytearray.fromhex(wire[3]['response_hex'])
        reply[42] = 1
        wire[3]['response_hex'] = reply.hex()
    def unavailable(capture):
        for name in names(CASES[1:3]):
            capture['values'][name] = None
            capture['errors'][name] = 'capture stopped'
    result = inspect_io_evidence(*saved(tmp_path, CASES[:3], wire_change=fail, capture_change=unavailable), names(CASES[:3]))
    assert result['summary'] == {'matched': 1, 'mismatch': 0, 'unavailable': 2}
    assert not result['all_values_match_under_supplied_order']
    assert result['items'][1]['decoded_wire_value'] is result['items'][2]['decoded_wire_value'] is None
    assert result['wire_evidence']['exchange_issue_counts']['exchange_after_failure'] == 1


def test_incomplete_handshake_and_no_reads_never_become_zeros(tmp_path):
    result = inspect_io_evidence(*saved(tmp_path, wire_change=lambda wire: wire.clear()), names())
    assert result['summary']['unavailable'] == 10 and result['read_exchanges_total'] == 0
    assert all(row['decoded_wire_value'] is None for row in result['items'])
    assert 'handshake_incomplete' in result['wire_evidence']['issues']


@pytest.mark.parametrize('bind_hash', [True, False])
def test_capture_wire_hash_must_bind_before_value_comparison(tmp_path, bind_hash):
    paths = saved(tmp_path, bind_hash=bind_hash)
    if bind_hash:
        # Changing only JSON whitespace leaves frames valid but breaks byte binding.
        paths[1].write_text(json.dumps(json.loads(paths[1].read_bytes()), indent=2))
    result = inspect_io_evidence(*paths, names())
    assert not result['all_values_match_under_supplied_order'] and result['summary']['unavailable'] == 10
    assert all(row['decoded_wire_value'] is None for row in result['items'])


def test_source_files_are_each_read_once_and_preserved(tmp_path, monkeypatch):
    paths = saved(tmp_path)
    original = [path.read_bytes() for path in paths]
    opened = []
    old_open = Path.open
    def counted(path, *args, **kwargs):
        if path in paths:
            opened.append(path)
        return old_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', counted)
    result = inspect_io_evidence(*paths, names())
    assert opened == list(paths)
    assert result['capture_source_sha256'] == hashlib.sha256(original[0]).hexdigest()
    assert result['wire_source_sha256'] == hashlib.sha256(original[1]).hexdigest()
    assert [path.read_bytes() for path in paths] == original


def test_full_100_signal_capture_uses_all_102_exchanges_and_global_pagination(tmp_path):
    cases = [(f'DI[{index}]', 0x48, (index - 1) // 8 * 8, 8, b'\x00', False, 'bool') for index in range(1, 101)]
    paths = saved(tmp_path, cases)
    result = inspect_io_evidence(*paths, names(cases), offset=99, limit=1)
    assert result['all_values_match_under_supplied_order'] and result['summary']['matched'] == 100
    assert result['items'][0]['wire_exchange'] == 101 and result['next_offset'] is None
    assert result['total'] == 100 and result['read_exchanges_total'] == 100
    assert inspect_evidence(*paths, offset=100, limit=100) == previous_inspector(*paths, offset=100, limit=100)


@pytest.mark.parametrize('expressions', [[], ['R[1]'], ['PR[1,1]'], ['DI[0]'], ['DI[5001]'],
    ['RI[1001]'], ['UI[1001]'], ['GI[1001]'], ['AO[0]'], ['DI[١]'], ['DI[1]', 'di[01]'],
    ['DI[1]'] * 101, [True], 'DI[1]'])
def test_invalid_or_duplicate_targets_are_refused_before_file_access(expressions):
    with pytest.raises(ValueError):
        inspect_io_evidence(Path('missing'), Path('missing'), expressions)


@pytest.mark.parametrize('options', [{'offset': True}, {'offset': -1}, {'limit': False}, {'limit': 0}, {'limit': 101}])
def test_invalid_pagination_is_refused_before_file_access(options):
    with pytest.raises(ValueError):
        inspect_io_evidence(Path('missing'), Path('missing'), names(), **options)


@pytest.mark.parametrize('changes', [{'provider': 'other'}, {'address': 1}, {'scaling_applied': True},
    {'values': {}}, {'value_formats': {}}, {'errors': {'unknown': 'failed'}}, {'errors': {'DO[9]': None}},
    {'values': {'DO[9]': 'ON'}}, {'values': {'DO[9]': float('inf')}}, {'values': {'DO[9]': 65536}},
    {'value_formats': {'DO[9]': 'u16'}}, {'value_formats': {'DO[9]': ['bool']}}])
def test_invalid_capture_shapes_and_format_metadata_are_refused(tmp_path, changes):
    paths = saved(tmp_path, CASES[1:2], capture_change=lambda capture: capture.update(changes))
    with pytest.raises(ValueError):
        inspect_io_evidence(*paths, ['DO[9]'])


def test_simulated_reader_capture_can_be_checked_without_another_socket(controller, tmp_path):
    port, seen, replies, _ = controller
    replies[0x48, 0], replies[0x0C, 0], replies[0x0A, 1000] = b'\x01', b'\xff\xff', b'\x00\x80'
    expressions = ['DI[1]', 'GI[1]', 'AO[1]']
    path, _ = bridge_policy(tmp_path, port, snpx_read={'port': port, 'io_targets': expressions,
        'io_word_formats': {'GI[1]': 'u16', 'AO[1]': 'i16'}})
    with RobotBridge.from_config(path) as bridge:
        sample = bridge.operations.sample('r1', expressions)
        root = bridge.operations.store.verify(sample['artifact_id'])
        before = len(seen)
        result = inspect_io_evidence(root / 'sample.json', root / 'wire.json', expressions)
        assert result['all_values_match_under_supplied_order'] and len(seen) == before == 5


def test_immutable_release_preserves_existing_operations_and_component_licenses():
    root = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'
    previous, _ = load_release(root / 'snpx_v1_2_0')
    current, _ = load_release(root / 'snpx_v1_3_0')
    assert self_test() and current['version'] == '1.3.0'
    assert current['operations'][:3] == previous['operations']
    assert {'LICENSE.txt', 'APACHE-2.0.txt', 'NOTICE.txt', 'io_evidence.py'} <= current['files'].keys()


def test_same_mcp_session_loads_io_checker_and_rolls_back_with_the_same_48_tools(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    fixture = tmp_path / 'edits/fixture'
    fixture.mkdir(parents=True)
    saved(fixture)
    releases = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.is_error, result
                return json.loads(result.content[0].text)

            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_release(tmp_path / 'state/extensions', releases / 'snpx_v1_2_0')
            await call('reload_extensions', {})
            install_release(tmp_path / 'state/extensions', releases / 'snpx_v1_3_0')
            assert not (await call('reload_extensions', {}))['mcp_tools_changed']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.snpx.inspect_io_evidence'})
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': {
                'artifact_id': imported['artifact_id'], 'capture_path': 'sample.json', 'wire_path': 'wire.json', 'expressions': names()}}
            result = (await call('run_read_operation', request))['result']
            assert result['summary']['matched'] == 10 and not result['expression_order_verified']
            assert (await session.call_tool('run_read_operation', {**request,
                'arguments': {**request['arguments'], 'wire_path': '../outside'}})).is_error
            (Path(imported['root']) / 'wire.json').write_text('[]')
            assert (await session.call_tool('run_read_operation', request)).is_error
            install_release(tmp_path / 'state/extensions', releases / 'snpx_v1_2_0')
            await call('reload_extensions', {})
            assert (await session.call_tool('run_read_operation', request)).is_error
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools] == tools and len(tools) == 48
    asyncio.run(scenario())
