"""Bounded offline text-alarm analysis; original MIT code, see LICENSE.txt."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import io
from pathlib import Path
import re


MAX_BYTES, MAX_LINES, MAX_LINE_BYTES = 16 * 1024 * 1024, 50000, 2048
_CODE = re.compile(r'(?<![A-Z0-9_-])([A-Z]{2,10}-[0-9]{3,5})(?![A-Z0-9_-])', re.I | re.ASCII)
_STAMP = re.compile(r'(?<![0-9])(?P<date>[0-9]{1,4}(?P<separator>[/-])[0-9]{1,2}(?P=separator)[0-9]{1,4})[ T]+'
    r'(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2})'
    r'(?:\.(?P<fraction>[0-9]{1,6}))?)?\s*(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})?\s*$', re.I | re.ASCII)
_SEVERITY = re.compile(r'^\(([^()\r\n]{1,64})\)\s*')
_EPOCH = datetime(1970, 1, 1)


def _options(date_order, utc_offset_minutes, source_order, view, code, include_unrecognized, offset, limit):
    if date_order not in ('ymd', 'mdy', 'dmy'):
        raise ValueError('Choose an explicit date_order: ymd, mdy or dmy')
    if type(utc_offset_minutes) is not int or not -840 <= utc_offset_minutes <= 840:
        raise ValueError('Supply an integer UTC offset between -840 and 840 minutes')
    if source_order not in ('oldest_first', 'newest_first') or view not in ('records', 'codes'):
        raise ValueError('Choose supported source order and result view')
    if not isinstance(code, str) or (code and _CODE.fullmatch(code) is None):
        raise ValueError('Alarm filter must be empty or one exact supported alarm code')
    if type(include_unrecognized) is not bool:
        raise ValueError('include_unrecognized must be a boolean')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use offset >= 0 and limit 1-100')


def _timestamp(prefix, date_order, supplied_offset):
    result = {'raw_timestamp': prefix.strip() or None,
        'timestamp_status': 'unsupported' if prefix.strip() else 'missing',
        'timestamp_error': 'timestamp_syntax_unsupported' if prefix.strip() else 'timestamp_missing',
        'timestamp_utc': None, 'utc_epoch_microseconds': None, 'timestamp_precision': None,
        'effective_utc_offset_minutes': None, 'offset_source': None,
        'inline_offset_differs_from_supplied': False, '_timestamp_us': None}
    match = _STAMP.search(prefix)
    if match is None:
        return result
    result['raw_timestamp'] = match[0].strip()
    parts = re.split(r'[/-]', match['date'])
    fields = dict(zip(date_order, parts))
    if len(fields['y']) != 4:
        return {**result, 'timestamp_status': 'invalid', 'timestamp_error': 'four_digit_year_required'}
    effective_offset = supplied_offset
    zone = match['zone']
    if zone:
        effective_offset = 0
        if zone.upper() != 'Z':
            hours, minutes = int(zone[1:3]), int(zone[4:6])
            if minutes > 59 or hours * 60 + minutes > 840:
                return {**result, 'timestamp_status': 'invalid', 'timestamp_error': 'inline_offset_outside_range'}
            effective_offset = (hours * 60 + minutes) * (1 if zone[0] == '+' else -1)
    try:
        fraction = match['fraction'] or ''
        stamp = datetime(int(fields['y']), int(fields['m']), int(fields['d']), int(match['hour']),
                         int(match['minute']), int(match['second'] or 0), int(fraction.ljust(6, '0') or 0))
        utc = stamp - timedelta(minutes=effective_offset)
    except (ValueError, OverflowError):
        return {**result, 'timestamp_status': 'invalid', 'timestamp_error': 'invalid_calendar_or_time'}
    delta = utc - _EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    return {**result, 'timestamp_status': 'valid', 'timestamp_error': None,
        'timestamp_utc': utc.isoformat(timespec='microseconds') + 'Z', 'utc_epoch_microseconds': str(micros),
        'timestamp_precision': 'fractional_second' if fraction else 'second' if match['second'] else 'minute',
        'effective_utc_offset_minutes': effective_offset, 'offset_source': 'inline' if zone else 'supplied',
        'inline_offset_differs_from_supplied': bool(zone) and effective_offset != supplied_offset,
        '_timestamp_us': micros}


def _record(line, line_number, date_order, utc_offset_minutes):
    match = _CODE.search(line)
    if match is None:
        return {'line_number': line_number, 'record_type': 'unrecognized', 'raw_line': line,
                'code': None, 'severity': None, 'message': None, 'timestamp_status': None, '_timestamp_us': None}
    tail, severity, issues = line[match.end():].strip(), None, []
    severity_match = _SEVERITY.match(tail)
    if severity_match:
        severity = severity_match[1].strip() or None
        tail = tail[severity_match.end():]
    elif tail.startswith('('):
        issues.append('severity_syntax_unsupported')
    if tail.startswith(':'):
        tail = tail[1:].lstrip()
    elif tail.startswith('- '):
        tail = tail[2:].lstrip()
    return {'line_number': line_number, 'record_type': 'alarm', 'raw_line': line,
        'code': match[1].upper(), 'severity': severity, 'message': tail, 'issues': issues,
        **_timestamp(line[:match.start()], date_order, utc_offset_minutes)}


def _groups(records, source_order):
    grouped = {}
    for row in records:
        if row['record_type'] != 'alarm':
            continue
        item = grouped.setdefault(row['code'], {'code': row['code'], 'count': 0, 'first_line': row['line_number'],
            'last_line': row['line_number'], '_messages': Counter(), '_severities': Counter(),
            'valid_timestamp_occurrences': 0, 'unavailable_timestamp_occurrences': 0,
            'first_valid_timestamp_utc': None, 'last_valid_timestamp_utc': None,
            'comparable_intervals': 0, 'opposite_order_intervals': 0, 'repeated_timestamp_intervals': 0,
            '_previous_us': None, '_minimum_us': None, '_maximum_us': None})
        item['count'] += 1
        item['last_line'] = row['line_number']
        item['_messages'][row['message']] += 1
        item['_severities'][row['severity'] or ''] += 1
        instant, previous = row['_timestamp_us'], item['_previous_us']
        if instant is None:
            item['unavailable_timestamp_occurrences'] += 1
        else:
            item['valid_timestamp_occurrences'] += 1
            item['first_valid_timestamp_utc'] = item['first_valid_timestamp_utc'] or row['timestamp_utc']
            item['last_valid_timestamp_utc'] = row['timestamp_utc']
            if previous is not None:
                difference = instant - previous if source_order == 'oldest_first' else previous - instant
                item['comparable_intervals'] += 1
                item['opposite_order_intervals'] += difference < 0
                item['repeated_timestamp_intervals'] += difference == 0
                if difference >= 0:
                    item['_minimum_us'] = difference if item['_minimum_us'] is None else min(item['_minimum_us'], difference)
                    item['_maximum_us'] = difference if item['_maximum_us'] is None else max(item['_maximum_us'], difference)
        # Missing timestamps break adjacency for this code; never bridge them.
        item['_previous_us'] = instant
    result = []
    for item in grouped.values():
        comparable = item['comparable_intervals']
        usable = comparable > 0 and item['opposite_order_intervals'] == 0
        result.append({key: value for key, value in item.items() if not key.startswith('_')} | {
            'message_variants': len(item['_messages']), 'severity_variants': len(item['_severities']),
            'most_common_message': item['_messages'].most_common(1)[0][0],
            'most_common_severity': item['_severities'].most_common(1)[0][0] or None,
            'incomplete_intervals': max(0, item['count'] - 1 - comparable),
            'interval_coverage_complete': comparable == max(0, item['count'] - 1) and item['opposite_order_intervals'] == 0,
            'minimum_interval_microseconds': str(item['_minimum_us']) if usable else None,
            'maximum_interval_microseconds': str(item['_maximum_us']) if usable else None})
    return sorted(result, key=lambda row: (-row['count'], row['code']))


def analyze_alarms(path: Path, *, date_order: str, utc_offset_minutes: int, source_order='oldest_first',
                   view='records', code='', include_unrecognized=True, offset=0, limit=100):
    _options(date_order, utc_offset_minutes, source_order, view, code, include_unrecognized, offset, limit)
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Alarm log exceeds 16 MiB')
    try:
        text, encoding = data.decode('utf-8-sig'), 'utf-8'
    except UnicodeDecodeError:
        try:
            text, encoding = data.decode('cp1252'), 'cp1252'
        except UnicodeDecodeError as error:
            raise ValueError('Alarm log encoding is unsupported') from error
    if '\x00' in text:
        raise ValueError('Alarm log contains NUL bytes; supply a supported text export')
    records, lines_total, blank_lines = [], 0, 0
    for line_number, line in enumerate(io.StringIO(text), 1):
        if line_number > MAX_LINES:
            raise ValueError('Alarm log exceeds 50000 lines')
        lines_total = line_number
        line = line.rstrip('\r\n')
        if len(line.encode('utf-8')) > MAX_LINE_BYTES:
            raise ValueError('Decoded alarm log line exceeds 2048 UTF-8 bytes')
        if not line.strip():
            blank_lines += 1
            continue
        records.append(_record(line, line_number, date_order, utc_offset_minutes))
    groups = _groups(records, source_order)
    alarms = [row for row in records if row['record_type'] == 'alarm']
    if view == 'codes':
        selected = [row for row in groups if not code or row['code'] == code.upper()]
    else:
        selected = [row for row in records if (not code or row['code'] == code.upper())
                    and (include_unrecognized or row['record_type'] == 'alarm')]
    return {'source_sha256': hashlib.sha256(data).hexdigest(), 'source_bytes': len(data), 'encoding': encoding,
        'lines_total': lines_total, 'blank_lines': blank_lines, 'nonempty_lines': len(records),
        'parsed_alarm_records': len(alarms), 'unrecognized_lines': len(records) - len(alarms),
        'distinct_codes': len(groups), 'timestamp_status_counts': dict(Counter(row['timestamp_status'] for row in alarms)),
        'parse_issue_counts': dict(Counter(issue for row in alarms for issue in row['issues'])),
        'view': view, 'code_filter': code.upper(), 'include_unrecognized': include_unrecognized,
        'total': len(selected), 'offset': offset, 'next_offset': offset + limit if offset + limit < len(selected) else None,
        'items': [{key: value for key, value in row.items() if not key.startswith('_')}
                  for row in selected[offset:offset + limit]],
        'clock_assumptions': {'date_order': date_order, 'utc_offset_minutes': utc_offset_minutes,
            'source_order': source_order, 'inline_offsets_take_precedence': True,
            'timezone_rules_applied': False, 'assumptions_verified': False},
        'interval_encoding': 'decimal_microseconds', 'timing_verified': False,
        'alarm_occurrences_verified': False, 'code_catalog_verified': False, 'capture_provenance_verified': False,
        'log_completeness_verified': False, 'controller_contacted': False, 'controller_written': False,
        'evidence_only': True}


def self_test():
    row = _record('2026/10/19 01:00:00 SRVO-001 (WARN) Example', 1, 'ymd', 60)
    return row['code'] == 'SRVO-001' and row['timestamp_utc'] == '2026-10-19T00:00:00.000000Z'


def handle(operation_id, arguments, context):
    if operation_id != 'workbench.alarms.analyze':
        raise ValueError('Unknown alarm operation')
    options = {key: value for key, value in arguments.items() if key not in {'artifact_id', 'path'}}
    return analyze_alarms(Path(context['log']['file']), **options)
