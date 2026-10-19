"""Offline SNPX decoding and comparison; MIT, see LICENSE.txt.

Host API 1 only. Configured endpoint equality does not authenticate a robot or
verify assignments. Tolerances apply to decoded floats; raw changes stay visible.
"""
from __future__ import annotations

import math
from pathlib import Path
import re

from .decoder import FORMATS, _decode_data, _fields, _read, decode_capture
from .decoder import self_test as decoder_self_test
from .wire import inspect_evidence
from .wire import self_test as wire_self_test


def _origin(capture):
    robot, host, port = capture.get('robot_id'), capture.get('host'), capture.get('port')
    if (not isinstance(robot, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', robot)
            or not isinstance(host, str) or not 1 <= len(host) <= 253 or host != host.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in host)
            or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('Comparison requires complete configured robot_id, host and port metadata')
    return {'robot_id': robot, 'host': host.casefold(), 'port': port}


def _options(absolute_tolerance, relative_tolerance, changes_only, offset, limit):
    for value, maximum in [(absolute_tolerance, 1e100), (relative_tolerance, 1)]:
        if type(value) not in {int, float} or not 0 <= value <= maximum or not math.isfinite(value):
            raise ValueError('Use finite tolerances: absolute 0-1e100 and relative 0-1')
    if type(changes_only) is not bool or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use boolean changes_only, offset >= 0 and limit 1-100')


def _element(before, after, index, absolute_tolerance, relative_tolerance):
    kind = before['type']
    left, right = before['values'][index], after['values'][index]
    left_raw, right_raw = before['raw_hex'][index], after['raw_hex'][index]
    row = {'name': before['name'], 'element': index, 'type': kind,
           'word_address': before['word_addresses'][index],
           'byte_order': before['byte_order'], 'word_order': before['word_order'],
           'before_value': left, 'after_value': right, 'before_raw_hex': left_raw, 'after_raw_hex': right_raw,
           'raw_changed': left_raw != right_raw if left_raw is not None and right_raw is not None else None,
           'delta': None, 'delta_encoding': before['value_encoding'], 'delta_error': None,
           'before_error': next((error['reason'] for error in before['errors'] if error['element'] == index), None),
           'after_error': next((error['reason'] for error in after['errors'] if error['element'] == index), None)}
    if left is None or right is None:
        return {**row, 'status': 'unavailable'}
    if kind in {'u64', 'i64'}:
        row['delta'] = str(int(right) - int(left))
        equal = left == right
    elif kind.startswith('f'):
        delta = right - left
        if math.isfinite(delta):
            row['delta'] = delta
        else:
            row['delta_error'] = 'nonfinite_delta'
        equal = math.isclose(left, right, rel_tol=relative_tolerance, abs_tol=absolute_tolerance)
    else:
        row['delta'] = right - left
        equal = left == right
    return {**row, 'status': 'unchanged' if equal else 'changed'}


def compare_captures(before_path: Path, after_path: Path, fields: list[dict], *,
                     absolute_tolerance=0, relative_tolerance=0, changes_only=True, offset=0, limit=100):
    fields = _fields(fields)
    _options(absolute_tolerance, relative_tolerance, changes_only, offset, limit)
    # Read each source once. Values, endpoint metadata and hashes share those bytes.
    before, before_source = _decode_data(_read(before_path), fields)
    after, after_source = _decode_data(_read(after_path), fields)
    origin = _origin(before_source)
    if origin != _origin(after_source):
        raise ValueError('Configured SNPX origins differ; compare captures from the same configured target')
    if (before['address'], before['count']) != (after['address'], after['count']):
        raise ValueError('SNPX comparison requires the same captured word span')
    raw_changes = None
    if before['available'] and after['available']:
        left_raw, right_raw = bytes.fromhex(before_source['raw_hex']), bytes.fromhex(after_source['raw_hex'])
        raw_changes = [before['address'] + index for index in range(before['count'])
                       if left_raw[index * 2:index * 2 + 2] != right_raw[index * 2:index * 2 + 2]]
    rows = [_element(left, right, index, absolute_tolerance, relative_tolerance)
            for left, right in zip(before['items'], after['items']) for index in range(left['count'])]
    selected = [row for row in rows if not changes_only or row['status'] != 'unchanged']
    summary = {status: sum(row['status'] == status for row in rows) for status in ['changed', 'unchanged', 'unavailable']}
    def source(result):
        return {key: result[key] for key in ['source_sha256', 'source_bytes', 'address', 'count', 'available']}
    return {'before': source(before), 'after': source(after), 'configured_origin': origin,
            'configured_origin_matches': True, 'comparison_complete': summary['unavailable'] == 0,
            'raw_changed_word_addresses': raw_changes, 'summary': summary, 'elements_total': len(rows),
            'total': len(selected), 'offset': offset, 'next_offset': offset + limit if offset + limit < len(selected) else None,
            'items': selected[offset:offset + limit], 'changes_only': changes_only,
            'absolute_tolerance': absolute_tolerance, 'relative_tolerance': relative_tolerance,
            'tolerance_scope': 'decoded floats only; integers exact; raw changes always retained',
            'delta_direction': 'after minus before', 'mapping_verified': False, 'capture_provenance_verified': False,
            'capture_is_atomic': False, 'scaling_applied': False, 'controller_contacted': False}


def self_test():
    sample = {'name': 'test', 'type': 'u64', 'word_addresses': [1], 'byte_order': 'little', 'word_order': 'low_first',
              'values': ['18446744073709551614'], 'raw_hex': ['feffffffffffffff'], 'errors': [], 'value_encoding': 'decimal_string'}
    after = {**sample, 'values': ['18446744073709551615'], 'raw_hex': ['ffffffffffffffff']}
    return decoder_self_test() and wire_self_test() and _element(sample, after, 0, 0, 0)['delta'] == '1'


def handle(operation_id, arguments, context):
    if operation_id == 'workbench.snpx.inspect_evidence':
        options = {key: value for key, value in arguments.items() if key not in {'artifact_id', 'capture_path', 'wire_path'}}
        return inspect_evidence(Path(context['capture']['file']), Path(context['wire']['file']), **options)
    if operation_id == 'workbench.snpx.decode_words':
        return decode_capture(Path(context['capture']['file']), arguments['fields'])
    if operation_id == 'workbench.snpx.compare_words':
        options = {key: value for key, value in arguments.items()
                   if key not in {'before_artifact_id', 'before_path', 'after_artifact_id', 'after_path'}}
        return compare_captures(Path(context['before']['file']), Path(context['after']['file']), **options)
    raise ValueError('Unknown SNPX operation')
