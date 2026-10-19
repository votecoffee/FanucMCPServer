import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from fanuc_workbench.bridge.extension_release import load_registry, load_release
from fanuc_workbench.bridge.extensions_cli import install_builtin
from fanuc_workbench.services.vqlog import analyze_file, parse_line


@pytest.mark.parametrize('message,event,fields', [
    ('Receive ACKQUE, area 1, work_id 2, tray 3, model_id 4, ack 0', 'Acknowledgement', {'ack': 0}),
    ('Put a part to queue, area 1, work_id 2, tray 3, model_id 4', 'Addition', {'work_id': 2}),
    ('Part is allocated, area 1, work_id 2, tray 3, model_id 4', 'Allocation', {'tray_id': 3}),
    ('Clear load stat, area 1, npicked 2/3', 'Clear', {'npicked_num': 2, 'npicked_den': 3}),
    ('Load-balance, area 1, model_id 4, npicked 2/3, n2pick 5/6', 'LoadBalance', {'n2pick_num': 5}),
    ('Part is overlapped, area 1, work_id 2, tray 3, model_id 4', 'Overlap', {'model_id': 4}),
    ('Receive GETQUE, area 1, timeout 5, consec 0, model_id *****, work_id ****', 'Request', {'model_id': None, 'work_id': None}),
    ('Receive SETLBD, line 2', 'SetLBD', {'line_id': 2}),
    ('Part is skipped (exit boundary), area 1, work_id 2, tray 3, model_id 4, cur_x -50, bound 60', 'Skip',
     {'model_id': 4, 'current_x': -50, 'bound': 60}),
    ('Get_queue timeout, area 1', 'Timeout', {'area_id': 1}),
])
def test_all_event_formats_preserve_typed_fields_without_nan(message, event, fields):
    result = parse_line('0, ' + message, 7)
    assert result['tick'] == 0 and result['line_number'] == 7
    assert result['event_type'] == event
    for name, value in fields.items():
        assert result['fields'][name] == value
    json.dumps(result, allow_nan=False)


def test_unknown_malformed_and_extended_events_are_retained(tmp_path):
    path = tmp_path / 'VQLOG.TXT'
    data = (b'not a timestamp, Unknown controller content\r\n'
            b'10, New firmware event\r\n'
            b'12, Get_queue timeout, area 1, new_field 2\r\n'
            b'14, Get_queue timeout, area 1\r\n')
    path.write_bytes(data)
    result = analyze_file(path)
    assert result['records_total'] == result['total'] == 4
    assert result['malformed_records'] == 1 and result['unknown_events'] == 2
    assert [row['event_type'] for row in result['items']] == ['Malformed', 'Unknown', 'Unknown', 'Timeout']
    assert result['items'][0]['message'] == 'not a timestamp, Unknown controller content'
    assert result['duration_seconds'] is None and not result['clock_available']
    assert result['source_sha256'] == hashlib.sha256(data).hexdigest()
    assert path.read_bytes() == data


def test_filter_pagination_and_explicit_tick_period(tmp_path):
    path = tmp_path / 'VQLOG.TXT'
    path.write_text('0, Part is allocated, area 1, work_id 2, tray 3, model_id 4\n'
                    '10, Part is allocated, area 2, work_id 2, tray 3, model_id 4\n'
                    '20, Get_queue timeout, area 1\n')
    result = analyze_file(path, event_type='Allocation', work_id=2, limit=1, tick_seconds=.01)
    assert result['duration_seconds'] == .2 and result['tick_period_verified'] is False
    assert result['total'] == 2 and result['next_offset'] == 1
    other = analyze_file(path, offset=1, limit=1, event_type='Allocation', work_id=2)
    assert other['items'][0]['fields']['area_id'] == 2 and other['next_offset'] is None
    assert analyze_file(path, area_id=1)['total'] == 2
    assert analyze_file(path)['duration_seconds'] == .04


def test_tick_regressions_never_invent_clock_wrap_or_negative_duration(tmp_path):
    path = tmp_path / 'VQLOG.TXT'
    path.write_text('100, Get_queue timeout, area 1\n0, Get_queue timeout, area 1\n')
    result = analyze_file(path)
    assert result['tick_regressions'] == 1 and result['duration_seconds'] is None
    assert all(row['elapsed_seconds'] is None for row in result['items'])
    path.write_text('')
    empty = analyze_file(path)
    assert empty['records_total'] == 0 and empty['duration_seconds'] is None


@pytest.mark.parametrize('options', [{'tick_seconds': 0}, {'tick_seconds': float('nan')},
    {'tick_seconds': True}, {'offset': -1}, {'limit': 101}, {'area_id': True}, {'event_type': 'Other'}])
def test_invalid_analysis_options_are_refused_before_file_access(options):
    with pytest.raises(ValueError):
        analyze_file(Path('not-a-controller-file'), **options)


def test_long_lines_and_large_ids_are_bounded(tmp_path):
    path = tmp_path / 'VQLOG.TXT'
    path.write_text('1, ' + 'X' * 4097)
    with pytest.raises(ValueError, match='length limit'):
        analyze_file(path)
    assert parse_line('9999999999999999999999999, event')['event_type'] == 'Malformed'


def test_builtin_staging_preserves_analysis_and_has_exact_support_file_hashes(tmp_path):
    root = tmp_path / 'extensions'
    result = install_builtin(root, 'vqlog')
    assert result['status'] == 'staged_only' and not result['controller_contacted']
    assert {row['id'] for row in load_registry(root)} == {'workbench.analysis', 'workbench.vqlog'}
    source = root / 'releases/workbench.vqlog/1.0.0'
    manifest, _ = load_release(source, result['manifest_sha256'])
    assert 'LICENSE.txt' in manifest['files']
    assert manifest['operations'][0]['effect'] == 'read'


def test_same_mcp_session_loads_vqlog_without_new_tools_or_controller_access(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    source = tmp_path / 'edits/fixture'
    source.mkdir(parents=True)
    data = b'0, Put a part to queue, area 1, work_id 2, tray 3, model_id 4\n10, Get_queue timeout, area 1\n'
    (source / 'VQLOG.TXT').write_bytes(data)

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump() for tool in (await session.list_tools()).tools]

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            assert (await call('list_operations', {}))['total'] == 33
            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_builtin(tmp_path / 'state/extensions', 'vqlog')
            reloaded = await call('reload_extensions', {})
            assert reloaded['status'] == 'reloaded' and not reloaded['mcp_tools_changed']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.vqlog.analyze'})
            arguments = {'artifact_id': imported['artifact_id'], 'path': 'VQLOG.TXT'}
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': arguments}
            result = (await call('run_read_operation', request))['result']
            assert result['records_total'] == 2 and result['source_sha256'] == hashlib.sha256(data).hexdigest()
            assert [tool.model_dump() for tool in (await session.list_tools()).tools] == tools
            assert len(tools) == 48
            bad = {**request, 'arguments': {**arguments, 'path': '../outside.txt'}}
            assert (await session.call_tool('run_read_operation', bad)).isError
            assert (await call('capabilities', {}))['controller_access']['read_only']

    asyncio.run(scenario())
