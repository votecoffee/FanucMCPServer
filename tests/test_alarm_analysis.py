import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from fanuc_workbench.bridge.extension_release import load_registry, load_release
from fanuc_workbench.bridge.extensions_cli import install_builtin
from fanuc_workbench.extensions.alarms.feature import MAX_BYTES, handle, self_test
from fanuc_workbench.services.alarm_analysis import analyze_alarms


def saved(tmp_path, text, encoding='utf-8'):
    path = tmp_path / 'alarm.txt'
    path.write_bytes(text.encode(encoding))
    return path


def analyze(path, **options):
    return analyze_alarms(path, **{'date_order': 'ymd', 'utc_offset_minutes': 0, **options})


def test_records_keep_unknown_lines_and_source_bytes_with_global_summary(tmp_path):
    path = saved(tmp_path, 'Export header\r\n\r\n1970/01/01 00:00:00 SRVO-001 (WARN) First\r\n'
                 '1970/01/01 00:00:01.000001 srvo-001 (WARN): Second\r\nSYST-002 (STOP) Untimed\r\n')
    original = path.read_bytes()
    result = analyze(path, limit=1)
    assert result['lines_total'] == 5 and result['blank_lines'] == 1
    assert result['parsed_alarm_records'] == 3 and result['unrecognized_lines'] == 1 and result['distinct_codes'] == 2
    assert result['timestamp_status_counts'] == {'valid': 2, 'missing': 1}
    assert result['total'] == 4 and result['next_offset'] == 1 and result['items'][0]['raw_line'] == 'Export header'
    next_page = analyze(path, offset=1, limit=1)
    assert next_page['items'][0]['line_number'] == 3 and next_page['items'][0]['utc_epoch_microseconds'] == '0'
    assert next_page['timestamp_status_counts'] == result['timestamp_status_counts']
    assert result['source_sha256'] == hashlib.sha256(original).hexdigest() and path.read_bytes() == original
    assert not result['alarm_occurrences_verified'] and not result['log_completeness_verified']
    assert not result['controller_contacted'] and not result['controller_written']


@pytest.mark.parametrize('stamp,order,offset,expected,micros,precision', [
    ('1970/01/01 00:00:00.000001Z', 'ymd', 0, '1970-01-01T00:00:00.000001Z', '1', 'fractional_second'),
    ('1969/12/31 23:59:59.999999Z', 'ymd', 0, '1969-12-31T23:59:59.999999Z', '-1', 'fractional_second'),
    ('2026/10/19 10:00', 'ymd', 90, '2026-10-19T08:30:00.000000Z', None, 'minute'),
    ('2026-10-19T10:00:02.1+01:30', 'ymd', 0, '2026-10-19T08:30:02.100000Z', None, 'fractional_second'),
    ('19/10/2026 10:00:00', 'dmy', 0, '2026-10-19T10:00:00.000000Z', None, 'second'),
    ('10/19/2026 10:00:00', 'mdy', -300, '2026-10-19T15:00:00.000000Z', None, 'second'),
    ('2026/10/19 10:00:00 -14:00', 'ymd', 0, '2026-10-20T00:00:00.000000Z', None, 'second'),
])
def test_explicit_clocks_and_exact_decimal_microseconds(tmp_path, stamp, order, offset, expected, micros, precision):
    row = analyze(saved(tmp_path, stamp + ' SRVO-001 Example'), date_order=order, utc_offset_minutes=offset)['items'][0]
    assert row['timestamp_status'] == 'valid' and row['timestamp_utc'] == expected
    assert row['timestamp_precision'] == precision
    if micros is not None:
        assert row['utc_epoch_microseconds'] == micros


def test_ambiguous_date_uses_only_the_supplied_order_and_inline_offsets_are_explicit(tmp_path):
    path = saved(tmp_path, '10/11/2026 12:00:00 SRVO-001 Example')
    assert analyze(path, date_order='mdy')['items'][0]['timestamp_utc'].startswith('2026-10-11')
    assert analyze(path, date_order='dmy')['items'][0]['timestamp_utc'].startswith('2026-11-10')
    assert analyze(path, date_order='ymd')['items'][0]['timestamp_status'] == 'invalid'
    result = analyze(saved(tmp_path, '2026/10/19 12:00:00Z SRVO-001 Example'), utc_offset_minutes=60)
    row = result['items'][0]
    assert row['offset_source'] == 'inline' and row['effective_utc_offset_minutes'] == 0
    assert row['inline_offset_differs_from_supplied']
    assert not result['clock_assumptions']['timezone_rules_applied'] and not result['clock_assumptions']['assumptions_verified']


