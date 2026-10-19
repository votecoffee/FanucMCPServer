import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from fanuc_workbench.bridge.extension_release import load_registry, load_release
from fanuc_workbench.bridge.extensions_cli import install_builtin
from fanuc_workbench.extensions.recording.feature import MAX_BYTES, handle, self_test
from fanuc_workbench.services.recording_analysis import summarize_recording
from test_bridge_operations import bridge_case, finished


def saved(tmp_path, samples, **extra):
    path = tmp_path / 'recording.json'
    path.write_text(json.dumps({'samples': samples, **extra}), encoding='utf-8')
    return path


def sample(timestamp, values, errors=None):
    return {'timestamp': timestamp, 'values': values, 'errors': errors or {}}


def test_availability_separates_adjacent_changes_from_unknown_gap_transitions(tmp_path):
    path = saved(tmp_path, [
        sample(10, {'DI[1]': False, 'R[1]': 10}),
        sample(12, {'DI[1]': False, 'R[1]': 11}, {'DI[1]': 'read failed'}),
        sample(14, {'DI[1]': None, 'R[1]': 0}, {'R[1]': 'bad value'}),
        sample(20, {'DI[1]': True}),
        sample(22, {'DI[1]': False, 'R[1]': 15}),
    ], sample_artifacts=[f'{index:032x}' for index in range(5)],
       events=[{'signal': 'DI[1]', 'old': False, 'new': True}], profile_seconds={'invented': 999})
    original = path.read_bytes()
    result = summarize_recording(path, gap_threshold_seconds=3)
    digital, register = result['items']
    assert digital['available_samples'] == 3 and digital['unavailable_samples'] == 2
    assert digital['unavailable_reasons'] == {'error': 1, 'null': 1}
    assert digital['available_segments'] == 2 and digital['longest_unavailable_run'] == 2
    assert digital['adjacent_changes'] == digital['changed_after_gap'] == 1
    assert digital['first_value'] is False and digital['last_value'] is False
    assert digital['numeric_samples'] == 0 and digital['numeric_min'] is None
    assert register['unavailable_reasons'] == {'error': 1, 'missing': 1}
    assert register['numeric_samples'] == 3 and register['numeric_min'] == 10 and register['numeric_max'] == 15
    assert register['first_error'] == 'bad value'
    assert result['availability'] == {'available_observations': 6, 'unavailable_observations': 4,
                                      'adjacent_changes': 2, 'changed_after_gap': 2}
    assert result['timestamps']['duration_seconds'] == 12
    assert result['timestamps']['interval_median_seconds'] == 2
    assert result['timestamps']['intervals_exceeding_threshold'] == 1
    assert result['sample_references']['count_matches_samples']
    assert not result['sample_references']['contents_verified']
    assert not result['derived_events_used'] and not result['recording_completion_verified']
    assert not result['controller_contacted'] and not result['wire_evidence_checked']
    assert result['source_sha256'] == hashlib.sha256(original).hexdigest() and path.read_bytes() == original


def test_values_are_typed_and_large_integers_stay_exact(tmp_path):
    big = 2 ** 63 + 123
    path = saved(tmp_path, [sample(0, {'A': True, 'B': {'J1': big, 'J2': [big]}}),
                            sample(2, {'A': 1, 'B': {'J2': [big], 'J1': big}}),
                            sample(4, {'A': 1.0, 'B': big})])
    result = summarize_recording(path)
    first, second = result['items']
    assert first['adjacent_changes'] == 2 and first['numeric_samples'] == 2
    assert second['adjacent_changes'] == 1
    assert second['first_value'] == {'J1': str(big), 'J2': [str(big)]}
    assert second['last_value'] == second['numeric_min'] == second['numeric_max'] == str(big)
    json.dumps(result, allow_nan=False)


def test_missing_error_only_signals_and_recovery_with_same_value(tmp_path):
    path = saved(tmp_path, [sample(0, {'A': 'ON'}, {'B': ''}), sample(2, {}), sample(4, {'A': 'ON'})])
    first, second = summarize_recording(path)['items']
    assert first['available_segments'] == 2 and first['changed_after_gap'] == first['adjacent_changes'] == 0
    assert second['available_samples'] == 0 and second['unavailable_samples'] == 3
    assert second['first_error'] == '' and second['first_value'] is None
    assert second['availability_fraction'] == 0 and second['longest_unavailable_run'] == 3


