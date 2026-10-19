import hashlib
import json
import socketserver
import struct
import threading

import pytest

from fanuc_workbench.services.snpx import SnpxError, SnpxReader, SnpxReadPolicy, bit_target


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
                        assert request[42] == 4 and request[43] in {8, 0x46, 0x48}
                        seq = request[30]
                        segment, index, count = request[43], *struct.unpack_from('<HH', request, 44)
                        size = count * 2 if segment == 8 else (count + 7) // 8
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
