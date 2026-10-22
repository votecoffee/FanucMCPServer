import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from fanuc_workbench.bridge.extension_release import install_release, load_release
from fanuc_workbench.extensions.recording_v1_2_0.feature import self_test
from fanuc_workbench.services.recording_analysis import analyze_handshake


def rule(**changes):
    return {'name': 'cycle', 'request_signal': 'DO[1]', 'ack_signal': 'DI[1]',
            'request_active': True, 'request_inactive': False, 'ack_active': True, 'ack_inactive': False,
            'timeout_seconds': 5, **changes}


def saved(tmp_path, states, *, times=None, errors=None, extra=None):
    samples = [{'timestamp': times[index] if times is not None else index * 2,
                'values': {'DO[1]': request, 'DI[1]': ack}, 'errors': (errors or {}).get(index, {})}
               for index, (request, ack) in enumerate(states)]
    path = tmp_path / 'recording.json'
    path.write_text(json.dumps({'samples': samples, **(extra or {})}), encoding='utf-8')
    return path


def test_separate_observed_edges_have_nominal_polling_range_and_source_hash(tmp_path):
    path = saved(tmp_path, [(False, False), (True, False), (True, False), (True, True)])
    original = path.read_bytes()
    result = analyze_handshake(path, [rule()])
    event = result['items'][0]
    assert event['status'] == 'ack_observed' and event['request_edge_observed']
    assert event['request_interval_indices'] == [0, 1] and event['ack_interval_indices'] == [2, 3]
    assert event['observed_delay_seconds'] == 4
    assert event['nominal_delay_range_seconds'] == {'minimum': 2, 'maximum': 6}
    assert event['threshold_relation'] == 'threshold_ambiguous'
    assert result['rules'][0]['known_pair_samples'] == 4
    assert result['request_edges_observed'] == 1 and result['clock']['strictly_increasing']
    assert result['source_sha256'] == hashlib.sha256(original).hexdigest() and path.read_bytes() == original
    assert not result['continuous_monitoring_verified'] and not result['causation_verified']
    assert not result['physical_timing_verified'] and not result['capture_is_atomic']
    assert not result['controller_contacted'] and not result['controller_written']


@pytest.mark.parametrize('timeout,relation', [(6, 'within_threshold'), (5, 'threshold_ambiguous'),
                                           (2, 'threshold_ambiguous'), (1, 'beyond_threshold')])
def test_threshold_uses_full_nominal_range_not_only_first_active_timestamp_delta(tmp_path, timeout, relation):
    path = saved(tmp_path, [(False, False), (True, False), (True, False), (True, True)])
    result = analyze_handshake(path, [rule(timeout_seconds=timeout)])
    assert result['items'][0]['threshold_relation'] == relation


@pytest.mark.parametrize('states,status', [
    ([(False, False), (True, True)], 'request_and_ack_coobserved'),
    ([(False, False), (True, False), (False, True)], 'ack_and_request_clear_coobserved'),
    ([(False, True), (True, True)], 'ack_not_inactive_at_request_baseline'),
    ([(False, True), (True, False)], 'ack_not_inactive_at_request_baseline'),
    ([(False, None), (True, False)], 'ack_baseline_unknown'),
    ([(False, False), (True, False), (False, False)], 'request_cleared_before_ack_observed'),
    ([(False, False), (True, False)], 'open_at_recording_end'),
])
def test_ambiguous_and_incomplete_requests_are_never_reported_as_fresh_acks(tmp_path, states, status):
    result = analyze_handshake(saved(tmp_path, states), [rule()])
    event = result['items'][0]
    assert event['status'] == status and event['request_edge_observed']
    assert event['nominal_delay_range_seconds'] is None and event['threshold_relation'] == 'unavailable'
    assert event['observed_delay_seconds'] is None


def test_initial_active_state_has_no_observed_edge_or_latency(tmp_path):
    path = saved(tmp_path, [(True, False), (True, True)])
    result = analyze_handshake(path, [rule()])
    assert result['total'] == 1 and result['request_edges_observed'] == 0
    event = result['items'][0]
    assert event['status'] == 'request_active_without_observed_edge'
    assert event['request_interval_indices'] is None and event['observed_wait_seconds'] is None


