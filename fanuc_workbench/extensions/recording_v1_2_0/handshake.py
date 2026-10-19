"""Offline sampled request/acknowledgment observations; MIT, see LICENSE.txt."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from . import summary


_REQUIRED = {'name', 'request_signal', 'ack_signal', 'request_active', 'request_inactive', 'ack_active', 'ack_inactive'}


def _rules(rules):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 16:
        raise ValueError('Supply 1-16 explicit handshake rules')
    names, validated = set(), []
    for rule in rules:
        if not isinstance(rule, dict) or not _REQUIRED <= rule.keys() or rule.keys() - _REQUIRED - {'timeout_seconds'}:
            raise ValueError('Handshake rules require names, signal names and explicit active/inactive states')
        for key, maximum in [('name', 64), ('request_signal', 128), ('ack_signal', 128)]:
            if not isinstance(rule[key], str) or not 1 <= len(rule[key]) <= maximum:
                raise ValueError('Handshake names and signal names must be bounded nonempty strings')
        if rule['name'] in names or rule['request_signal'] == rule['ack_signal']:
            raise ValueError('Rule names must be unique and request/ack signals distinct')
        names.add(rule['name'])
        for key in ['request_active', 'request_inactive', 'ack_active', 'ack_inactive']:
            value = rule[key]
            if (type(value) not in {bool, int, float, str}
                    or (isinstance(value, str) and len(value) > 64)
                    or (type(value) in {int, float} and (not summary._finite_number(value) or abs(value) > 2 ** 31 - 1))):
                raise ValueError('Handshake states require bounded, finite JSON scalars')
        for prefix in ['request', 'ack']:
            if summary._key(rule[prefix + '_active']) == summary._key(rule[prefix + '_inactive']):
                raise ValueError('Active and inactive states must differ')
        timeout = rule.get('timeout_seconds', 10)
        if not summary._finite_number(timeout) or not 0 < timeout <= 86400:
            raise ValueError('Handshake threshold must be finite and in (0, 86400] seconds')
        validated.append({**rule, 'timeout_seconds': timeout})
    return validated


def _state(sample, signal, active, inactive):
    if signal in sample.get('errors', {}):
        return None, 'error'
    if signal not in sample['values']:
        return None, 'missing'
    value = sample['values'][signal]
    if value is None:
        return None, 'null'
    key = summary._key(value)
    if key == summary._key(active):
        return True, None
    if key == summary._key(inactive):
        return False, None
    return None, 'other_state'


def _event(rule, samples, start, end, status, clock_valid, *, edge=True, ack=False):
    start_time, end_time = samples[start]['timestamp'], samples[end]['timestamp']
    wait_end = end - 1 if status == 'evidence_gap' else end
    waited = samples[wait_end]['timestamp'] - start_time if clock_valid and edge and wait_end >= start else None
    result = {'rule': rule['name'], 'status': status, 'request_edge_observed': edge,
              'request_first_active_index': start, 'end_index': end,
              'request_interval_indices': [start - 1, start] if edge else None,
              'ack_interval_indices': [end - 1, end] if ack else None,
              'observed_delay_seconds': None, 'nominal_delay_range_seconds': None,
              'threshold_relation': 'unavailable', 'timeout_seconds': rule['timeout_seconds'],
              'observed_wait_seconds': waited,
              'observed_wait_exceeds_threshold': waited > rule['timeout_seconds'] if waited is not None else None,
              'wait_through_index': wait_end if edge and wait_end >= start else None}
    # Only separate request and ack intervals with request still active support
    # this sampled timing range. Coobserved changes have unresolved ordering.
    if ack and edge and clock_valid and status == 'ack_observed' and end > start:
        minimum = max(0, samples[end - 1]['timestamp'] - start_time)
        maximum = end_time - samples[start - 1]['timestamp']
        relation = ('within_threshold' if maximum <= rule['timeout_seconds'] else 'beyond_threshold'
                    if minimum > rule['timeout_seconds'] else 'threshold_ambiguous')
        result.update(observed_delay_seconds=end_time - start_time,
                      nominal_delay_range_seconds={'minimum': minimum, 'maximum': maximum}, threshold_relation=relation)
    return result


def _analyze(samples, rule, clock_valid):
    events, reasons = [], Counter()
    previous_request = previous_ack = None
    previous_pair_known, pending = False, None
    known_pairs = 0
    for index, sample in enumerate(samples):
        request, request_reason = _state(sample, rule['request_signal'], rule['request_active'], rule['request_inactive'])
        ack, ack_reason = _state(sample, rule['ack_signal'], rule['ack_active'], rule['ack_inactive'])
        for prefix, reason in [('request', request_reason), ('ack', ack_reason)]:
            if reason:
                reasons[prefix + '_' + reason] += 1
        pair_known = request is not None and ack is not None
        known_pairs += pair_known
        if pending is not None:
            if not pair_known:
                events.append(_event(rule, samples, pending, index, 'evidence_gap', clock_valid))
                pending = None
            elif ack:
                status = 'ack_observed' if request else 'ack_and_request_clear_coobserved'
                events.append(_event(rule, samples, pending, index, status, clock_valid, ack=True))
                pending = None
            elif not request:
                events.append(_event(rule, samples, pending, index, 'request_cleared_before_ack_observed', clock_valid))
                pending = None
        if previous_request is False and request is True:
            if previous_ack is not False:
                status = 'ack_baseline_unknown' if previous_ack is None else 'ack_not_inactive_at_request_baseline'
                events.append(_event(rule, samples, index, index, status, clock_valid))
            elif ack is None:
                events.append(_event(rule, samples, index, index, 'evidence_gap', clock_valid))
            elif ack:
                events.append(_event(rule, samples, index, index, 'request_and_ack_coobserved', clock_valid, ack=True))
            else:
                pending = index
        elif request is True and pair_known and (index == 0 or previous_request is None or not previous_pair_known):
            events.append(_event(rule, samples, index, index, 'request_active_without_observed_edge', clock_valid, edge=False))
        previous_request, previous_ack, previous_pair_known = request, ack, pair_known
    if pending is not None:
        events.append(_event(rule, samples, pending, len(samples) - 1, 'open_at_recording_end', clock_valid))
    return events, {'name': rule['name'], 'request_signal': rule['request_signal'], 'ack_signal': rule['ack_signal'],
                    'known_pair_samples': known_pairs, 'unknown_pair_samples': len(samples) - known_pairs,
                    'unavailable_reasons': dict(reasons), 'events_total': len(events),
                    'request_edges_observed': sum(event['request_edge_observed'] for event in events),
                    'status_counts': dict(Counter(event['status'] for event in events))}


def analyze_handshake(path: Path, rules: list[dict], *, offset=0, limit=100):
    rules = _rules(rules)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use offset >= 0 and limit 1-100')
    with Path(path).open('rb') as stream:
        data = stream.read(summary.MAX_BYTES + 1)
    if len(data) > summary.MAX_BYTES:
        raise ValueError('Recording exceeds 8 MiB')
    try:
        document = json.loads(data.decode('utf-8-sig'), object_pairs_hook=summary._pairs, parse_constant=summary._constant)
        samples, signals, _ = summary._validate(document)
    except (UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError('Unsupported recording JSON') from error
    if samples and any(rule[key] not in signals for rule in rules for key in ['request_signal', 'ack_signal']):
        raise ValueError('Each configured handshake signal must occur in the recording')
    timestamps = [sample['timestamp'] for sample in samples]
    intervals = [after - before for before, after in zip(timestamps, timestamps[1:])]
    regressions, duplicates = sum(delta < 0 for delta in intervals), sum(delta == 0 for delta in intervals)
    clock_valid = bool(samples) and not regressions and not duplicates
    events, summaries = [], []
    for rule_index, rule in enumerate(rules):
        rows, totals = _analyze(samples, rule, clock_valid)
        events.extend({**row, 'rule_index': rule_index} for row in rows)
        summaries.append(totals)
    events.sort(key=lambda row: (row['end_index'], row['rule_index'], row['request_first_active_index']))
    return {'source_sha256': hashlib.sha256(data).hexdigest(), 'source_bytes': len(data),
            'samples_total': len(samples), 'rules': summaries, 'total': len(events), 'offset': offset,
            'next_offset': offset + limit if offset + limit < len(events) else None,
            'items': events[offset:offset + limit], 'status_counts': dict(Counter(row['status'] for row in events)),
            'request_edges_observed': sum(row['request_edge_observed'] for row in events),
            'threshold_counts': dict(Counter(row['threshold_relation'] for row in events)),
            'clock': {'strictly_increasing': clock_valid, 'regressions': regressions, 'duplicates': duplicates,
                      'source_verified': False},
            'timing_basis': 'nominal recorded timestamp grid; sequential/stale reads can violate physical timing ranges',
            'physical_timing_verified': False, 'capture_is_atomic': False, 'rule_semantics_verified': False,
            'continuous_monitoring_verified': False, 'causation_verified': False,
            'capture_provenance_verified': False, 'recording_completion_verified': False,
            'controller_contacted': False, 'controller_written': False, 'evidence_only': True}


def self_test():
    rule = _rules([{'name': 'test', 'request_signal': 'Q', 'ack_signal': 'A',
        'request_active': True, 'request_inactive': False, 'ack_active': True, 'ack_inactive': False}])[0]
    samples = [{'timestamp': time, 'values': {'Q': q, 'A': a}}
               for time, q, a in [(0, False, False), (2, True, False), (4, True, True)]]
    rows, _ = _analyze(samples, rule, True)
    return rows[0]['status'] == 'ack_observed' and rows[0]['nominal_delay_range_seconds'] == {'minimum': 0, 'maximum': 4}