@pytest.mark.parametrize('stamp,status,error', [
    ('2026/02/30 12:00:00', 'invalid', 'invalid_calendar_or_time'),
    ('2026/10/19 24:00:00', 'invalid', 'invalid_calendar_or_time'),
    ('26/10/19 12:00:00', 'invalid', 'four_digit_year_required'),
    ('2026/10/19 12:00:00+14:01', 'invalid', 'inline_offset_outside_range'),
    ('2026/10/19 12:00:00+00:60', 'invalid', 'inline_offset_outside_range'),
    ('0001/01/01 00:00:00+14:00', 'invalid', 'invalid_calendar_or_time'),
    ('9999/12/31 23:59:59-14:00', 'invalid', 'invalid_calendar_or_time'),
    ('2026/10-19 12:00:00', 'unsupported', 'timestamp_syntax_unsupported'),
    ('12026/10/19 12:00:00', 'unsupported', 'timestamp_syntax_unsupported'),
    ('Oct 19, 2026 12:00:00', 'unsupported', 'timestamp_syntax_unsupported'),
])
def test_bad_timestamps_keep_the_alarm_and_do_not_invent_an_instant(tmp_path, stamp, status, error):
    result = analyze(saved(tmp_path, stamp + ' SRVO-001 Retained'))
    row = result['items'][0]
    assert result['parsed_alarm_records'] == 1 and row['message'] == 'Retained'
    assert row['timestamp_status'] == status and row['timestamp_error'] == error
    assert row['timestamp_utc'] is None and row['utc_epoch_microseconds'] is None


def test_recurrence_counts_duplicates_without_claiming_physical_occurrences(tmp_path):
    path = saved(tmp_path, '1970/01/01 00:00:00 SRVO-001 (WARN) A\n'
                 '1970/01/01 00:00:01.000001 SRVO-001 (WARN) B\n'
                 '1970/01/01 00:00:01.000001 SRVO-001 (WARN) B\n'
                 '1970/01/01 00:00:05 SYST-002 Other\n')
    result = analyze(path, view='codes')
    first = result['items'][0]
    assert first['code'] == 'SRVO-001' and first['count'] == 3 and first['message_variants'] == 2
    assert first['most_common_message'] == 'B' and first['most_common_severity'] == 'WARN'
    assert first['repeated_timestamp_intervals'] == 1 and first['comparable_intervals'] == 2
    assert first['minimum_interval_microseconds'] == '0' and first['maximum_interval_microseconds'] == '1000001'
    assert first['interval_coverage_complete'] and not result['timing_verified']
    assert not result['alarm_occurrences_verified']


def test_missing_timestamp_breaks_code_adjacency_instead_of_bridging(tmp_path):
    path = saved(tmp_path, '1970/01/01 00:00:00 SRVO-001 A\nSRVO-001 Missing\n'
                 '1970/01/01 00:00:03 SRVO-001 C\n1970/01/01 00:00:05 SRVO-001 D\n')
    row = analyze(path, view='codes')['items'][0]
    assert row['count'] == 4 and row['comparable_intervals'] == 1 and row['incomplete_intervals'] == 2
    assert row['minimum_interval_microseconds'] == row['maximum_interval_microseconds'] == '2000000'
    assert not row['interval_coverage_complete'] and row['unavailable_timestamp_occurrences'] == 1


def test_declared_source_order_is_checked_without_sorting_or_repair(tmp_path):
    path = saved(tmp_path, '1970/01/01 00:00:03 SRVO-001 C\n1970/01/01 00:00:02 SRVO-001 B\n'
                 '1970/01/01 00:00:00 SRVO-001 A\n')
    wrong = analyze(path, view='codes')['items'][0]
    assert wrong['opposite_order_intervals'] == 2 and wrong['minimum_interval_microseconds'] is None
    declared = analyze(path, view='codes', source_order='newest_first')['items'][0]
    assert declared['opposite_order_intervals'] == 0 and declared['minimum_interval_microseconds'] == '1000000'
    assert declared['maximum_interval_microseconds'] == '2000000' and declared['interval_coverage_complete']
    assert declared['first_valid_timestamp_utc'].endswith('03.000000Z')
    assert declared['last_valid_timestamp_utc'].endswith('00.000000Z')


def test_filters_and_views_page_results_without_changing_file_totals(tmp_path):
    path = saved(tmp_path, 'Header\nSRVO-001 One\nSRVO-001 Two\nSYST-002 Other\n')
    result = analyze(path, code='srvo-001', limit=1)
    assert result['total'] == 2 and result['next_offset'] == 1 and result['parsed_alarm_records'] == 3
    assert result['code_filter'] == 'SRVO-001' and result['items'][0]['line_number'] == 2
    assert analyze(path, include_unrecognized=False)['total'] == 3
    groups = analyze(path, view='codes', limit=1)
    assert groups['total'] == 2 and groups['next_offset'] == 1
    assert analyze(path, code='SRVO-999')['items'] == []
    assert analyze(path, offset=99)['items'] == []


@pytest.mark.parametrize('text,encoding', [('SRVO-001 Caf\u00e9\r\n', 'cp1252'), ('SRVO-001 \u4fdd\u6301\n', 'utf-8'),
                                         ('SRVO-001 BOM\n', 'utf-8-sig')])
