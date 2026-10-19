import hashlib
import json
import asyncio
import os
from pathlib import Path
import socketserver
import struct
import sys
import threading
import time

import pytest

from fanuc_workbench.services.snpx import SnpxError, SnpxReader, SnpxReadPolicy, bit_target, io_target
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
from test_robot_bridge import MemoryRobot


@pytest.fixture
def controller():
    seen, replies, failures = [], {}, []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:
                while True:
                    request = bytearray()
                    while len(request) < 56:
                        chunk = self.request.recv(56 - len(request))
                        if not chunk:
                            return
                        request.extend(chunk)
                    request = bytes(request)
                    seen.append(request)
                    # Independent synthetic server, accepts only session setup and reads.
                    if request == bytes(56):
                        response = bytearray(56)
                        response[0] = response[8] = 1
                    elif request[42:44] == b'\x4f\x01':
                        response = request
                    else:
                        assert request[0:2] == b'\x02\0' and request[31] == 0xc0
                        assert request[42] == 4 and request[43] in {8, 0x46, 0x48, 0x0A, 0x0C}
                        seq = request[30]
                        segment, index, count = request[43], *struct.unpack_from('<HH', request, 44)
                        size = count * 2 if segment in {8, 0x0A, 0x0C} else (count + 7) // 8
                        data = replies.get((segment, index), bytes(size))
                        response = bytearray(56 + (size if size > 6 else 0))
                        struct.pack_into('<HH', response, 0, 3, seq)
                        response[30] = seq
                        response[40:42] = b'\x01\x01'
                        if size <= 6:
                            response[31] = 0xd4
                            response[44:44 + len(data)] = data
                        else:
                            response[31] = 0x94
                            struct.pack_into('<H', response, 4, size)
                            response[48:52] = bytes((1, 1, 4, segment))
                            response[56:] = data
                    if failures:
                        action = failures.pop(0)
                        response = action(bytearray(response))
                    if response is None:
                        return
                    # Deliberately split frames across TCP writes.
                    self.request.sendall(response[:17])
                    self.request.sendall(response[17:])
            except OSError:
                # Malformed-response tests deliberately close/reset the stream.
                pass
            except AssertionError as exc:
                errors.append(exc)

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        block_on_close = False

    errors = []
    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen, replies, failures
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not errors


def policy(port):
    return SnpxReadPolicy.load({'port': port, 'io_targets': ['DI[1]', 'DO[9]', 'RI[1]', 'UO[8]'],
                               'word_ranges': [{'address': 1, 'count': 16}]})


def test_reader_handshake_inline_extended_words_and_fixed_bits(controller):
    port, seen, replies, _ = controller
    replies[8, 0] = struct.pack('<HH', 0x8000, 65535)
    replies[8, 4] = struct.pack('<4H', 1, 2, 3, 4)
    replies[0x48, 0] = b'\x01'
    replies[0x46, 8] = b'\x01'
    replies[0x48, 5000] = b'\x01'
    replies[0x46, 6000] = b'\x80'
    with SnpxReader('127.0.0.1', policy(port), timeout=2) as client:
        assert struct.unpack('<HH', client.read_words(1, 2)) == (32768, 65535)
        assert struct.unpack('<4H', client.read_words(5, 4)) == (1, 2, 3, 4)
        for name in ['DI[1]', 'DO[9]', 'RI[1]', 'UO[8]']:
            assert client.read_io(name) is True
    assert len(seen) == 8
    assert seen[0] == bytes(56)
    assert seen[1].hex() == ('08000100000000000001000000000000000100000000000000000000000001c0'
                             '00000000100e000001014f01000000000000000000000000')
    assert seen[2].hex() == ('02000200000000000002000000000000000200000000000000000000000002c0'
                             '00000000100e000001010408000002000000000000000000')
    assert all(bytes.fromhex(row['request_hex']) == request for row, request in zip(client.transcript, seen))
    assert all(b'CLRASG' not in request and b'SETASG' not in request for request in seen)
    assert client.sock is None


