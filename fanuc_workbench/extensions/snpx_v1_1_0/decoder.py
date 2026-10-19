"""Offline interpretation of preserved SNPX word captures. MIT; see LICENSE.txt.

Self-contained host API 1 feature: no controller, host-package or GUI imports.
Labels and decoding choices are caller assumptions, never verified assignments.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import struct


_FORMATS = {'u16': ('H', 1), 'i16': ('h', 1), 'u32': ('I', 2), 'i32': ('i', 2),
            'f32': ('f', 2), 'u64': ('Q', 4), 'i64': ('q', 4), 'f64': ('d', 4)}
FORMATS = tuple(_FORMATS)
MAX_BYTES = 256 * 1024
MAX_FIELDS, MAX_VALUES = 32, 512


def _fields(fields):
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS:
        raise ValueError('Supply 1-32 explicit decoding fields')
    names, result, total = set(), [], 0
    required = {'name', 'word_offset', 'type', 'byte_order', 'word_order'}
    for field in fields:
        if not isinstance(field, dict) or not required <= field.keys() or field.keys() - required - {'count'}:
            raise ValueError('Each field requires name, word_offset, type, byte_order and word_order')
        name, offset, kind, count = field['name'], field['word_offset'], field['type'], field.get('count', 1)
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', name) or name in names:
            raise ValueError('Field names must be distinct bounded labels')
        if (type(offset) is not int or not 0 <= offset < 128 or type(count) is not int or not 1 <= count <= 128
                or not isinstance(kind, str) or kind not in _FORMATS
                or not isinstance(field['byte_order'], str) or field['byte_order'] not in {'little', 'big'}
                or not isinstance(field['word_order'], str) or field['word_order'] not in {'low_first', 'high_first'}):
            raise ValueError('Invalid decoding type, order, offset or count')
        width = _FORMATS[kind][1]
        if offset + count * width > 128:
            raise ValueError('Decoded field exceeds the 128-word capture bound')
        total += count
        if total > MAX_VALUES:
            raise ValueError('At most 512 decoded elements are allowed')
        names.add(name)
        result.append({**field, 'count': count})
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Capture JSON contains a duplicate key')
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError('Capture JSON contains a nonfinite constant')


def _value(raw, kind, byte_order, word_order):
    words = [int.from_bytes(raw[index:index + 2], byte_order) for index in range(0, len(raw), 2)]
    if word_order == 'high_first':
        words.reverse()
    bits = sum(word << (16 * index) for index, word in enumerate(words))
    code = _FORMATS[kind][0]
    value = struct.unpack('<' + code, bits.to_bytes(len(raw), 'little'))[0]
    if kind.startswith('f') and not math.isfinite(value):
        return None
    return str(value) if kind in {'u64', 'i64'} else value


def _read(path: Path) -> bytes:
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('SNPX capture exceeds the 256 KiB decoding limit')
    return data


def _decode_data(data: bytes, fields: list[dict]) -> tuple[dict, dict]:
    fields = _fields(fields)
    try:
        capture = json.loads(data.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid SNPX capture JSON') from exc
    if not isinstance(capture, dict) or capture.get('provider') != 'snpx':
        raise ValueError('Supply a preserved SNPX raw-word capture')
    address, count, errors = capture.get('address'), capture.get('count'), capture.get('errors')
    if (type(address) is not int or type(count) is not int or not 1 <= address <= 65535
            or not 1 <= count <= 128 or address + count > 65536 or not isinstance(errors, dict)):
        raise ValueError('Invalid SNPX capture span or error metadata')
    if any(field['word_offset'] + field['count'] * _FORMATS[field['type']][1] > count for field in fields):
        raise ValueError('Decoded field lies outside the captured words')
    payload = capture.get('raw_hex')
    if errors:
        if any(capture.get(key) is not None for key in ['raw_hex', 'words_u16', 'words_i16']):
            raise ValueError('Capture data contradicts its unavailable status')
        raw = None
    else:
        if not isinstance(payload, str) or len(payload) != count * 4 or not re.fullmatch(r'[0-9a-fA-F]+', payload):
            raise ValueError('Capture raw bytes do not match its word count')
        raw = bytes.fromhex(payload)
        unsigned, signed = list(struct.unpack('<' + 'H' * count, raw)), list(struct.unpack('<' + 'h' * count, raw))
        for key, expected in [('words_u16', unsigned), ('words_i16', signed)]:
            values = capture.get(key)
            if not isinstance(values, list) or any(type(value) is not int for value in values) or values != expected:
                raise ValueError('Capture word values disagree with its raw bytes')
    rows = []
    for field in fields:
        width = _FORMATS[field['type']][1]
        row = {**field, 'word_addresses': [], 'raw_hex': [], 'values': [], 'errors': [],
               'value_encoding': 'decimal_string' if field['type'] in {'u64', 'i64'} else 'number'}
        for index in range(field['count']):
            offset = field['word_offset'] + index * width
            part = raw[offset * 2:(offset + width) * 2] if raw is not None else None
            value = _value(part, field['type'], field['byte_order'], field['word_order']) if part is not None else None
            row['word_addresses'].append(address + offset)
            row['raw_hex'].append(part.hex() if part is not None else None)
            row['values'].append(value)
            if value is None:
                row['errors'].append({'element': index, 'reason': 'capture_unavailable' if raw is None else 'nonfinite_float'})
        rows.append(row)
    return {'source_sha256': hashlib.sha256(data).hexdigest(), 'source_bytes': len(data),
            'address': address, 'count': count, 'available': raw is not None, 'items': rows,
            'mapping_verified': False, 'capture_provenance_verified': False,
            'capture_is_atomic': False, 'scaling_applied': False, 'controller_contacted': False,
            'interpretation': 'caller-selected formats and labels; raw SNPX words only'}, capture


def decode_capture(path: Path, fields: list[dict]) -> dict:
    fields = _fields(fields)
    return _decode_data(_read(path), fields)[0]


def self_test():
    return (_value(bytes.fromhex('0000803f'), 'f32', 'little', 'low_first') == 1.0
            and _value(bytes.fromhex('3f800000'), 'f32', 'big', 'high_first') == 1.0
            and _value(bytes.fromhex('ffffffffffffffff'), 'u64', 'little', 'low_first') == '18446744073709551615'
            and _value(bytes.fromhex('0000807f'), 'f32', 'little', 'low_first') is None)


def handle(operation_id, arguments, context):
    if operation_id != 'workbench.snpx.decode_words':
        raise ValueError('Unknown SNPX decoding operation')
    return decode_capture(Path(context['capture']['file']), arguments['fields'])