@pytest.mark.parametrize('gap_kind', ['null', 'error', 'empty-error', 'unexpected', 'missing'])
def test_gaps_end_pending_request_and_never_attach_later_ack(tmp_path, gap_kind):
    path = saved(tmp_path, [(False, False), (True, False), (True, False), (True, True)], times=[0, 2, 50, 60])
    document = json.loads(path.read_bytes())
    gap = document['samples'][2]
    if gap_kind == 'null':
        gap['values']['DI[1]'] = None
    elif gap_kind in {'error', 'empty-error'}:
        gap['errors']['DI[1]'] = '' if gap_kind == 'empty-error' else 'unavailable'
    elif gap_kind == 'unexpected':
        gap['values']['DI[1]'] = 'unknown'
    else:
        del gap['values']['DI[1]']
    path.write_text(json.dumps(document))
    result = analyze_handshake(path, [rule()])
    assert result['status_counts'] == {'evidence_gap': 1, 'request_active_without_observed_edge': 1}
    assert result['request_edges_observed'] == 1
    assert result['items'][0]['observed_wait_seconds'] == 0
    assert result['items'][0]['wait_through_index'] == 1
    assert all(row['nominal_delay_range_seconds'] is None for row in result['items'])
    assert result['rules'][0]['unknown_pair_samples'] == 1


def test_immediate_gap_and_repeated_missing_ack_do_not_invent_elapsed_wait_or_duplicate_boundaries(tmp_path):
    path = saved(tmp_path, [(False, False), (True, None), (True, None), (True, None), (True, False)])
    result = analyze_handshake(path, [rule()])
    assert result['status_counts'] == {'evidence_gap': 1, 'request_active_without_observed_edge': 1}
    assert result['items'][0]['observed_wait_seconds'] is None and result['items'][0]['wait_through_index'] is None


def test_new_request_after_gap_can_be_observed_after_a_fresh_inactive_baseline(tmp_path):
    path = saved(tmp_path, [(False, False), (True, False), (None, None), (False, False),
                            (True, False), (True, True)])
    result = analyze_handshake(path, [rule()])
    assert result['status_counts'] == {'evidence_gap': 1, 'ack_observed': 1}
    assert result['request_edges_observed'] == 2
    assert result['items'][1]['request_first_active_index'] == 4


def test_open_request_reports_observed_wait_without_claiming_continuous_timeout(tmp_path):
    path = saved(tmp_path, [(False, False), (True, False), (True, False), (True, False)], times=[0, 2, 4, 20])
    result = analyze_handshake(path, [rule()])
    event = result['items'][0]
    assert event['status'] == 'open_at_recording_end' and event['observed_wait_seconds'] == 18
    assert event['observed_wait_exceeds_threshold'] is True
    assert not result['recording_completion_verified'] and not result['continuous_monitoring_verified']


@pytest.mark.parametrize('times,duplicates,regressions', [([0, 2, 2], 1, 0), ([4, 2, 6], 0, 1)])
def test_bad_clock_preserves_state_observations_but_disables_all_timing(tmp_path, times, duplicates, regressions):
    result = analyze_handshake(saved(tmp_path, [(False, False), (True, False), (True, True)], times=times), [rule()])
    assert result['items'][0]['status'] == 'ack_observed'
    assert result['clock'] == {'strictly_increasing': False, 'duplicates': duplicates,
                               'regressions': regressions, 'source_verified': False}
    assert result['items'][0]['nominal_delay_range_seconds'] is None
    assert result['items'][0]['observed_wait_seconds'] is None


def test_explicit_string_and_numeric_states_are_supported_without_boolean_coercion(tmp_path):
    path = saved(tmp_path, [('OFF', 0), ('ON', 0), ('ON', 1)])
    result = analyze_handshake(path, [rule(request_active='ON', request_inactive='OFF', ack_active=1, ack_inactive=0)])
    assert result['items'][0]['status'] == 'ack_observed'
    strict = analyze_handshake(saved(tmp_path, [(False, False), (1, False), (True, True)]), [rule()])
    assert strict['request_edges_observed'] == 0
    assert strict['rules'][0]['unavailable_reasons'] == {'request_other_state': 1}


def test_multiple_rules_and_pagination_keep_global_counts_and_stable_order(tmp_path):
    path = saved(tmp_path, [(False, False), (True, False), (True, True)])
    result = analyze_handshake(path, [rule(name='first'), rule(name='second')], limit=1)
    next_page = analyze_handshake(path, [rule(name='first'), rule(name='second')], offset=1, limit=1)
    assert result['total'] == 2 and result['next_offset'] == 1 and result['items'][0]['rule'] == 'first'
    assert next_page['next_offset'] is None and next_page['items'][0]['rule'] == 'second'
    assert result['status_counts'] == next_page['status_counts'] == {'ack_observed': 2}
    assert result['request_edges_observed'] == 2