@pytest.mark.parametrize('data', [False, {}, {'unknown': 1}, {'port': True, 'io_targets': ['DI[1]']},
    {'io_targets': ['R[1]']}, {'io_targets': ['DI[0]']}, {'io_targets': ['DI[5001]']},
    {'io_targets': ['DI[1]', 'DI[1]']}, {'io_targets': ['di[1]']}, {'io_targets': 'DI[1]'},
    {'word_ranges': [{'address': 0, 'count': 1}]}, {'word_ranges': [{'address': 1, 'count': 129}]},
    {'word_ranges': [{'address': 65535, 'count': 2}]}, {'word_ranges': [{'address': True, 'count': 2}]},
    {'word_ranges': [{'address': 1, 'count': 2}, {'address': 2, 'count': 1}]},
    {'word_ranges': [{'address': 1, 'count': 1, 'type': 'R'}]}])
def test_invalid_read_policy(data):
    with pytest.raises(ValueError):
        SnpxReadPolicy.load(data)


def test_policy_refuses_unlisted_targets_before_sending(controller):
    port, seen, _, _ = controller
    with SnpxReader('127.0.0.1', policy(port)) as client:
        for address, count in [(17, 1), (16, 2), (1, True)]:
            with pytest.raises(ValueError):
                client.read_words(address, count)
        with pytest.raises(ValueError):
            client.read_io('DI[2]')
    assert len(seen) == 2


@pytest.mark.parametrize('mutate', [
    lambda frame: frame[:20],
    lambda frame: frame[:4] + b'\xff\xff' + frame[6:],
    lambda frame: bytearray([2]) + frame[1:],
    lambda frame: frame[:8] + b'\0' + frame[9:],
])
def test_invalid_handshake_closes_connection(controller, mutate):
    port, seen, _, failures = controller
    failures.append(mutate)
    client = SnpxReader('127.0.0.1', policy(port), timeout=0.1)
    with pytest.raises(SnpxError):
        with client:
            pytest.fail('Invalid handshake accepted')
    assert len(seen) == 1 and client.sock is None
    assert client.transcript[0]['response_hex']


@pytest.mark.parametrize('offset,value', [(0, 2), (2, 9), (30, 9), (31, 0xd1), (40, 2), (42, 1)])
def test_invalid_read_response_is_not_a_zero_value(controller, offset, value):
    port, seen, _, failures = controller
    with SnpxReader('127.0.0.1', policy(port), timeout=1) as client:
        def mutate(frame):
            frame[offset] = value
            return frame
        failures.append(mutate)
        with pytest.raises(SnpxError):
            client.read_words(1, 2)
        assert client.sock is None
    assert len(seen) == 3


@pytest.mark.parametrize('target,expected', [('DI[5000]', (0x48, 4999)), ('RI[1000]', (0x48, 5999)),
                                         ('UI[1000]', (0x48, 6999)), ('RO[1]', (0x46, 5000))])
def test_fixed_port_boundaries(target, expected):
    assert bit_target(target) == expected


def test_maximum_word_payload_has_a_bounded_frame(controller):
    port, _, _, _ = controller
    access = SnpxReadPolicy.load({'port': port, 'word_ranges': [{'address': 65408, 'count': 128}]})
    with SnpxReader('127.0.0.1', access, timeout=1) as client:
        assert client.read_words(65408, 128) == bytes(256)
    assert len(bytes.fromhex(client.transcript[-1]['response_hex'])) == 312


@pytest.mark.parametrize('offset,value', [(4, 9), (48, 2), (49, 2), (50, 7), (51, 0x38), (52, 1)])
def test_extended_response_refuses_wrong_length_fragment_service_or_status(controller, offset, value):
    port, _, _, failures = controller
    with SnpxReader('127.0.0.1', policy(port), timeout=1) as client:
        def mutate(frame):
            frame[offset] = value
            return frame
        failures.append(mutate)
        with pytest.raises(SnpxError):
            client.read_words(1, 4)
        assert client.sock is None


