import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from fanuc_workbench.bridge.extension_release import install_release
from fanuc_workbench.bridge.extensions_cli import install_builtin
from fanuc_workbench.extensions.alarms.feature import analyze_alarms as original_analysis
from fanuc_workbench.extensions.alarms_v1_1_0.feature import handle, self_test
from fanuc_workbench.services.alarm_analysis import analyze_alarms, compare_alarms


CLOCK = {'date_order': 'ymd', 'utc_offset_minutes': 0}


def pair(tmp_path, before, after):
    paths = tmp_path / 'before.txt', tmp_path / 'after.txt'
    for path, text in zip(paths, (before, after)):
        path.write_bytes(text.encode('utf-8'))
    return paths


def compare(paths, **options):
    return compare_alarms(*paths, **{'baseline_clock': CLOCK, 'candidate_clock': CLOCK, **options})


def test_code_presence_counts_hashes_and_global_summaries(tmp_path):
    paths = pair(tmp_path, 'Header\nSRVO-001 (WARN) A\nSRVO-001 (WARN) A\nSYST-002 Removed\n',
                 'Header\nSRVO-001 (WARN) A\nMOTN-003 Added\n')
    originals = [path.read_bytes() for path in paths]
    result = compare(paths)
    rows = {row['code']: row for row in result['items']}
    assert rows['SRVO-001']['count_delta'] == -1 and rows['SRVO-001']['status'] == 'changed'
    assert rows['SYST-002']['status'] == 'only_in_baseline' and rows['SYST-002']['candidate'] is None
    assert rows['MOTN-003']['status'] == 'only_in_candidate' and rows['MOTN-003']['baseline'] is None
    assert result['status_counts'] == {'only_in_baseline': 1, 'only_in_candidate': 1, 'changed': 1, 'unchanged': 0}
    assert result['parsed_record_count_delta'] == -1 and result['codes_compared'] == 3
    for side, data in zip(('baseline', 'candidate'), originals):
        assert result[side]['source_sha256'] == hashlib.sha256(data).hexdigest()
        assert result[side]['unrecognized_lines'] == 1
    assert [path.read_bytes() for path in paths] == originals
    assert not result['source_bytes_identical']
    assert not result['physical_occurrence_identity_verified'] and not result['capture_windows_comparable_verified']
    assert not result['controller_contacted'] and not result['controller_written']


@pytest.mark.parametrize('after,field', [
    ('SRVO-001 (WARN) A\nSRVO-001 (WARN) C\n', 'message_set_changed'),
    ('SRVO-001 (WARN) A\nSRVO-001 (STOP) B\n', 'severity_set_changed'),
])
def test_full_distributions_detect_changes_with_equal_counts_and_same_modal_text(tmp_path, after, field):
    paths = pair(tmp_path, 'SRVO-001 (WARN) A\nSRVO-001 (WARN) B\n', after)
    row = compare(paths)['items'][0]
    assert row['status'] == 'changed' and row['count_delta'] == 0 and row[field]
    assert row['baseline']['most_common_message'] == row['candidate']['most_common_message'] == 'A'
    assert row['baseline_text_distribution_sha256'] != row['candidate_text_distribution_sha256']


def test_severity_message_association_changes_are_detected_even_when_marginal_counts_match(tmp_path):
    paths = pair(tmp_path, 'SRVO-001 (WARN) A\nSRVO-001 (STOP) B\n',
                 'SRVO-001 (STOP) A\nSRVO-001 (WARN) B\n')
    row = compare(paths)['items'][0]
    assert row['status'] == 'changed' and row['record_text_distribution_changed']
    assert not row['message_set_changed'] and not row['severity_set_changed']


def test_timestamp_order_headers_and_raw_formatting_are_outside_comparison_scope(tmp_path):
    paths = pair(tmp_path, '1970/01/01 00:00:00 SRVO-001 (WARN): A\n1970/01/01 00:00:01 SRVO-001 (WARN) B\n',
                 'Other header\n2026/10/19 10:00:00 srvo-001 (WARN) B\nSRVO-001 (WARN) A\n')
    result = compare(paths, changes_only=False)
    row = result['items'][0]
    assert row['status'] == 'unchanged' and not result['source_bytes_identical']
    assert row['baseline_text_distribution_sha256'] == row['candidate_text_distribution_sha256']
    assert not result['timestamps_used_for_matching'] and not result['record_order_used_for_matching']
    assert row['baseline']['valid_timestamp_occurrences'] == 2
    assert row['candidate']['unavailable_timestamp_occurrences'] == 1
    assert compare(paths)['total'] == 0