def test_supported_encodings_preserve_raw_lines_and_exact_hash(tmp_path, text, encoding):
    path = saved(tmp_path, text, encoding)
    result = analyze(path)
    assert result['items'][0]['raw_line'] == text.rstrip('\r\n')
    assert result['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['encoding'] == ('cp1252' if encoding == 'cp1252' else 'utf-8')


def test_unrecognized_tokens_malformed_severity_and_first_code_policy_are_explicit(tmp_path):
    path = saved(tmp_path, '123SRVO-001 Fragment\nABCDEFGHIJK-001 Too long\nSRVO-123456 Too many digits\n'
                 'SRVO-001 (bad severity\nSRVO-001 and SYST-002\nSRVO-001 -123 remains negative\n')
    result = analyze(path)
    assert result['unrecognized_lines'] == result['parsed_alarm_records'] == 3
    assert result['parse_issue_counts'] == {'severity_syntax_unsupported': 1}
    assert result['items'][4]['code'] == 'SRVO-001'
    assert result['items'][5]['message'] == '-123 remains negative'


@pytest.mark.parametrize('text', ['', '\r\n\n   \n'])
def test_empty_or_blank_only_logs_have_no_alarms_or_intervals(tmp_path, text):
    result = analyze(saved(tmp_path, text), view='codes')
    assert result['total'] == result['parsed_alarm_records'] == result['distinct_codes'] == 0
    assert result['timestamp_status_counts'] == {} and not result['log_completeness_verified']


@pytest.mark.parametrize('options', [{'date_order': 'auto'}, {'date_order': None}, {'utc_offset_minutes': True},
    {'utc_offset_minutes': 841}, {'utc_offset_minutes': -841}, {'utc_offset_minutes': 1.5},
    {'source_order': 'auto'}, {'view': 'other'}, {'code': 'SRVO-001 extra'}, {'code': True},
    {'include_unrecognized': 1}, {'offset': True}, {'offset': -1}, {'limit': False}, {'limit': 0}, {'limit': 101}])
def test_invalid_options_fail_before_source_access(options):
    with pytest.raises(ValueError):
        analyze(Path('does-not-exist'), **options)


def test_size_line_count_encoding_and_binary_bounds_are_enforced(tmp_path):
    path = tmp_path / 'alarm.txt'
    for data in [b'x' * (MAX_BYTES + 1), b'\n' * 50001, ('\u00e9' * 1025).encode(), b'\x81', b'\x00SRVO-001']:
        path.write_bytes(data)
        with pytest.raises(ValueError):
            analyze(path)


def test_maximum_line_count_and_worst_case_escaped_page_stay_bounded(tmp_path):
    result = analyze(saved(tmp_path, 'Unknown header\n' * 50000))
    assert result['lines_total'] == result['unrecognized_lines'] == 50000 and len(result['items']) == 100
    result = analyze(saved(tmp_path, ('SRVO-001 ' + '\x01' * 2000 + '\n') * 100))
    assert result['parsed_alarm_records'] == 100
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 3 * 1024 * 1024


def test_builtin_staging_preserves_analysis_and_pins_self_contained_mit_release(tmp_path):
    assert self_test()
    result = install_builtin(tmp_path, 'alarms')
    assert result['status'] == 'staged_only' and not result['controller_contacted']
    assert {row['id'] for row in load_registry(tmp_path)} == {'workbench.analysis', 'workbench.alarms'}
    manifest, _ = load_release(tmp_path / 'releases/workbench.alarms/1.0.0', result['manifest_sha256'])
    assert set(manifest['files']) == {'feature.py', 'LICENSE.txt'} and manifest['operations'][0]['effect'] == 'read'
    with pytest.raises(ValueError, match='Unknown'):
        handle('other', {}, {})


def test_same_mcp_session_loads_alarm_feature_and_handles_large_page_with_48_tools(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    fixture = tmp_path / 'edits/fixture'
    fixture.mkdir(parents=True)
    saved(fixture, ('1970/01/01 00:00:00 SRVO-001 ' + '\x01' * 2000 + '\n') * 100)

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

            assert (await call('list_operations', {}))['total'] == 34
            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_builtin(tmp_path / 'state/extensions', 'alarms')
            assert not (await call('reload_extensions', {}))['mcp_tools_changed']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.alarms.analyze'})
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': {
                'artifact_id': imported['artifact_id'], 'path': 'alarm.txt', 'date_order': 'ymd', 'utc_offset_minutes': 0}}
            result = (await call('run_read_operation', request))['result']
            assert result['parsed_alarm_records'] == 100 and len(result['items']) == 100
            assert not result['alarm_occurrences_verified'] and (await call('list_operations', {}))['total'] == 35
            assert (await session.call_tool('run_read_operation', {**request,
                'arguments': {**request['arguments'], 'path': '../outside'}})).isError
            assert (await session.call_tool('run_read_operation', {**request, 'revision': 'stale'})).isError
            (Path(imported['root']) / 'alarm.txt').write_text('changed')
            assert (await session.call_tool('run_read_operation', request)).isError
            assert [tool.model_dump() for tool in (await session.list_tools()).tools] == tools and len(tools) == 48
    asyncio.run(scenario())
