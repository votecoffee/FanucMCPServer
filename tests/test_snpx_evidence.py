import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

import pytest

from fanuc_workbench.bridge.extension_release import install_release, load_release
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.extensions.snpx_v1_2_0.feature import self_test
from fanuc_workbench.services.snpx_evidence import inspect_evidence
from test_robot_bridge import MemoryRobot
from test_snpx import bridge_policy, controller
from test_snpx_decode import document


INIT = bytes(56)
ACK = bytes.fromhex('0100000000000000010000000000000000000000000000000000000000000000'
                    '000000000000000000000000000000000000000000000000')
SESSION = bytes.fromhex('08000100000000000001000000000000000100000000000000000000000001c0'
                        '00000000100e000001014f01000000000000000000000000')
READ = bytes.fromhex('02000200000000000002000000000000000200000000000000000000000002c0'
                     '00000000100e000001010408000002000000000000000000')


def trace(raw=bytes(4)):
    request = bytearray(READ)
    struct.pack_into('<H', request, 46, len(raw) // 2)
    response = bytearray(56 + (len(raw) if len(raw) > 6 else 0))
    struct.pack_into('<HH', response, 0, 3, 2)
    response[30], response[31] = 2, 0xD4 if len(raw) <= 6 else 0x94
    response[40:42] = bytes((1, 1))
    if len(raw) <= 6:
        response[44:44 + len(raw)] = raw
    else:
        struct.pack_into('<H', response, 4, len(raw))
        response[48:52] = bytes((1, 1, 4, 8))
        response[56:] = raw
    return [{'request_hex': INIT.hex(), 'response_hex': ACK.hex()},
            {'request_hex': SESSION.hex(), 'response_hex': SESSION.hex()},
            {'request_hex': bytes(request).hex(), 'response_hex': bytes(response).hex()}]


def saved(tmp_path, raw=bytes(4), *, exchanges=None, **changes):
    wire = tmp_path / 'wire.json'
    wire.write_text(json.dumps(trace(raw) if exchanges is None else exchanges), encoding='utf-8')
    capture = tmp_path / 'sample.json'
    capture.write_text(json.dumps({**document(raw, address=1),
        'wire_sha256': hashlib.sha256(wire.read_bytes()).hexdigest(), **changes}), encoding='utf-8')
    return capture, wire


@pytest.mark.parametrize('raw', [bytes(2), bytes(4), bytes(6), bytes(8), bytes(256), bytes.fromhex('3412ffff')])
def test_complete_inline_and_extended_reads_bind_hashes_and_payload(tmp_path, raw):
    paths = saved(tmp_path, raw)
    original = [path.read_bytes() for path in paths]
    result = inspect_evidence(*paths)
    assert result['supported_trace_valid'] and result['word_payload_matches_wire']
    assert result['wire_hash_matches_capture'] and not result['issues'] and not result['exchange_issue_counts']
    assert result['successful_read_exchanges'] == 1
    assert result['zero_payload_read_exchanges'] == (1 if not any(raw) else 0)
    assert result['application_write_requests_observed'] == 0
    assert result['items'][2]['payload_hex'] == raw.hex()
    assert result['capture_source_sha256'] == hashlib.sha256(original[0]).hexdigest()
    assert result['wire_source_sha256'] == hashlib.sha256(original[1]).hexdigest()
    assert [path.read_bytes() for path in paths] == original
    assert not result['controller_contacted'] and not result['capture_provenance_verified']
    assert not result['controller_compatibility_verified'] and not result['transport_cause_verified']


@pytest.mark.parametrize('offset,value,code', [
    (0, 2, 'response_packet_type_unsupported'), (2, 7, 'response_sequence_mismatch'),
    (30, 7, 'response_sequence_mismatch'), (31, 0xD1, 'controller_error_reply'),
    (40, 2, 'multipart_reply_unsupported'), (42, 1, 'controller_status_nonzero'),
    (4, 255, 'payload_exceeds_request')])
def test_invalid_replies_never_count_zero_payloads(tmp_path, offset, value, code):
    wire = trace()
    response = bytearray.fromhex(wire[2]['response_hex'])
    response[offset] = value
    wire[2]['response_hex'] = response.hex()
    result = inspect_evidence(*saved(tmp_path, exchanges=wire))
    assert not result['supported_trace_valid'] and not result['word_payload_matches_wire']
    assert result['exchange_issue_counts'][code] == 1
    assert result['zero_payload_read_exchanges'] == result['successful_read_exchanges'] == 0
    assert result['items'][2]['payload_hex'] is None


@pytest.mark.parametrize('length,code', [(0, 'no_reply_recorded'), (20, 'response_header_truncated'),
                                       (42, 'response_truncated'), (55, 'response_truncated')])
def test_partial_replies_keep_transport_error_metadata_without_guessing_cause(tmp_path, length, code):
    wire = trace()
    wire[2]['response_hex'] = wire[2]['response_hex'][:length * 2]
    wire[2]['error_type'] = 'TimeoutError'
    result = inspect_evidence(*saved(tmp_path, exchanges=wire))
    assert result['exchange_issue_counts'][code] == 1
    assert result['exchange_issue_counts']['reported_transport_error'] == 1
    assert result['items'][2]['reported_error_type'] == 'TimeoutError'
    assert not result['transport_cause_verified'] and not result['supported_trace_valid']


@pytest.mark.parametrize('offset,value,code', [(48, 2, 'multipart_reply_unsupported'),
    (50, 7, 'extended_service_mismatch'), (51, 0x38, 'extended_segment_mismatch'),
    (52, 1, 'controller_status_nonzero'), (4, 7, 'extended_payload_size_unsupported')])
def test_extended_reply_rejections_are_distinct(tmp_path, offset, value, code):
    wire = trace(bytes(8))
    response = bytearray.fromhex(wire[2]['response_hex'])
    response[offset] = value
    wire[2]['response_hex'] = response.hex()
    result = inspect_evidence(*saved(tmp_path, bytes(8), exchanges=wire))
    assert result['exchange_issue_counts'][code] == 1 and not result['supported_trace_valid']


def test_failed_handshake_taints_later_valid_looking_reads(tmp_path):
    wire = trace()
    wire[0]['response_hex'] = bytes(56).hex()
    result = inspect_evidence(*saved(tmp_path, exchanges=wire))
    assert result['exchange_issue_counts']['init_not_acknowledged'] == 1
    assert result['exchange_issue_counts']['exchange_after_failure'] == 2
    assert result['successful_read_exchanges'] == result['zero_payload_read_exchanges'] == 0


def test_reported_error_taints_an_otherwise_complete_reply(tmp_path):
    wire = trace()
    wire[2]['error_type'] = 'SnpxError'
    result = inspect_evidence(*saved(tmp_path, exchanges=wire))
    assert not result['supported_trace_valid'] and result['successful_read_exchanges'] == 0


@pytest.mark.parametrize('offset,value,code', [(2, 9, 'request_sequence_mismatch'),
    (8, 1, 'request_header_unsupported'), (48, 1, 'request_header_unsupported'),
    (43, 0x38, 'read_span_or_segment_unsupported'), (46, 0, 'read_span_or_segment_unsupported')])
def test_request_contract_is_checked_independently(tmp_path, offset, value, code):
    wire = trace()
    request = bytearray.fromhex(wire[2]['request_hex'])
    request[offset] = value
    wire[2]['request_hex'] = request.hex()
    result = inspect_evidence(*saved(tmp_path, exchanges=wire))
    assert result['exchange_issue_counts'][code] == 1 and result['successful_read_exchanges'] == 0


@pytest.mark.parametrize('extended', [False, True])
def test_write_request_is_flagged_and_session_writing_header_is_not(tmp_path, extended):
    wire = trace()
    request = bytearray.fromhex(wire[2]['request_hex'])
    request[31] = 0x80 if extended else 0xC0
    request[50 if extended else 42] = 7
    wire[2]['request_hex'] = request.hex()
    result = inspect_evidence(*saved(tmp_path, exchanges=wire))
    assert result['application_write_requests_observed'] == 1
    assert not result['items'][1]['request']['application_write_request_observed']
    assert result['exchange_issue_counts']['request_outside_read_contract'] == 1


def test_short_session_acknowledgement_is_supported(tmp_path):
    wire = trace()
    reply = bytearray(56)
    struct.pack_into('<HH', reply, 0, 3, 1)
    reply[30:32], reply[40:42] = bytes((1, 0xD4)), bytes((1, 1))
    wire[1]['response_hex'] = reply.hex()
    result = inspect_evidence(*saved(tmp_path, exchanges=wire))
    assert result['supported_trace_valid'] and result['successful_read_exchanges'] == 1


def test_unavailable_capture_is_not_overridden_by_a_complete_zero_reply(tmp_path):
    paths = saved(tmp_path, errors={'words': 'unavailable'}, raw_hex=None, words_u16=None, words_i16=None)
    result = inspect_evidence(*paths)
    assert not result['supported_trace_valid'] and not result['word_payload_matches_wire']
    assert 'capture_unavailable_despite_complete_read' in result['issues']
    assert 'capture_reports_unavailable' in result['issues']


def test_hash_mismatch_and_payload_contradiction_remain_visible(tmp_path):
    paths = saved(tmp_path, wire_sha256='0' * 64)
    result = inspect_evidence(*paths)
    assert result['wire_hash_matches_capture'] is False and 'wire_hash_mismatch' in result['issues']
    assert not result['supported_trace_valid']
    paths = saved(tmp_path, bytes(4), exchanges=trace(bytes.fromhex('01000000')))
    result = inspect_evidence(*paths)
    assert result['wire_hash_matches_capture'] and not result['word_payload_matches_wire']
    assert 'capture_payload_not_supported_by_wire' in result['issues']


def test_empty_wire_does_not_infer_a_connection_refusal(tmp_path):
    result = inspect_evidence(*saved(tmp_path, exchanges=[], errors={'words': 'unavailable'},
        raw_hex=None, words_u16=None, words_i16=None))
    assert 'no_exchanges_recorded' in result['issues'] and 'handshake_incomplete' in result['issues']
    assert result['successful_read_exchanges'] == 0 and not result['transport_cause_verified']


@pytest.mark.parametrize('wire', [[{'request_hex': 'xyz', 'response_hex': ''}],
    [{'request_hex': '', 'response_hex': '', 'unknown': True}],
    [{'request_hex': '', 'response_hex': '', 'error_type': '../invalid'}],
    [{'request_hex': '00' * 513, 'response_hex': ''}], [{}] * 257])
def test_malformed_or_oversized_wire_input_is_refused(tmp_path, wire):
    with pytest.raises(ValueError):
        inspect_evidence(*saved(tmp_path, exchanges=wire))


@pytest.mark.parametrize('options', [{'offset': True}, {'offset': -1}, {'limit': 0}, {'limit': 101}])
def test_invalid_options_are_refused_before_file_access(options):
    with pytest.raises(ValueError):
        inspect_evidence(Path('missing-capture'), Path('missing-wire'), **options)


def test_pagination_keeps_whole_trace_summary(tmp_path):
    result = inspect_evidence(*saved(tmp_path), offset=1, limit=1)
    assert result['supported_trace_valid'] and result['total'] == 3 and result['next_offset'] == 2
    assert result['items'][0]['exchange'] == 1 and result['successful_read_exchanges'] == 1


def test_actual_simulated_reader_evidence_can_be_inspected_without_further_contact(controller, tmp_path):
    port, seen, _, _ = controller
    path, _ = bridge_policy(tmp_path, port)
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        captured = bridge.snpx.read_words('r1', 1, 4)
        root = bridge.operations.store.verify(captured['artifact_id'])
        result = inspect_evidence(root / 'sample.json', root / 'wire.json')
        assert result['supported_trace_valid'] and result['word_payload_matches_wire']
        assert result['zero_payload_read_exchanges'] == 1
    assert len(seen) == 3 and robot.connects == robot.uploads == 0


def test_actual_io_trace_has_protocol_checks_without_named_value_binding(controller, tmp_path):
    port, seen, _, _ = controller
    path, _ = bridge_policy(tmp_path, port)
    with RobotBridge.from_config(path) as bridge:
        sampled = bridge.operations.sample('r1', ['DI[1]', 'DO[9]'])
        root = bridge.operations.store.verify(sampled['artifact_id'])
        result = inspect_evidence(root / 'sample.json', root / 'wire.json')
        assert result['supported_trace_valid'] and result['successful_read_exchanges'] == 2
        assert result['capture_kind'] == 'io_sample' and result['word_payload_matches_wire'] is None
        assert not result['io_value_binding_checked']
    assert len(seen) == 4


def test_release_keeps_previous_operations_and_includes_component_licenses():
    assert self_test()
    root = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'
    previous, _ = load_release(root / 'snpx_v1_1_0')
    current, _ = load_release(root / 'snpx_v1_2_0')
    assert current['version'] == '1.2.0' and current['operations'][:2] == previous['operations']
    assert {'LICENSE.txt', 'APACHE-2.0.txt', 'NOTICE.txt'} <= current['files'].keys()


def test_same_mcp_session_adds_inspection_and_rolls_back_with_48_tools(tmp_path):
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
            assert len(tools) == 48

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.is_error, response
                return json.loads(response.content[0].text)

            await call('list_operations', {})
            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_release(tmp_path / 'state/extensions', releases / 'snpx_v1_1_0')
            await call('reload_extensions', {})
            install_release(tmp_path / 'state/extensions', releases / 'snpx_v1_2_0')
            reloaded = await call('reload_extensions', {})
            assert not reloaded['mcp_tools_changed'] and not reloaded['controller_contacted']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.snpx.inspect_evidence'})
            args = {'artifact_id': imported['artifact_id'], 'capture_path': 'sample.json', 'wire_path': 'wire.json'}
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': args}
            result = (await call('run_read_operation', request))['result']
            assert result['supported_trace_valid'] and not result['controller_contacted']
            assert (await session.call_tool('run_read_operation', {**request,
                'arguments': {**args, 'wire_path': '../wire.json'}})).is_error
            install_release(tmp_path / 'state/extensions', releases / 'snpx_v1_1_0')
            await call('reload_extensions', {})
            assert (await session.call_tool('run_read_operation', request)).is_error
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools] == tools

    asyncio.run(scenario())