def test_session_handshake_accepts_a_short_ack(controller):
    port, _, _, failures = controller
    def ack(_):
        frame = bytearray(56)
        struct.pack_into('<HH', frame, 0, 3, 1)
        frame[30:32], frame[40:42] = b'\x01\xd4', b'\x01\x01'
        return frame
    failures.extend([lambda frame: frame, ack])
    with SnpxReader('127.0.0.1', policy(port), timeout=1) as client:
        assert client.read_words(1, 1) == b'\0\0'


def test_one_deadline_covers_handshake_and_reads(controller):
    port, seen, _, failures = controller
    def delayed(frame):
        time.sleep(0.2)
        return frame
    failures.extend([delayed] * 3)
    client = SnpxReader('127.0.0.1', policy(port), timeout=0.5)
    started = time.monotonic()
    with pytest.raises(SnpxError):
        with client:
            client.read_words(1, 1)
    assert time.monotonic() - started < 1.5 and client.sock is None
    assert len(seen) <= 3 and client.transcript[-1].get('error_type')


def bridge_policy(tmp_path, port, **fields):
    document = {'state_dir': 'state', 'source_dir': 'edits', 'allow_writes': False,
        'robots': {'r1': {'profile': {'name': 'Synthetic controller', 'host': '127.0.0.1', 'timeout': 1},
            'sample_provider': 'snpx', 'snpx_read': {'port': port, 'io_targets': ['DI[1]', 'DO[9]'],
            'word_ranges': [{'address': 1, 'count': 16}]}, **fields}}}
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path, document


def test_bridge_catalog_raw_words_hash_evidence_and_read_only(controller, tmp_path):
    port, seen, replies, _ = controller
    replies[8, 0] = struct.pack('<4H', 1, 0, 32768, 65535)
    path, _ = bridge_policy(tmp_path, port)
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        assert bridge.list_robots()['robots'][0]['sample_provider'] == 'snpx'
        assert bridge.operations.capabilities('r1')['snpx_reads']['enabled']
        assert not seen  # Configuration, discovery and capabilities never connect.
        descriptor = bridge.extensions.describe('core.snpx.read_words')
        result = bridge.extensions.run_read(descriptor['id'], {'robot_id': 'r1', 'address': 1, 'count': 4}, descriptor['revision'])['result']
        assert result['words_u16'] == [1, 0, 32768, 65535]
        assert result['words_i16'] == [1, 0, -32768, -1]
        assert result['raw_hex'] == replies[8, 0].hex() and result['errors'] == {}
        assert not result['mapping_verified'] and not result['controller_written']
        folder = bridge.operations.store.verify(result['artifact_id'])
        assert hashlib.sha256((folder / 'wire.json').read_bytes()).hexdigest() == result['wire_sha256']
        assert json.loads((folder / 'sample.json').read_text())['host'] == '127.0.0.1'
        assert bridge.config.read_only
        with pytest.raises(ValueError, match='allowlist'):
            bridge.snpx.read_words('r1', 16, 2)
        with pytest.raises(ValueError, match='revision'):
            bridge.extensions.run_read(descriptor['id'], {'robot_id': 'r1', 'address': 1}, 'old')
        with pytest.raises(ValueError):
            bridge.extensions.run_read(descriptor['id'], {'robot_id': 'r1', 'address': 1, 'host': '192.0.2.1'}, descriptor['revision'])
        events = [json.loads(line) for line in (bridge.config.state_dir / 'audit.jsonl').read_text().splitlines()]
        assert any(event['action'] == 'snpx_read' for event in events)
    assert len(seen) == 3 and robot.connects == robot.uploads == 0


def test_snpx_samples_use_fixed_bits_and_keep_shared_rate_limit(controller, tmp_path):
    port, seen, replies, _ = controller
    replies[0x48, 0], replies[0x46, 8] = b'\x01', b'\x00'
    path, _ = bridge_policy(tmp_path, port, native_provider='frrobot')
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        result = bridge.operations.sample('r1', [' di [ 1 ] ', 'DO[9]'])
        assert result['values'] == {' di [ 1 ] ': True, 'DO[9]': False}
        assert not result['errors'] and not result['capture_is_atomic']
        assert bridge.operations.store.verify(result['artifact_id'])
        with pytest.raises(ValueError, match='two seconds'):
            bridge.operations.sample('r1', ['DI[1]'])
    assert len(seen) == 4 and robot.connects == robot.uploads == 0


