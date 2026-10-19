"""Compare bounded saved alarm record distributions; original MIT code."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .parser import _options, _scan


def _clock(value):
    if (not isinstance(value, dict) or not {'date_order', 'utc_offset_minutes'} <= value.keys()
            or value.keys() - {'date_order', 'utc_offset_minutes', 'source_order'}):
        raise ValueError('Each clock requires date_order and utc_offset_minutes, with optional source_order')
    clock = {'source_order': 'oldest_first', **value}
    _options(**clock, view='codes', code='', include_unrecognized=True, offset=0, limit=100)
    return clock


def _distributions(records):
    result = {}
    for row in records:
        if row['record_type'] == 'alarm':
            result.setdefault(row['code'], Counter())[(row['severity'], row['message'])] += 1
    return result


def _fingerprint(distribution):
    rows = [[severity, message, count] for (severity, message), count in distribution.items()]
    rows.sort(key=lambda row: (row[0] is not None, row[0] or '', row[1]))
    payload = json.dumps(rows, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _compare(baseline_records, baseline_groups, candidate_records, candidate_groups):
    before, after = _distributions(baseline_records), _distributions(candidate_records)
    baseline = {row['code']: row for row in baseline_groups}
    candidate = {row['code']: row for row in candidate_groups}
    rows = []
    for code in sorted(before.keys() | after.keys()):
        left, right = before.get(code, Counter()), after.get(code, Counter())
        status = ('only_in_candidate' if code not in before else 'only_in_baseline' if code not in after
                  else 'changed' if left != right else 'unchanged')
        rows.append({'code': code, 'status': status,
            'baseline_count': sum(left.values()), 'candidate_count': sum(right.values()),
            'count_delta': sum(right.values()) - sum(left.values()),
            'record_text_distribution_changed': left != right,
            'message_set_changed': {message for _, message in left} != {message for _, message in right},
            'severity_set_changed': {severity for severity, _ in left} != {severity for severity, _ in right},
            'baseline_text_distribution_sha256': _fingerprint(left),
            'candidate_text_distribution_sha256': _fingerprint(right),
            'baseline': baseline.get(code), 'candidate': candidate.get(code)})
    return rows


def compare_alarms(baseline_path: Path, candidate_path: Path, *, baseline_clock: dict, candidate_clock: dict,
                   code='', changes_only=True, offset=0, limit=100):
    baseline_clock, candidate_clock = _clock(baseline_clock), _clock(candidate_clock)
    _options(**baseline_clock, view='codes', code=code, include_unrecognized=True, offset=offset, limit=limit)
    if type(changes_only) is not bool:
        raise ValueError('changes_only must be a boolean')
    baseline_records, baseline_groups, baseline = _scan(baseline_path, **baseline_clock)
    candidate_records, candidate_groups, candidate = _scan(candidate_path, **candidate_clock)
    rows = _compare(baseline_records, baseline_groups, candidate_records, candidate_groups)
    statuses = {status: 0 for status in ('only_in_baseline', 'only_in_candidate', 'changed', 'unchanged')}
    statuses.update(Counter(row['status'] for row in rows))
    selected = [row for row in rows if (not code or row['code'] == code.upper())
                and (not changes_only or row['status'] != 'unchanged')]
    return {'baseline': baseline, 'candidate': candidate,
        'source_bytes_identical': baseline['source_sha256'] == candidate['source_sha256'],
        'comparison_scope': 'code_counts_and_severity_message_pair_distributions',
        'codes_compared': len(rows), 'status_counts': statuses,
        'parsed_record_count_delta': candidate['parsed_alarm_records'] - baseline['parsed_alarm_records'],
        'code_filter': code.upper(), 'changes_only': changes_only,
        'total': len(selected), 'offset': offset, 'next_offset': offset + limit if offset + limit < len(selected) else None,
        'items': selected[offset:offset + limit],
        'timestamps_used_for_matching': False, 'record_order_used_for_matching': False,
        'physical_occurrence_identity_verified': False, 'same_controller_verified': False,
        'capture_windows_comparable_verified': False, 'timing_verified': False,
        'alarm_occurrences_verified': False, 'code_catalog_verified': False,
        'capture_provenance_verified': False, 'log_completeness_verified': False,
        'controller_contacted': False, 'controller_written': False, 'evidence_only': True}


def self_test():
    left = [{'record_type': 'alarm', 'code': 'SRVO-001', 'severity': 'WARN', 'message': 'Example'}]
    row = _compare(left, [], left * 2, [])[0]
    return row['status'] == 'changed' and row['count_delta'] == 1
