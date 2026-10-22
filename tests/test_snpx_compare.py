import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys

import pytest

from fanuc_workbench.bridge.extension_release import install_release, load_release
from fanuc_workbench.extensions.snpx_v1_1_0.feature import self_test
from fanuc_workbench.services.snpx_decode import compare_captures
from test_snpx_decode import document, field


ORIGIN = {'robot_id': 'test_robot', 'host': '192.0.2.1', 'port': 60008}


def captures(tmp_path, before, after, *, before_fields=None, after_fields=None):
    paths = []
    for name, raw, extra in [('before', before, before_fields), ('after', after, after_fields)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps({**document(raw), **ORIGIN, **(extra or {})}), encoding='utf-8')
        paths.append(path)
    return paths


@pytest.mark.parametrize('kind,code,left,right,delta', [
    ('u16', 'H', 0, 65535, 65535), ('i16', 'h', 32767, -32768, -65535),
    ('u32', 'I', 0, 4294967295, 4294967295), ('i32', 'i', -2147483648, 2147483647, 4294967295),
    ('u64', 'Q', 18446744073709551614, 18446744073709551615, '1'),
    ('i64', 'q', -9223372036854775808, 9223372036854775807, '18446744073709551615')])
def test_integer_changes_and_deltas_are_exact_even_with_large_float_tolerances(tmp_path, kind, code, left, right, delta):
    paths = captures(tmp_path, struct.pack('<' + code, left), struct.pack('<' + code, right))
    original = [path.read_bytes() for path in paths]
    result = compare_captures(*paths, [field(kind)], absolute_tolerance=1e100, relative_tolerance=1)
    row = result['items'][0]
    assert row['status'] == 'changed' and row['delta'] == delta and row['raw_changed']
    assert row['before_value'] == (str(left) if kind.endswith('64') else left)
    assert row['after_value'] == (str(right) if kind.endswith('64') else right)
    assert result['summary'] == {'changed': 1, 'unchanged': 0, 'unavailable': 0}
    assert result['comparison_complete'] and result['configured_origin_matches']
    assert not result['mapping_verified'] and not result['capture_provenance_verified']
    for side, data in zip(['before', 'after'], original):
        assert result[side]['source_sha256'] == hashlib.sha256(data).hexdigest()
    assert [path.read_bytes() for path in paths] == original
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('options,changed', [({}, True), ({'absolute_tolerance': .01}, False),
                                         ({'relative_tolerance': .001}, False)])
def test_float_tolerance_never_hides_raw_word_changes(tmp_path, options, changed):
    paths = captures(tmp_path, struct.pack('<f', 10), struct.pack('<f', 10.005))
    result = compare_captures(*paths, [field()], **options)
    assert bool(result['summary']['changed']) == changed
    assert result['raw_changed_word_addresses']
    assert result['total'] == (1 if changed else 0)
    full = compare_captures(*paths, [field()], changes_only=False, **options)
    assert full['items'][0]['raw_changed'] and full['items'][0]['delta'] > 0


def test_signed_zero_is_numerically_unchanged_but_bit_change_is_retained(tmp_path):
    paths = captures(tmp_path, struct.pack('<f', -0.0), struct.pack('<f', 0.0))
    result = compare_captures(*paths, [field()])
    assert result['summary']['unchanged'] == 1 and result['total'] == 0
    assert result['raw_changed_word_addresses'] == [101]


def test_delta_overflow_is_null_without_breaking_json(tmp_path):
    maximum = sys.float_info.max
    paths = captures(tmp_path, struct.pack('<d', -maximum), struct.pack('<d', maximum))
    result = compare_captures(*paths, [field('f64')])
    row = result['items'][0]
    assert row['status'] == 'changed' and row['delta'] is None and row['delta_error'] == 'nonfinite_delta'
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('value', [math.inf, -math.inf, math.nan])
def test_nonfinite_readings_remain_unavailable_even_when_bytes_match(tmp_path, value):
    raw = struct.pack('<f', value)
    result = compare_captures(*captures(tmp_path, raw, raw), [field()])
    assert not result['comparison_complete'] and result['summary']['unavailable'] == 1
    assert result['raw_changed_word_addresses'] == [] and result['total'] == 1
    assert result['items'][0]['before_error'] == result['items'][0]['after_error'] == 'nonfinite_float'
    assert result['items'][0]['delta'] is None


def test_failed_capture_is_visible_with_no_invented_raw_comparison(tmp_path):
    paths = captures(tmp_path, bytes(4), bytes(4), after_fields={
        'raw_hex': None, 'words_u16': None, 'words_i16': None, 'errors': {'words': 'unavailable'}})
    result = compare_captures(*paths, [field()])
    assert result['raw_changed_word_addresses'] is None and not result['comparison_complete']
    row = result['items'][0]
    assert row['status'] == 'unavailable' and row['before_value'] == 0 and row['after_value'] is None
    assert row['after_error'] == 'capture_unavailable' and row['raw_changed'] is None