@pytest.mark.parametrize('expressions', [['R[1]'], ['PR[1]'], ['DI[2]'], ['F[1]'], ['DI[1]', 'di[01]']])
def test_snpx_sample_refuses_unlisted_or_unsupported_without_network(controller, tmp_path, expressions):
    port, seen, _, _ = controller
    path, _ = bridge_policy(tmp_path, port)
    with RobotBridge.from_config(path) as bridge:
        with pytest.raises(ValueError):
            bridge.operations.sample('r1', expressions)
    assert not seen


def test_failed_snpx_sample_retains_partial_values_and_no_file_fallback(controller, tmp_path):
    port, seen, replies, failures = controller
    replies[0x48, 0] = b'\x01'
    failures.extend([lambda frame: frame] * 3 + [lambda frame: frame[:31] + b'\xd1' + frame[32:]])
    path, _ = bridge_policy(tmp_path, port)
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        result = bridge.operations.sample('r1', ['DI[1]', 'DO[9]'])
        assert result['values'] == {'DI[1]': True, 'DO[9]': None}
        assert set(result['errors']) == {'DO[9]'}
        folder = bridge.operations.store.verify(result['artifact_id'])
        assert len(json.loads((folder / 'wire.json').read_text())) == 4
    assert len(seen) == 4 and robot.connects == robot.uploads == 0


def test_word_failure_returns_unavailable_with_preserved_wire(controller, tmp_path):
    port, seen, _, failures = controller
    failures.extend([lambda frame: frame] * 2 + [lambda frame: frame[:30] + b'\x09' + frame[31:]])
    path, _ = bridge_policy(tmp_path, port)
    with RobotBridge.from_config(path) as bridge:
        result = bridge.snpx.read_words('r1', 1, 1)
        assert result['words_u16'] is result['words_i16'] is result['raw_hex'] is None
        assert 'words' in result['errors']
        assert bridge.operations.store.verify(result['artifact_id'])
    assert len(seen) == 3


def test_snpx_is_opt_in_and_auto_sampling_stays_file_based(controller, tmp_path):
    port, seen, _, _ = controller
    path, document = bridge_policy(tmp_path, port, sample_provider='auto')
    robot = MemoryRobot()
    robot.files['md:NUMREG.VA'] = b"[1] = 42 'Count'\r\n"
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        assert bridge.operations.sample('r1', ['R[1]'])['values'] == {'R[1]': '42'}
    assert not seen and robot.connects > 0
    document['robots']['r1'].pop('snpx_read')
    path.write_text(json.dumps(document), encoding='utf-8')
    with RobotBridge.from_config(path) as bridge:
        with pytest.raises(ValueError, match='disabled'):
            bridge.snpx.read_words('r1', 1)
    assert not seen


@pytest.mark.parametrize('fields', [{'sample_provider': 'snpx', 'snpx_read': None},
    {'sample_provider': 'snpx', 'snpx_read': {'word_ranges': [{'address': 1, 'count': 1}]}},
    {'sample_provider': 'unknown'}, {'sample_provider': []}])
def test_sampling_policy_validation(tmp_path, fields):
    path, _ = bridge_policy(tmp_path, 60008, **fields)
    with pytest.raises(ValueError):
        BridgeConfig.load(path)


def test_connection_reload_preserves_snpx_policy_and_refuses_mapping_or_port_changes(tmp_path):
    path, document = bridge_policy(tmp_path, 60008)
    with RobotBridge.from_config(path) as bridge:
        document['robots']['r1']['profile']['host'] = '192.0.2.2'
        path.write_text(json.dumps(document), encoding='utf-8')
        assert bridge.reload_connections()['status'] == 'reloaded'
        assert bridge.config.robots['r1'].snpx_read.port == 60008
        document['robots']['r1']['snpx_read']['port'] = 60009
        path.write_text(json.dumps(document), encoding='utf-8')
        with pytest.raises(ValueError, match='cannot change'):
            bridge.reload_connections()
        document['robots']['r1']['snpx_read']['port'] = 60008
        document['robots']['r1']['snpx_read']['word_ranges'][0]['address'] = 20
        path.write_text(json.dumps(document), encoding='utf-8')
        with pytest.raises(ValueError, match='cannot change'):
            bridge.reload_connections()