def test_clocks_are_independent_and_recurrence_is_preserved_per_file(tmp_path):
    paths = pair(tmp_path, '19/10/2026 01:00:00 SRVO-001 A\n19/10/2026 01:00:02 SRVO-001 A\n',
                 '10/19/2026 03:00:02 SRVO-001 A\n10/19/2026 03:00:00 SRVO-001 A\n')
    result = compare(paths, baseline_clock={'date_order': 'dmy', 'utc_offset_minutes': 60},
        candidate_clock={'date_order': 'mdy', 'utc_offset_minutes': 180, 'source_order': 'newest_first'}, changes_only=False)
    row = result['items'][0]
    assert row['status'] == 'unchanged'
    assert row['baseline']['first_valid_timestamp_utc'] == '2026-10-19T00:00:00.000000Z'
    assert row['candidate']['last_valid_timestamp_utc'] == '2026-10-19T00:00:00.000000Z'
    assert row['baseline']['minimum_interval_microseconds'] == row['candidate']['minimum_interval_microseconds'] == '2000000'
    assert result['baseline']['clock_assumptions']['utc_offset_minutes'] == 60
    assert result['candidate']['clock_assumptions']['source_order'] == 'newest_first'
    assert not result['timing_verified']


def test_filters_and_pages_do_not_change_whole_file_counts(tmp_path):
    paths = pair(tmp_path, 'SRVO-001 A\nSYST-002 B\nMOTN-003 C\n', 'SRVO-001 A\nSYST-002 D\n')
    first = compare(paths, limit=1)
    assert first['total'] == 2 and first['next_offset'] == 1 and first['items'][0]['code'] == 'MOTN-003'
    second = compare(paths, offset=1, limit=1)
    assert second['items'][0]['code'] == 'SYST-002' and second['next_offset'] is None
    filtered = compare(paths, code='syst-002')
    assert filtered['total'] == 1 and filtered['code_filter'] == 'SYST-002'
    assert filtered['status_counts'] == first['status_counts'] == second['status_counts']
    assert filtered['codes_compared'] == 3 and filtered['baseline']['parsed_alarm_records'] == 3
    assert compare(paths, code='SRVO-001')['items'] == []
    assert compare(paths, code='SRVO-001', changes_only=False)['items'][0]['status'] == 'unchanged'
    assert compare(paths, offset=100)['items'] == []


def test_empty_files_unrecognized_lines_and_duplicates_do_not_create_physical_events(tmp_path):
    paths = pair(tmp_path, '', 'Header\n\n')
    result = compare(paths)
    assert result['codes_compared'] == result['total'] == result['parsed_record_count_delta'] == 0
    assert result['baseline']['lines_total'] == 0 and result['candidate']['unrecognized_lines'] == 1
    paths = pair(tmp_path, 'SRVO-001 Same\n', 'SRVO-001 Same\nSRVO-001 Same\n')
    row = compare(paths)['items'][0]
    assert row['count_delta'] == 1 and not row['message_set_changed'] and not row['severity_set_changed']
    assert not compare(paths)['alarm_occurrences_verified']


def test_identical_bytes_still_retain_distinct_supplied_clock_assumptions(tmp_path):
    paths = pair(tmp_path, '1970/01/01 00:00:00 SRVO-001 A', '1970/01/01 00:00:00 SRVO-001 A')
    result = compare(paths, candidate_clock={**CLOCK, 'utc_offset_minutes': 60}, changes_only=False)
    assert result['source_bytes_identical'] and result['status_counts']['unchanged'] == 1
    assert result['items'][0]['baseline']['first_valid_timestamp_utc'] != result['items'][0]['candidate']['first_valid_timestamp_utc']


@pytest.mark.parametrize('options', [
    {'baseline_clock': None}, {'baseline_clock': {}}, {'candidate_clock': {'date_order': 'ymd'}},
    {'candidate_clock': {**CLOCK, 'extra': True}}, {'candidate_clock': {**CLOCK, 'utc_offset_minutes': True}},
    {'candidate_clock': {**CLOCK, 'utc_offset_minutes': -841}}, {'candidate_clock': {**CLOCK, 'date_order': 'auto'}},
    {'baseline_clock': {**CLOCK, 'source_order': 'auto'}}, {'changes_only': 1}, {'code': 'SRVO-001 extra'},
    {'offset': True}, {'offset': -1}, {'limit': 0}, {'limit': 101},
])
def test_invalid_inputs_fail_before_either_source_is_opened(options):
    with pytest.raises(ValueError):
        compare((Path('not-present-before'), Path('not-present-after')), **options)


