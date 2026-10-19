"""Offline SNPX evidence inspection; no controller access.

Wire layout adapted from fanuc_ucl, Copyright 2026 Bowan Foryt, Apache-2.0.
See APACHE-2.0.txt and NOTICE.txt. Changes: bounded saved-trace analysis,
independent failure classifications, hash binding and raw-word correlation.
This checks this application's narrow client contract, not arbitrary SNPX.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct

from .decoder import _decode_data, _nonfinite, _unique


MAX_WIRE_BYTES, MAX_CAPTURE_BYTES = 1024 * 1024, 256 * 1024
MAX_EXCHANGES, MAX_FRAME_BYTES = 256, 512
_MAGIC = bytes.fromhex('08000100000000000001000000000000000100000000000000000000000001c0'
                       '00000000100e000001014f01000000000000000000000000')
_INIT_ACK = bytearray(56)
_INIT_ACK[0] = _INIT_ACK[8] = 1
_SEGMENTS = {8: 'assigned_words', 0x46: 'output_bits', 0x48: 'input_bits',
             0x0A: 'group_or_analog_outputs', 0x0C: 'group_or_analog_inputs'}


def _json_file(path, maximum):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Saved SNPX evidence exceeds its file-size bound')
    return data, _json_data(data)


def _json_data(data):
    try:
        parsed = json.loads(data.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid saved SNPX evidence JSON') from exc
    return parsed


def _hex(value):
    if (not isinstance(value, str) or len(value) > MAX_FRAME_BYTES * 2 or len(value) % 2
            or (value and not re.fullmatch(r'[0-9a-fA-F]+', value))):
        raise ValueError('Wire frames require bounded exact hexadecimal strings')
    return bytes.fromhex(value)


def _request(data, index):
    row = {'kind': 'unsupported', 'sequence': None, 'service': None, 'segment': None,
           'wire_index': None, 'count': None, 'expected_payload_bytes': None,
           'application_write_request_observed': False}
    if len(data) >= 56:
        service = data[50] if data[31] == 0x80 else data[42]
        row.update(sequence=struct.unpack_from('<H', data, 2)[0], service=service,
                   application_write_request_observed=struct.unpack_from('<H', data)[0] == 2
                       and data[31] in {0xC0, 0x80} and service in {7, 8, 9})
    if index == 0 and data == bytes(56):
        return {**row, 'kind': 'init', 'expected_payload_bytes': 0}, []
    if index == 1 and data == _MAGIC:
        return {**row, 'kind': 'session', 'expected_payload_bytes': 0}, []
    if len(data) != 56:
        return row, ['request_size_unsupported']
    if data[42] != 4 or data[31] != 0xC0:
        return row, ['request_outside_read_contract']
    segment, start, count = data[43], *struct.unpack_from('<HH', data, 44)
    row.update(kind='read', segment=segment, segment_name=_SEGMENTS.get(segment, 'unsupported'),
               wire_index=start, count=count)
    issues = []
    if (struct.unpack_from('<H', data)[0] != 2 or data[4:8] != bytes(4)
            or struct.unpack_from('<I', data, 8)[0] != 0x200
            or struct.unpack_from('<I', data, 16)[0] != 0x200
            or struct.unpack_from('<I', data, 36)[0] != 0xE10 or data[40:42] != bytes((1, 1))
            or any(data[12:16] + data[20:30] + data[32:36] + data[48:56])):
        issues.append('request_header_unsupported')
    if row['sequence'] != index or data[30] != index or index < 2 or index > 255:
        issues.append('request_sequence_mismatch')
    if segment == 8 and 1 <= count <= 128 and start + count <= 65535:
        row['expected_payload_bytes'] = count * 2
    elif segment in {0x46, 0x48} and count == 8 and start % 8 == 0 and start + count <= 7000:
        row['expected_payload_bytes'] = 1
    elif segment in {0x0A, 0x0C} and count == 1 and start < 2000:
        row['expected_payload_bytes'] = 2
    else:
        issues.append('read_span_or_segment_unsupported')
    if index < 2:
        issues.append('handshake_request_missing')
    return row, issues


def _reply(data, request):
    summary = {'received_bytes': len(data)}
    if not data:
        return summary, None, ['no_reply_recorded']
    if len(data) < 42:
        return summary, None, ['response_header_truncated']
    kind, seq, length = struct.unpack_from('<HHH', data)
    summary.update(packet_type=kind, sequence=seq, message_type=data[31], declared_payload_bytes=length)
    issues = []
    if len(data) != 56 + length:
        issues.append('response_truncated' if len(data) < 56 + length else 'response_extra_bytes')
    if request['kind'] == 'init':
        if data != bytes(_INIT_ACK):
            issues.append('init_not_acknowledged')
        return summary, None, issues
    if request['kind'] == 'session' and data == _MAGIC:
        return {**summary, 'session_reply': 'echo'}, None, issues
    if request['kind'] not in {'read', 'session'}:
        return summary, None, [*issues, 'reply_to_unsupported_request']
    size = request['expected_payload_bytes']
    if kind != 3:
        issues.append('response_packet_type_unsupported')
    if seq != request['sequence'] or data[30] != request['sequence']:
        issues.append('response_sequence_mismatch')
    if data[40:42] != bytes((1, 1)):
        issues.append('multipart_reply_unsupported')
    if data[31] == 0xD1:
        issues.append('controller_error_reply')
    if size is not None and length > size:
        issues.append('payload_exceeds_request')
    if len(data) < 56 or size is None:
        return summary, None, issues or ['response_body_unavailable']
    payload = None
    if data[31] == 0xD4 and length == 0 and size <= 6:
        if data[42:44] != bytes(2):
            issues.append('controller_status_nonzero')
        payload = data[44:44 + size]
    elif data[31] == 0x94:
        if size <= 6 or length != size:
            issues.append('extended_payload_size_unsupported')
        if data[48:50] != bytes((1, 1)):
            issues.append('multipart_reply_unsupported')
        if data[50] != 4:
            issues.append('extended_service_mismatch')
        if data[51] != request['segment']:
            issues.append('extended_segment_mismatch')
        if data[52:54] != bytes(2):
            issues.append('controller_status_nonzero')
        payload = data[56:]
    else:
        issues.append('response_message_type_unsupported')
    return summary, payload if not issues else None, list(dict.fromkeys(issues))


def inspect_evidence(capture_path: Path, wire_path: Path, *, offset=0, limit=100):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use offset >= 0 and limit 1-100')
    capture_data, capture = _json_file(capture_path, MAX_CAPTURE_BYTES)
    wire_data, wire = _json_file(wire_path, MAX_WIRE_BYTES)
    result = _inspect_loaded(capture_data, capture, wire_data, wire)
    return {**result, 'offset': offset, 'next_offset': offset + limit if offset + limit < result['total'] else None,
            'items': result['items'][offset:offset + limit]}


def _inspect_loaded(capture_data, capture, wire_data, wire):
    """Analyze already-read bytes; callers retain one consistent source snapshot."""
    if (not isinstance(capture, dict) or capture.get('provider') != 'snpx'
            or not isinstance(capture.get('errors'), dict)
            or not isinstance(wire, list) or len(wire) > MAX_EXCHANGES):
        raise ValueError('Supply a saved SNPX capture and bounded wire-exchange array')
    wire_hash = hashlib.sha256(wire_data).hexdigest()
    declared = capture.get('wire_sha256')
    valid_hash = isinstance(declared, str) and bool(re.fullmatch(r'[0-9a-f]{64}', declared))
    hash_matches = declared == wire_hash if valid_hash else None
    issues = [] if hash_matches else ['wire_hash_mismatch' if valid_hash else 'capture_wire_hash_missing_or_invalid']
    rows, read_rows, failed = [], [], False
    for index, exchange in enumerate(wire):
        if (not isinstance(exchange, dict) or set(exchange) - {'request_hex', 'response_hex', 'error_type'}
                or not {'request_hex', 'response_hex'} <= exchange.keys()):
            raise ValueError('Invalid SNPX wire exchange fields')
        reported = exchange.get('error_type')
        if reported is not None and (not isinstance(reported, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,64}', reported)):
            raise ValueError('Invalid reported SNPX transport error type')
        request_bytes, reply_bytes = _hex(exchange['request_hex']), _hex(exchange['response_hex'])
        request, row_issues = _request(request_bytes, index)
        response, payload, reply_issues = _reply(reply_bytes, request)
        row_issues.extend(reply_issues)
        if failed:
            row_issues.append('exchange_after_failure')
        if reported is not None:
            row_issues.append('reported_transport_error')
        row_issues = list(dict.fromkeys(row_issues))
        accepted = not row_issues
        payload = payload if accepted else None
        row = {'exchange': index, 'request': request, 'response': response,
               'reported_error_type': reported, 'issues': row_issues, 'accepted_by_read_contract': accepted,
               'payload_hex': payload.hex() if payload is not None and request['kind'] == 'read' else None,
               'payload_all_zero': all(byte == 0 for byte in payload)
                   if payload is not None and request['kind'] == 'read' else None}
        rows.append(row)
        if request['kind'] == 'read':
            read_rows.append(row)
        failed = failed or not accepted
    if not wire:
        issues.append('no_exchanges_recorded')
    if len(rows) < 2 or [row['request']['kind'] for row in rows[:2]] != ['init', 'session']:
        issues.append('handshake_incomplete')
    if not read_rows:
        issues.append('no_read_request_observed')
    if capture['errors']:
        issues.append('capture_reports_unavailable')
    correlation = None
    capture_kind = ('raw_words' if 'address' in capture or 'count' in capture
                    else 'io_sample' if isinstance(capture.get('values'), dict) else 'unsupported')
    if capture_kind == 'unsupported':
        issues.append('capture_shape_unsupported')
    if capture_kind == 'raw_words':
        try:
            decoded, _ = _decode_data(capture_data, [{'name': 'raw', 'type': 'u16', 'word_offset': 0,
                'byte_order': 'little', 'word_order': 'low_first'}])
        except ValueError:
            issues.append('capture_word_data_invalid')
            correlation = False
        else:
            if len(read_rows) != 1:
                issues.append('word_capture_read_count_mismatch')
                correlation = False
            else:
                read = read_rows[0]
                request = read['request']
                correlation = (decoded['available'] and read['accepted_by_read_contract'] and request['segment'] == 8
                    and request['wire_index'] + 1 == decoded['address'] and request['count'] == decoded['count']
                    and read['payload_hex'] == capture['raw_hex'].lower())
                if decoded['available'] and not correlation:
                    issues.append('capture_payload_not_supported_by_wire')
                if not decoded['available'] and read['accepted_by_read_contract']:
                    issues.append('capture_unavailable_despite_complete_read')
    counts = dict(sorted(Counter(issue for row in rows for issue in row['issues']).items()))
    return {'capture_source_sha256': hashlib.sha256(capture_data).hexdigest(), 'capture_source_bytes': len(capture_data),
            'wire_source_sha256': wire_hash, 'wire_source_bytes': len(wire_data), 'wire_hash_matches_capture': hash_matches,
            'capture_kind': capture_kind, 'word_payload_matches_wire': correlation,
            'io_value_binding_checked': False, 'issues': list(dict.fromkeys(issues)), 'exchange_issue_counts': counts,
            'supported_trace_valid': hash_matches is True and not failed and not issues,
            'successful_read_exchanges': sum(row['accepted_by_read_contract'] for row in read_rows),
            'zero_payload_read_exchanges': sum(row['payload_all_zero'] is True for row in read_rows),
            'application_write_requests_observed': sum(row['request']['application_write_request_observed'] for row in rows),
            'total': len(rows), 'offset': 0, 'next_offset': None,
            'items': rows, 'timing_available': False, 'transport_cause_verified': False,
            'mapping_verified': False, 'capture_provenance_verified': False, 'controller_compatibility_verified': False,
            'controller_contacted': False, 'contract': 'saved evidence checked against this application read protocol only'}


def self_test():
    request, issues = _request(bytes(56), 0)
    _, _, reply_issues = _reply(bytes(_INIT_ACK), request)
    return request['kind'] == 'init' and not issues and not reply_issues and len(_MAGIC) == 56