def test_mcp_catalog_reads_snpx_without_adding_tools(controller, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    port, seen, replies, _ = controller
    replies[8, 0] = struct.pack('<H', 1234)
    replies[0x0C, 0], replies[0x0A, 1000] = b'\xff\xff', b'\x00\x80'
    path, _ = bridge_policy(tmp_path, port, snpx_read={'port': port,
        'io_targets': ['GI[1]', 'AO[1]'], 'io_word_formats': {'GI[1]': 'u16', 'AO[1]': 'i16'},
        'word_ranges': [{'address': 1, 'count': 1}]})

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(path)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]
            assert len(tools) == 48

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.is_error, response
                return json.loads(response.content[0].text)

            descriptor = await call('describe_operation', {'operation_id': 'core.snpx.read_words'})
            assert descriptor['effect'] == 'read'
            result = (await call('run_read_operation', {'operation_id': descriptor['id'],
                'revision': descriptor['revision'], 'arguments': {'robot_id': 'r1', 'address': 1}}))['result']
            assert result['words_u16'] == [1234] and not result['mapping_verified']
            sampled = await call('sample_robot', {'robot_id': 'r1', 'expressions': ['GI[1]', 'AO[1]']})
            assert sampled['values'] == {'GI[1]': 65535, 'AO[1]': -32768}
            assert sampled['value_formats'] == {'GI[1]': 'u16', 'AO[1]': 'i16'}
            assert not sampled['scaling_applied']
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools] == tools
    asyncio.run(scenario())
    assert len(seen) == 7


@pytest.mark.parametrize('target,expected', [
    ('GI[1]', (0x0C, 0)), ('GO[1]', (0x0A, 0)),
    ('AI[1]', (0x0C, 1000)), ('AO[1]', (0x0A, 1000)),
    ('GI[1000]', (0x0C, 999)), ('GO[1000]', (0x0A, 999)),
    ('AI[1000]', (0x0C, 1999)), ('AO[1000]', (0x0A, 1999))])
def test_word_io_offsets_and_boundaries(target, expected):
    assert io_target(target) == expected


@pytest.mark.parametrize('targets,formats', [
    (['GI[1]'], {}), (['AI[1]'], {'AI[1]': 'float'}),
    (['GO[1]'], {'GO[1]': 16}), (['AO[1]'], {'AO[1]': None}),
    (['GI[1]'], {'GI[1]': 'u16', 'DI[1]': 'u16'}),
    (['DI[1]'], {'DI[1]': 'i16'}), (['GO[1]'], {'GO[2]': 'i16'}),
    (['AO[1]'], ['i16']), (['AO[1]'], None),
    (['GI[1001]'], {'GI[1001]': 'u16'}), (['AI[1001]'], {'AI[1001]': 'i16'}),
    (['GI[0]'], {'GI[0]': 'u16'}), (['AI[01]'], {'AI[01]': 'i16'})])
def test_word_io_requires_exact_explicit_formats_and_bounded_targets(targets, formats):
    with pytest.raises(ValueError):
        SnpxReadPolicy.load({'io_targets': targets, 'io_word_formats': formats})


def test_all_word_io_ports_preserve_signedness_and_exact_read_addresses(controller):
    port, seen, replies, _ = controller
    cases = [('GI[1000]', 0x0C, 999), ('GO[1000]', 0x0A, 999),
             ('AI[1]', 0x0C, 1000), ('AO[1]', 0x0A, 1000)]
    for format_name in ['u16', 'i16']:
        access = SnpxReadPolicy.load({'port': port, 'io_targets': [name for name, _, _ in cases],
                                     'io_word_formats': {name: format_name for name, _, _ in cases}})
        with SnpxReader('127.0.0.1', access, timeout=3) as client:
            for word in [0, 32767, 32768, 65535]:
                for target, segment, index in cases:
                    replies[segment, index] = struct.pack('<H', word)
                    value = client.read_io(target)
                    assert type(value) is int
                    assert value == (word if format_name == 'u16' or word < 32768 else word - 65536)
    reads = [frame for frame in seen if frame[42] == 4]
    assert len(reads) == 32
    assert all(struct.unpack_from('<H', frame, 46)[0] == 1 for frame in reads)
    assert {(frame[43], struct.unpack_from('<H', frame, 44)[0]) for frame in reads} == {
        (segment, index) for _, segment, index in cases}


