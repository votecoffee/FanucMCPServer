"""Offline I/O value checks against retained SNPX replies.

Fixed port layout adapted from fanuc_ucl, Copyright 2026 Bowan Foryt,
Apache-2.0; see APACHE-2.0.txt and NOTICE.txt. Changes: bounded comparison
against explicit caller-supplied read order, with no connection or assignment.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import struct

from .wire import MAX_CAPTURE_BYTES, MAX_WIRE_BYTES, _inspect_loaded, _json_file


_BITS = {'DI': (0x48, 0, 5000), 'DO': (0x46, 0, 5000),
         'RI': (0x48, 5000, 1000), 'RO': (0x46, 5000, 1000),
         'UI': (0x48, 6000, 1000), 'UO': (0x46, 6000, 1000)}
_WORDS = {'GI': (0x0C, 0), 'GO': (0x0A, 0), 'AI': (0x0C, 1000), 'AO': (0x0A, 1000)}
_EXPRESSION = re.compile(r'\s*(DI|DO|RI|RO|UI|UO|GI|GO|AI|AO)\s*\[\s*([0-9]{1,5})\s*\]\s*', re.I)


def _target(expression):
    match = _EXPRESSION.fullmatch(expression) if isinstance(expression, str) and len(expression) <= 128 else None
    if match is None:
        raise ValueError('Supply bounded digital/group/analog I/O expressions')
    port, number = match[1].upper(), int(match[2])
    if port in _BITS:
        segment, base, maximum = _BITS[port]
        if not 1 <= number <= maximum:
            raise ValueError('Digital index exceeds its fixed port range')
        index = base + number - 1
        return {'target': f'{port}[{number}]', 'segment': segment, 'wire_index': index - index % 8,
                'count': 8, 'bit_offset': index % 8, 'payload_bytes': 1, 'digital': True}
    if not 1 <= number <= 1000:
        raise ValueError('Word I/O indices must be between 1 and 1000')
    segment, base = _WORDS[port]
    return {'target': f'{port}[{number}]', 'segment': segment, 'wire_index': base + number - 1,
            'count': 1, 'bit_offset': None, 'payload_bytes': 2, 'digital': False}


def _expressions(expressions):
    if not isinstance(expressions, list) or not 1 <= len(expressions) <= 100:
        raise ValueError('Supply 1-100 expressions in the intended read order')
    targets = [_target(expression) for expression in expressions]
    if len({target['target'] for target in targets}) != len(targets):
        raise ValueError('Expressions must identify distinct normalized I/O targets')
    return targets


def _capture(capture, expressions, targets):
    if (not isinstance(capture, dict) or capture.get('provider') != 'snpx'
            or 'address' in capture or 'count' in capture or capture.get('scaling_applied') is not False
            or not isinstance(capture.get('values'), dict) or set(capture['values']) != set(expressions)
            or not isinstance(capture.get('errors'), dict) or set(capture['errors']) - set(expressions)
            or not isinstance(capture.get('value_formats'), dict) or set(capture['value_formats']) != set(expressions)):
        raise ValueError('Supply an unscaled SNPX I/O capture with exact expression and format metadata')
    if any(not isinstance(error, str) or len(error) > 1024 for error in capture['errors'].values()):
        raise ValueError('Capture errors require bounded strings')
    for expression, target in zip(expressions, targets):
        format_name = capture['value_formats'][expression]
        if format_name not in (('bool',) if target['digital'] else ('u16', 'i16')):
            raise ValueError('Digital I/O requires bool; word I/O requires explicit u16/i16 metadata')
        value = capture['values'][expression]
        if value is not None and (type(value) not in {bool, int} or not -32768 <= value <= 65535):
            raise ValueError('Captured I/O values require bounded bool/int/null data')


def _row(expression, target, format_name, value, error, read, hash_matches, shared):
    issues, decoded, matches = [], None, None
    address_matches = (all(read['request'][key] == target[key] for key in ['segment', 'wire_index', 'count'])
                       if read else None)
    if not hash_matches:
        issues.append('capture_wire_hash_not_bound')
    if read is None:
        issues.append('read_exchange_missing')
    elif not address_matches:
        issues.append('request_differs_from_supplied_order')
    elif not read['accepted_by_read_contract'] or read['payload_hex'] is None:
        issues.append('read_payload_unavailable')
    elif hash_matches:
        payload = bytes.fromhex(read['payload_hex'])
        if len(payload) != target['payload_bytes']:
            issues.append('read_payload_size_invalid')
        else:
            decoded = (bool(payload[0] & (1 << target['bit_offset'])) if target['digital']
                       else struct.unpack('<H' if format_name == 'u16' else '<h', payload)[0])
    # An empty error string still marks the captured observation unavailable.
    available = value is not None and error is None
    if not available:
        issues.append('capture_value_unavailable')
    valid_type = (type(value) is bool if target['digital'] else type(value) is int
                  and ((0 <= value <= 65535) if format_name == 'u16' else (-32768 <= value <= 32767)))
    if available and not valid_type:
        issues.append('capture_value_type_or_range_invalid')
    if decoded is not None and available and valid_type:
        matches = value == decoded
        if not matches:
            issues.append('capture_value_differs_from_payload')
    return {'expression': expression, 'target': target['target'], 'format': format_name,
            'expected_request': {key: target[key] for key in ['segment', 'wire_index', 'count', 'bit_offset']},
            'wire_exchange': read['exchange'] if read else None, 'request_matches_supplied_order': address_matches,
            'shared_request_address': shared, 'capture_value': value, 'capture_error': error,
            'decoded_wire_value': decoded, 'matches_under_supplied_order': matches,
            'status': 'matched' if matches is True else 'mismatch' if matches is False else 'unavailable',
            'issues': issues}


def inspect_io_evidence(capture_path: Path, wire_path: Path, expressions: list[str], *, offset=0, limit=100):
    targets = _expressions(expressions)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use offset >= 0 and limit 1-100')
    capture_data, capture = _json_file(capture_path, MAX_CAPTURE_BYTES)
    wire_data, wire = _json_file(wire_path, MAX_WIRE_BYTES)
    _capture(capture, expressions, targets)
    protocol = _inspect_loaded(capture_data, capture, wire_data, wire)
    reads = [row for row in protocol['items'] if row['request']['kind'] == 'read']
    signatures = Counter((target['segment'], target['wire_index'], target['count']) for target in targets)
    rows = [_row(expression, target, capture['value_formats'][expression], capture['values'][expression],
                 capture['errors'].get(expression) if expression in capture['errors'] else None,
                 reads[index] if index < len(reads) else None, protocol['wire_hash_matches_capture'] is True,
                 signatures[target['segment'], target['wire_index'], target['count']] > 1)
            for index, (expression, target) in enumerate(zip(expressions, targets))]
    counts = {status: sum(row['status'] == status for row in rows) for status in ['matched', 'mismatch', 'unavailable']}
    root_issues = [] if len(reads) == len(expressions) else ['read_count_differs_from_expression_count']
    return {key: protocol[key] for key in ['capture_source_sha256', 'capture_source_bytes', 'wire_source_sha256',
        'wire_source_bytes']} | {
        'wire_evidence': {key: protocol[key] for key in ['supported_trace_valid', 'wire_hash_matches_capture',
            'successful_read_exchanges', 'zero_payload_read_exchanges', 'application_write_requests_observed',
            'issues', 'exchange_issue_counts']}, 'issues': root_issues,
        'summary': counts, 'read_exchanges_total': len(reads), 'total': len(rows), 'offset': offset,
        'next_offset': offset + limit if offset + limit < len(rows) else None, 'items': rows[offset:offset + limit],
        'request_order_matches_supplied_expressions': len(reads) == len(rows)
            and all(row['request_matches_supplied_order'] is True for row in rows),
        'all_values_match_under_supplied_order': protocol['supported_trace_valid'] and not root_issues
            and counts['matched'] == len(rows),
        'io_value_binding_checked': True, 'expression_order_verified': False,
        'shared_request_addresses': any(row['shared_request_address'] for row in rows),
        'value_format_metadata_verified': False, 'mapping_verified': False, 'capture_provenance_verified': False,
        'controller_compatibility_verified': False, 'physical_identity_verified': False,
        'capture_is_atomic': False, 'scaling_applied': False, 'controller_contacted': False,
        'controller_written': False, 'contract': 'saved I/O comparison under explicit caller-supplied read order'}


def self_test():
    return _target('DI[5000]')['wire_index'] == 4992 and _target(' AO[1] ')['wire_index'] == 1000