@pytest.mark.parametrize('text', ['\x00', '\n' * 50001, 'X' * 2049], ids=['nul', 'line-count', 'line-bytes'])
def test_either_source_must_meet_parser_bounds(tmp_path, text):
    paths = pair(tmp_path, 'SRVO-001 A', text)
    with pytest.raises(ValueError):
        compare(paths)
    with pytest.raises(ValueError):
        compare(paths[::-1])


def test_maximum_page_of_escaped_messages_remains_below_worker_limit(tmp_path):
    before = ''.join(f'SRVO-{index:03} (WARN) ' + '\x01' * 2000 + '\n' for index in range(100))
    after = before.replace('\x01', '\x02')
    result = compare(pair(tmp_path, before, after))
    assert result['total'] == 100 and len(result['items']) == 100
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 3 * 1024 * 1024


@pytest.mark.parametrize('view', ['records', 'codes'])
def test_original_analysis_result_contract_is_preserved(tmp_path, view):
    paths = pair(tmp_path, 'Header\n1970/01/01 00:00:00 SRVO-001 (WARN) A\nSRVO-001 Missing\n', '')
    options = {**CLOCK, 'view': view}
    assert analyze_alarms(paths[0], **options) == original_analysis(paths[0], **options)
    assert self_test()
    with pytest.raises(ValueError, match='Unknown'):
        handle('other', {}, {})


def test_same_mcp_session_loads_compares_rolls_back_and_checks_both_artifacts(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    baseline_dir, candidate_dir = tmp_path / 'edits/baseline', tmp_path / 'edits/candidate'
    baseline_dir.mkdir(parents=True)
    candidate_dir.mkdir()
    baseline = ''.join(f'SRVO-{index:03} (WARN) ' + '\x01' * 2000 + '\n' for index in range(100))
    (baseline_dir / 'alarm.txt').write_text(baseline, encoding='utf-8')
    (candidate_dir / 'alarm.txt').write_text(baseline.replace('\x01', '\x02'), encoding='utf-8')

    async def scenario():
        repo = Path(__file__).resolve().parents[1]
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(repo)})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.is_error, response
                return json.loads(response.content[0].text)

            baseline_total = (await call('list_operations', {}))['total']
            artifacts = [await call('import_workspace', {'source_folder': folder}) for folder in ('baseline', 'candidate')]
            release_root = tmp_path / 'state/extensions'
            install_builtin(release_root, 'alarms')
            assert not (await call('reload_extensions', {}))['mcp_tools_changed']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.alarms.compare'})
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': {
                'baseline_artifact_id': artifacts[0]['artifact_id'], 'baseline_path': 'alarm.txt',
                'candidate_artifact_id': artifacts[1]['artifact_id'], 'candidate_path': 'alarm.txt',
                'baseline_clock': CLOCK, 'candidate_clock': CLOCK}}
            result = (await call('run_read_operation', request))['result']
            assert result['status_counts']['changed'] == 100 and len(result['items']) == 100
            assert (await call('list_operations', {}))['total'] == baseline_total + 2
            for field in ('baseline_path', 'candidate_path'):
                assert (await session.call_tool('run_read_operation', {**request,
                    'arguments': {**request['arguments'], field: '../outside'}})).is_error
            assert (await session.call_tool('run_read_operation', {**request, 'revision': 'stale'})).is_error
            install_release(release_root, repo / 'fanuc_workbench/extensions/alarms', packaged=True)
            await call('reload_extensions', {})
            assert (await call('list_operations', {}))['total'] == baseline_total + 1
            assert (await session.call_tool('run_read_operation', request)).is_error
            legacy = await call('describe_operation', {'operation_id': 'workbench.alarms.analyze'})
            install_builtin(release_root, 'alarms')
            await call('reload_extensions', {})
            assert (await call('describe_operation', {'operation_id': 'workbench.alarms.analyze'}))['revision'] != legacy['revision']
            assert (await call('run_read_operation', request))['result']['codes_compared'] == 100
            for artifact in artifacts:
                path = Path(artifact['root']) / 'alarm.txt'
                data = path.read_bytes()
                path.write_bytes(b'changed')
                assert (await session.call_tool('run_read_operation', request)).is_error
                path.write_bytes(data)
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools] == tools and len(tools) == 48
    asyncio.run(scenario())