def test_mixed_io_sample_retains_decoding_policy_and_raw_evidence(controller, tmp_path):
    port, seen, replies, _ = controller
    replies.update({(0x48, 0): b'\x01', (0x46, 8): b'\x00', (0x0C, 0): b'\xff\xff',
                    (0x0A, 1): b'\x00\x80', (0x0C, 1000): b'\x00\x00', (0x0A, 1001): b'\xff\xff'})
    targets = ['DI[1]', 'DO[9]', 'GI[1]', 'GO[2]', 'AI[1]', 'AO[2]']
    formats = {'GI[1]': 'u16', 'GO[2]': 'u16', 'AI[1]': 'i16', 'AO[2]': 'i16'}
    path, _ = bridge_policy(tmp_path, port, snpx_read={'port': port, 'io_targets': targets, 'io_word_formats': formats})
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        result = bridge.operations.sample('r1', targets)
        assert result['values'] == {'DI[1]': True, 'DO[9]': False, 'GI[1]': 65535,
                                    'GO[2]': 32768, 'AI[1]': 0, 'AO[2]': -1}
        assert type(result['values']['AI[1]']) is int
        assert result['value_formats'] == {'DI[1]': 'bool', 'DO[9]': 'bool', **formats}
        assert not result['errors'] and not result['scaling_applied']
        root = bridge.operations.store.verify(result['artifact_id'])
        wire = (root / 'wire.json').read_bytes()
        assert result['wire_sha256'] == hashlib.sha256(wire).hexdigest()
        saved = json.loads((root / 'sample.json').read_text())
        assert dict(saved['policy']['io_word_formats']) == formats
        assert saved['value_formats'] == result['value_formats']
        assert bridge.operations.capabilities('r1')['snpx_reads']['word_io']['units'] == 'raw counts'
    assert len(seen) == 8 and robot.connects == robot.uploads == 0


def test_failed_word_io_is_null_with_partial_sample_and_no_fallback(controller, tmp_path):
    port, seen, replies, failures = controller
    replies[0x0C, 0] = b'\x00\x00'
    failures.extend([lambda frame: frame] * 3 + [lambda frame: frame[:42] + b'\x01' + frame[43:]])
    targets = ['GI[1]', 'AO[1]', 'AI[1]']
    path, _ = bridge_policy(tmp_path, port, snpx_read={'port': port, 'io_targets': targets,
        'io_word_formats': {'GI[1]': 'u16', 'AO[1]': 'i16', 'AI[1]': 'i16'}})
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        result = bridge.operations.sample('r1', targets)
        assert result['values'] == {'GI[1]': 0, 'AO[1]': None, 'AI[1]': None}
        assert set(result['errors']) == {'AO[1]', 'AI[1]'}
        assert result['value_formats'] == {'GI[1]': 'u16', 'AO[1]': 'i16', 'AI[1]': 'i16'}
        assert bridge.operations.store.verify(result['artifact_id'])
    assert len(seen) == 4 and robot.connects == robot.uploads == 0


def test_word_formats_cannot_be_changed_with_endpoint_reload(controller, tmp_path):
    port, seen, _, _ = controller
    path, document = bridge_policy(tmp_path, port, snpx_read={'port': port, 'io_targets': ['GI[1]'],
                                                          'io_word_formats': {'GI[1]': 'u16'}})
    with RobotBridge.from_config(path) as bridge:
        document['robots']['r1']['snpx_read']['io_word_formats']['GI[1]'] = 'i16'
        path.write_text(json.dumps(document), encoding='utf-8')
        with pytest.raises(ValueError, match='cannot change'):
            bridge.reload_connections()
    assert not seen