@pytest.mark.parametrize('times,monotonic,duration,duplicates,regressions', [
    ([], False, None, 0, 0), ([0], True, 0, 0, 0), ([2, 2, 4], True, 2, 1, 0),
    ([4, 1, 3], False, None, 0, 1)])
def test_partial_empty_duplicate_and_regressing_clocks(tmp_path, times, monotonic, duration, duplicates, regressions):
    result = summarize_recording(saved(tmp_path, [sample(time, {'A': 0}) for time in times]),
                                 gap_threshold_seconds=1)
    clock = result['timestamps']
    assert clock['monotonic'] == monotonic and clock['duration_seconds'] == duration
    assert clock['duplicates'] == duplicates and clock['regressions'] == regressions
    if not monotonic:
        assert clock['interval_min_seconds'] is None and clock['intervals_exceeding_threshold'] is None
    assert not clock['clock_source_verified']
    assert not result['recording_completion_verified']


def test_pagination_filters_and_global_summary_cover_all_signals(tmp_path):
    path = saved(tmp_path, [sample(0, {'B': 1, 'A': 0}), sample(2, {'B': 2, 'A': 0})])
    first = summarize_recording(path, limit=1)
    second = summarize_recording(path, offset=1, limit=1)
    assert first['total'] == 2 and first['next_offset'] == 1 and first['items'][0]['signal'] == 'A'
    assert second['next_offset'] is None and second['items'][0]['signal'] == 'B'
    assert first['availability'] == second['availability']
    filtered = summarize_recording(path, signal='A')
    assert filtered['total'] == 1 and filtered['signals_total'] == 2
    assert filtered['availability']['adjacent_changes'] == 1
    assert summarize_recording(path, signal='unknown')['items'] == []
    assert summarize_recording(path, offset=99)['items'] == []


def test_references_are_metadata_only_and_alignment_is_reported(tmp_path):
    path = saved(tmp_path, [sample(0, {'A': 0})], sample_artifacts=['a' * 32, 'a' * 32])
    result = summarize_recording(path)
    refs = result['sample_references']
    assert refs['count'] == 2 and refs['unique_count'] == 1
    assert not refs['count_matches_samples'] and not refs['contents_verified']
    assert not result['capture_provenance_verified']


@pytest.mark.parametrize('options', [{'offset': True}, {'offset': -1}, {'limit': 0}, {'limit': 101},
    {'limit': False}, {'signal': None}, {'signal': 'A' * 129}, {'gap_threshold_seconds': 0},
    {'gap_threshold_seconds': 86401}, {'gap_threshold_seconds': True},
    {'gap_threshold_seconds': float('nan')}, {'gap_threshold_seconds': 10 ** 1000}])
def test_invalid_options_are_refused_before_opening_files(options):
    with pytest.raises(ValueError):
        summarize_recording(Path('does-not-exist'), **options)


@pytest.mark.parametrize('document', [[], {}, {'samples': {}}, {'samples': [None]},
    {'samples': [{'timestamp': True, 'values': {}}]},
    {'samples': [{'timestamp': 10 ** 13, 'values': {}}]},
    {'samples': [{'timestamp': 1, 'values': [], 'errors': {}}]},
    {'samples': [{'timestamp': 1, 'values': {}, 'errors': {'A': None}}]},
    {'samples': [sample(0, {'': 1})]}, {'samples': [sample(0, {'A' * 129: 1})]},
    {'samples': [sample(0, {'A': 'x' * 1025})]}, {'samples': [sample(0, {'A': 2 ** 64})]},
    {'samples': [sample(0, {'A': [0] * 65})]},
    {'samples': [sample(0, {'A': [[[[[0]]]]]})]},
    {'samples': [sample(0, {'A': ['x' * 1000] * 5})]},
    {'samples': [sample(0, {}, {'A': 'x' * 1025})]},
    {'samples': [sample(0, {})] * 301}, {'samples': [sample(0, {f'A{i}': 0 for i in range(257)})]},
    {'samples': [], 'sample_artifacts': '../state/catalog'},
    {'samples': [], 'sample_artifacts': ['../outside']},
    {'samples': [], 'sample_artifacts': [True]},
    {'samples': [], 'sample_artifacts': ['a' * 32] * 301}])
def test_unsupported_shapes_and_bounds_fail_closed(tmp_path, document):
    path = tmp_path / 'recording.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError):
        summarize_recording(path)