def test_pagination_counts_all_elements_and_retains_unselected_word_changes(tmp_path):
    before = struct.pack('<4H', 10, 20, 30, 40)
    after = struct.pack('<4H', 11, 20, 29, 41)
    paths = captures(tmp_path, before, after)
    selected = [field('u16', count=3)]
    first = compare_captures(*paths, selected, limit=1)
    assert first['summary'] == {'changed': 2, 'unchanged': 1, 'unavailable': 0}
    assert first['elements_total'] == 3 and first['total'] == 2 and first['next_offset'] == 1
    assert first['items'][0]['word_address'] == 100 and first['items'][0]['delta'] == 1
    assert first['raw_changed_word_addresses'] == [100, 102, 103]
    second = compare_captures(*paths, selected, offset=1, limit=1)
    assert second['next_offset'] is None and second['items'][0]['word_address'] == 102
    all_rows = compare_captures(*paths, selected, changes_only=False)
    assert all_rows['total'] == 3 and all_rows['items'][1]['delta'] == 0


@pytest.mark.parametrize('extra', [{'robot_id': 'other'}, {'host': '192.0.2.2'}, {'port': 60009},
    {'robot_id': None}, {'host': ''}, {'host': ' host '}, {'port': True}, {'port': 0},
    {'address': 101}, {'count': 1, 'raw_hex': '0000', 'words_u16': [0], 'words_i16': [0]}])
def test_different_or_incomplete_origins_and_spans_are_refused(tmp_path, extra):
    paths = captures(tmp_path, bytes(4), bytes(4), after_fields=extra)
    with pytest.raises(ValueError):
        compare_captures(*paths, [field('u16')])


def test_configured_hostname_comparison_does_not_require_dns(tmp_path):
    paths = captures(tmp_path, bytes(2), bytes(2), before_fields={'host': 'ROBOT.EXAMPLE'}, after_fields={'host': 'robot.example'})
    result = compare_captures(*paths, [field('u16')])
    assert result['configured_origin']['host'] == 'robot.example' and not result['controller_contacted']


@pytest.mark.parametrize('options', [{'absolute_tolerance': -1}, {'absolute_tolerance': True},
    {'absolute_tolerance': math.inf}, {'absolute_tolerance': math.nan}, {'absolute_tolerance': 10 ** 1000},
    {'relative_tolerance': -1}, {'relative_tolerance': 2}, {'relative_tolerance': math.nan},
    {'changes_only': 1}, {'offset': True}, {'offset': -1}, {'limit': 0}, {'limit': 101}])
def test_invalid_options_are_refused_before_file_access(options):
    with pytest.raises(ValueError):
        compare_captures(Path('missing-before'), Path('missing-after'), [field()], **options)


def test_release_keeps_legacy_decode_contract_and_self_tests():
    assert self_test()
    root = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'
    old, _ = load_release(root / 'snpx')
    new, _ = load_release(root / 'snpx_v1_1_0')
    assert old['version'] == '1.0.0' and new['version'] == '1.1.0'
    assert new['operations'][0] == old['operations'][0]
    assert {row['id'] for row in new['operations']} == {'workbench.snpx.decode_words', 'workbench.snpx.compare_words'}


def test_same_mcp_session_upgrades_and_rolls_back_without_host_changes(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    fixture = tmp_path / 'edits/fixture'
    fixture.mkdir(parents=True)
    captures(fixture, struct.pack('<fQ', 1, 2 ** 64 - 2), struct.pack('<fQ', 2, 2 ** 64 - 1))
    releases = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]
            assert len(tools) == 55

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.is_error, response
                return json.loads(response.content[0].text)

            await call('list_operations', {})  # Seed standard analyses first.
            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_release(tmp_path / 'state/extensions', releases / 'snpx')
            await call('reload_extensions', {})
            old_decode = await call('describe_operation', {'operation_id': 'workbench.snpx.decode_words'})
            decode_args = {'artifact_id': imported['artifact_id'], 'path': 'before.json', 'fields': [field()]}
            old_request = {'operation_id': old_decode['id'], 'revision': old_decode['revision'], 'arguments': decode_args}
            assert (await call('run_read_operation', old_request))['result']['items'][0]['values'] == [1.0]
            install_release(tmp_path / 'state/extensions', releases / 'snpx_v1_1_0')
            reloaded = await call('reload_extensions', {})
            assert not reloaded['mcp_tools_changed'] and not reloaded['controller_contacted']
            assert (await session.call_tool('run_read_operation', old_request)).is_error
            descriptor = await call('describe_operation', {'operation_id': 'workbench.snpx.compare_words'})
            args = {'before_artifact_id': imported['artifact_id'], 'before_path': 'before.json',
                    'after_artifact_id': imported['artifact_id'], 'after_path': 'after.json',
                    'fields': [field(), field('u64', name='counter', word_offset=2)]}
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': args}
            compared = (await call('run_read_operation', request))['result']
            assert [row['delta'] for row in compared['items']] == [1.0, '1']
            assert compared['summary']['changed'] == 2 and not compared['controller_contacted']
            new_decode = await call('describe_operation', {'operation_id': old_decode['id']})
            assert (await call('run_read_operation', {**old_request, 'revision': new_decode['revision']}))['result']['items'][0]['values'] == [1.0]
            # Rollback selects preserved bytes; no client/host restart or tool refresh.
            install_release(tmp_path / 'state/extensions', releases / 'snpx')
            await call('reload_extensions', {})
            assert (await session.call_tool('run_read_operation', request)).is_error
            assert (await call('run_read_operation', old_request))['result']['items'][0]['values'] == [1.0]
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools] == tools

    asyncio.run(scenario())