def test_empty_recording_is_explicit_and_unknown_configured_signal_is_refused(tmp_path):
    result = analyze_handshake(saved(tmp_path, []), [rule()])
    assert result['samples_total'] == result['total'] == 0 and result['status_counts'] == {}
    assert not result['clock']['strictly_increasing']
    with pytest.raises(ValueError, match='signal'):
        analyze_handshake(saved(tmp_path, [(False, False)]), [rule(ack_signal='missing')])


@pytest.mark.parametrize('rules', [[], [rule()] * 17, [rule(), rule()], [rule(request_signal='DI[1]')],
    [rule(request_active=False)], [rule(request_active=None)], [rule(request_active=[])],
    [rule(ack_active=float('nan'))], [rule(ack_active=2 ** 63)], [rule(ack_inactive='X' * 65)],
    [rule(timeout_seconds=True)], [rule(timeout_seconds=0)], [rule(timeout_seconds=86401)],
    [rule(timeout_seconds=float('inf'))], [rule(name='')], [rule(unknown=True)], [{}]])
def test_invalid_rules_fail_before_source_access(rules):
    with pytest.raises(ValueError):
        analyze_handshake(Path('does-not-exist'), rules)


@pytest.mark.parametrize('options', [{'offset': True}, {'offset': -1}, {'limit': False}, {'limit': 0}, {'limit': 101}])
def test_invalid_pagination_fails_before_source_access(options):
    with pytest.raises(ValueError):
        analyze_handshake(Path('does-not-exist'), [rule()], **options)


def test_derived_events_are_ignored_and_maximum_rule_sample_count_stays_bounded(tmp_path):
    states = [(False, False), (True, False), (True, True), (False, False)] * 75
    path = saved(tmp_path, states, extra={'events': [{'invented': True}], 'profile_seconds': {'invented': 999}})
    result = analyze_handshake(path, [rule(name=f'pair{index}') for index in range(16)])
    assert result['total'] == result['request_edges_observed'] == 1200
    assert result['status_counts'] == {'ack_observed': 1200}
    assert len(result['items']) == 100 and len(json.dumps(result, allow_nan=False)) < 200000


def test_self_contained_release_preserves_previous_operations_and_support_bytes():
    assert self_test()
    root = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'
    old, _ = load_release(root / 'recording_v1_1_0')
    current, _ = load_release(root / 'recording_v1_2_0')
    assert current['version'] == '1.2.0' and current['operations'][:2] == old['operations']
    for name in ['summary.py', 'wire.py', 'decoder.py', 'LICENSE.txt', 'APACHE-2.0.txt']:
        assert (root / 'recording_v1_2_0' / name).read_bytes() == (root / 'recording_v1_1_0' / name).read_bytes()


def test_same_mcp_session_loads_handshake_and_rolls_back_without_tool_changes(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    fixture = tmp_path / 'edits/fixture'
    fixture.mkdir(parents=True)
    saved(fixture, [(False, False), (True, False), (True, True)])
    releases = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.is_error, response
                return json.loads(response.content[0].text)

            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_release(tmp_path / 'state/extensions', releases / 'recording_v1_1_0')
            await call('reload_extensions', {})
            install_release(tmp_path / 'state/extensions', releases / 'recording_v1_2_0')
            assert not (await call('reload_extensions', {}))['mcp_tools_changed']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.recording.handshake'})
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': {
                'artifact_id': imported['artifact_id'], 'path': 'recording.json', 'rules': [rule()]}}
            result = (await call('run_read_operation', request))['result']
            assert result['items'][0]['status'] == 'ack_observed' and not result['physical_timing_verified']
            assert (await session.call_tool('run_read_operation', {**request,
                'arguments': {**request['arguments'], 'path': '../outside'}})).is_error
            (Path(imported['root']) / 'recording.json').write_text('{}')
            assert (await session.call_tool('run_read_operation', request)).is_error
            install_release(tmp_path / 'state/extensions', releases / 'recording_v1_1_0')
            await call('reload_extensions', {})
            assert (await session.call_tool('run_read_operation', request)).is_error
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools] == tools and len(tools) == 51
    asyncio.run(scenario())