@pytest.mark.parametrize('data', [b'{"samples":[],"samples":[]}',
    b'{"samples":[{"timestamp":NaN,"values":{}}]}',
    b'{"samples":[{"timestamp":1,"values":{"A":Infinity}}]}',
    b'{"samples":[{"timestamp":1,"values":{"A":1e999}}]}', b'\xff', b'{',
    b'{"samples":' + b'[' * 2000 + b']' * 2000 + b'}', b' ' * (MAX_BYTES + 1)],
    ids=['duplicate-key', 'nan-time', 'infinite-value', 'overflow-value', 'encoding', 'syntax', 'nesting', 'size'])
def test_invalid_json_encoding_nonfinite_and_excessive_input_are_refused(tmp_path, data):
    path = tmp_path / 'recording.json'
    path.write_bytes(data)
    with pytest.raises(ValueError):
        summarize_recording(path)


def test_maximum_recording_is_bounded_and_page_summaries_stay_global(tmp_path):
    values = {f'A{index:03d}': index for index in range(256)}
    path = saved(tmp_path, [sample(index * 2, values) for index in range(300)])
    result = summarize_recording(path)
    assert result['samples_total'] == 300 and result['signals_total'] == 256
    assert len(result['items']) == 100 and result['next_offset'] == 100
    assert result['availability']['available_observations'] == 76800
    assert len(json.dumps(result)) < 200000


def test_saved_watch_uses_shared_summary_without_additional_robot_access(bridge_case):
    bridge, robot, _ = bridge_case
    job = bridge.operations.start_watch('r1', ['R[1]'], samples=1)
    status = finished(bridge.operations.jobs, job['job_id'])
    assert status['status'] == 'complete'
    connects = robot.connects
    install_builtin(bridge.extensions.root, 'recording')
    bridge.extensions.reload()
    descriptor = bridge.extensions.describe('workbench.recording.summarize')
    result = bridge.extensions.run_read(descriptor['id'], {'artifact_id': status['artifact_id'],
        'path': 'recording.json'}, descriptor['revision'])
    assert result['result']['items'][0]['first_value'] == '42'
    assert result['result']['sample_references']['count_matches_samples']
    assert robot.connects == connects and robot.uploads == 0


def test_self_contained_release_staging_and_handler_contract(tmp_path):
    assert self_test()
    result = install_builtin(tmp_path, 'recording')
    assert result['status'] == 'staged_only' and not result['controller_contacted']
    assert {row['id'] for row in load_registry(tmp_path)} == {'workbench.analysis', 'workbench.recording'}
    manifest, _ = load_release(tmp_path / 'releases/workbench.recording/1.0.0', result['manifest_sha256'])
    assert set(manifest['files']) == {'feature.py', 'LICENSE.txt'}
    assert manifest['operations'][0]['effect'] == 'read'
    with pytest.raises(ValueError, match='Unknown'):
        handle('unknown', {}, {})


def test_same_mcp_session_loads_summary_with_48_tools_and_checks_artifact_hashes(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    fixture = tmp_path / 'edits/fixture'
    fixture.mkdir(parents=True)
    saved(fixture, [sample(0, {'DI[1]': False}), sample(2, {'DI[1]': True})])

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump() for tool in (await session.list_tools()).tools]

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.isError, response
                return json.loads(response.content[0].text)

            assert (await call('list_operations', {}))['total'] == 33
            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_builtin(tmp_path / 'state/extensions', 'recording')
            assert (await call('reload_extensions', {}))['status'] == 'reloaded'
            descriptor = await call('describe_operation', {'operation_id': 'workbench.recording.summarize'})
            arguments = {'artifact_id': imported['artifact_id'], 'path': 'recording.json'}
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': arguments}
            result = (await call('run_read_operation', request))['result']
            assert result['items'][0]['adjacent_changes'] == 1 and not result['controller_contacted']
            assert (await call('list_operations', {}))['total'] == 34
            assert [tool.model_dump() for tool in (await session.list_tools()).tools] == tools
            assert len(tools) == 48
            assert (await session.call_tool('run_read_operation', {**request,
                'arguments': {**arguments, 'path': '../outside.json'}})).isError
            assert (await session.call_tool('run_read_operation', {**request, 'revision': 'stale'})).isError
            (Path(imported['root']) / 'recording.json').write_text('{}')
            assert (await session.call_tool('run_read_operation', request)).isError
            assert (await call('capabilities', {}))['controller_access']['read_only']

    asyncio.run(scenario())
