"""Bounded offline watch summaries. Original code, MIT; see LICENSE.txt."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median


MAX_BYTES = 8 * 1024 * 1024
MAX_SAMPLES, MAX_SIGNALS, MAX_VALUE_BYTES = 300, 256, 4096
_ARTIFACT_ID = re.compile(r'[0-9a-f]{32}')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def _constant(_):
    raise ValueError('Nonfinite JSON constant')


def _finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _validate_value(value, depth=0):
    if depth > 4:
        raise ValueError('Recorded value exceeds nesting limit')
    if value is None or type(value) is bool:
        return
    if type(value) in {int, float}:
        if not _finite_number(value) or (type(value) is int and abs(value) >= 2 ** 64):
            raise ValueError('Recorded number is nonfinite or outside the 64-bit magnitude limit')
    elif isinstance(value, str):
        if len(value) > 1024:
            raise ValueError('Recorded string exceeds length limit')
    elif isinstance(value, (list, dict)):
        if len(value) > 64:
            raise ValueError('Recorded value exceeds container limit')
        if isinstance(value, dict) and any(len(key) > 128 for key in value):
            raise ValueError('Recorded object key exceeds length limit')
        for child in value.values() if isinstance(value, dict) else value:
            _validate_value(child, depth + 1)
    else:
        raise ValueError('Unsupported recorded value')


def _key(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _output(value):
    # Keep integers exact in clients that parse JSON numbers as IEEE doubles.
    if type(value) is int and abs(value) > 2 ** 53 - 1:
        return str(value)
    if isinstance(value, list):
        return [_output(item) for item in value]
    if isinstance(value, dict):
        return {key: _output(item) for key, item in value.items()}
    return value


def _validate(document):
    if not isinstance(document, dict) or not isinstance(document.get('samples'), list):
        raise ValueError('Supply watch recording JSON with a samples array')
    samples = document['samples']
    if len(samples) > MAX_SAMPLES:
        raise ValueError('Recording exceeds 300 samples')
    signals = set()
    for sample in samples:
        if not isinstance(sample, dict) or not {'timestamp', 'values'} <= sample.keys():
            raise ValueError('Each sample requires timestamp and values')
        timestamp = sample['timestamp']
        if not _finite_number(timestamp) or abs(timestamp) > 10 ** 12:
            raise ValueError('Sample timestamp must be finite and within supported range')
        values, errors = sample['values'], sample.get('errors', {})
        if not isinstance(values, dict) or not isinstance(errors, dict):
            raise ValueError('Sample values and errors must be objects')
        names = values.keys() | errors.keys()
        if any(not name or len(name) > 128 for name in names):
            raise ValueError('Signal names require 1-128 characters')
        signals.update(names)
        if len(signals) > MAX_SIGNALS:
            raise ValueError('Recording exceeds 256 signals')
        if any(not isinstance(error, str) or len(error) > 1024 for error in errors.values()):
            raise ValueError('Sample errors require strings of at most 1024 characters')
        for value in values.values():
            _validate_value(value)
            if len(_key(value).encode('ascii')) > MAX_VALUE_BYTES:
                raise ValueError('Recorded value exceeds 4096 encoded bytes')
    references = document.get('sample_artifacts', [])
    if (not isinstance(references, list) or len(references) > MAX_SAMPLES
            or any(not isinstance(item, str) or not _ARTIFACT_ID.fullmatch(item) for item in references)):
        raise ValueError('Sample references require at most 300 artifact IDs')
    return samples, sorted(signals), references


def _signal_summary(samples, signal):
    reasons = Counter()
    available = []
    numeric = []
    segments = changes = gap_changes = run = longest = 0
    previous_key = None
    previous_index = None
    first_error = None
    for index, sample in enumerate(samples):
        errors, values = sample.get('errors', {}), sample['values']
        if signal in errors:
            reason = 'error'
            if first_error is None:
                first_error = errors[signal]
        elif signal not in values:
            reason = 'missing'
        elif values[signal] is None:
            reason = 'null'
        else:
            reason = None
        if reason:
            reasons[reason] += 1
            run += 1
            longest = max(longest, run)
            continue
        value, run = values[signal], 0
        key = _key(value)
        adjacent = previous_index == index - 1
        if not adjacent:
            segments += 1
        if previous_index is not None and key != previous_key:
            if adjacent:
                changes += 1
            else:
                gap_changes += 1
        available.append((index, value))
        if type(value) in {int, float}:
            numeric.append(value)
        previous_key, previous_index = key, index
    return {'signal': signal, 'available_samples': len(available),
            'unavailable_samples': sum(reasons.values()),
            'unavailable_reasons': dict(reasons), 'first_error': first_error,
            'availability_fraction': len(available) / len(samples) if samples else None,
            'available_segments': segments, 'longest_unavailable_run': longest,
            'adjacent_changes': changes, 'changed_after_gap': gap_changes,
            'first_available_index': available[0][0] if available else None,
            'last_available_index': available[-1][0] if available else None,
            'first_value': _output(available[0][1]) if available else None,
            'last_value': _output(available[-1][1]) if available else None,
            'numeric_samples': len(numeric),
            'numeric_min': _output(min(numeric)) if numeric else None,
            'numeric_max': _output(max(numeric)) if numeric else None}


def summarize_recording(path: Path, *, signal: str = '', offset: int = 0, limit: int = 100,
                        gap_threshold_seconds: float | None = None) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use offset >= 0 and a page limit between 1 and 100')
    if not isinstance(signal, str) or len(signal) > 128:
        raise ValueError('Signal filter must be a string of at most 128 characters')
    if gap_threshold_seconds is not None and (not _finite_number(gap_threshold_seconds)
                                             or not 0 < gap_threshold_seconds <= 86400):
        raise ValueError('Gap threshold must be finite and in (0, 86400] seconds')
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Recording exceeds the 8 MiB analysis limit')
    try:
        document = json.loads(data.decode('utf-8-sig'), object_pairs_hook=_pairs, parse_constant=_constant)
        samples, signals, references = _validate(document)
    except (UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError('Recording encoding, nesting or number is unsupported') from error
    timestamps = [sample['timestamp'] for sample in samples]
    intervals = [after - before for before, after in zip(timestamps, timestamps[1:])]
    regressions = sum(value < 0 for value in intervals)
    monotonic = bool(samples) and not regressions
    rows = [_signal_summary(samples, name) for name in signals]
    selected = [row for row in rows if not signal or row['signal'] == signal]
    return {'source_sha256': hashlib.sha256(data).hexdigest(), 'source_bytes': len(data),
            'samples_total': len(samples), 'signals_total': len(signals),
            'total': len(selected), 'offset': offset,
            'next_offset': offset + limit if offset + limit < len(selected) else None,
            'items': selected[offset:offset + limit],
            'availability': {'available_observations': sum(row['available_samples'] for row in rows),
                             'unavailable_observations': sum(row['unavailable_samples'] for row in rows),
                             'adjacent_changes': sum(row['adjacent_changes'] for row in rows),
                             'changed_after_gap': sum(row['changed_after_gap'] for row in rows)},
            'timestamps': {'first': timestamps[0] if timestamps else None,
                           'last': timestamps[-1] if timestamps else None,
                           'monotonic': monotonic, 'regressions': regressions,
                           'duplicates': sum(value == 0 for value in intervals),
                           'duration_seconds': timestamps[-1] - timestamps[0] if monotonic else None,
                           'interval_min_seconds': min(intervals) if intervals and monotonic else None,
                           'interval_max_seconds': max(intervals) if intervals and monotonic else None,
                           'interval_median_seconds': median(intervals) if intervals and monotonic else None,
                           'gap_threshold_seconds': gap_threshold_seconds,
                           'intervals_exceeding_threshold': (sum(value > gap_threshold_seconds for value in intervals)
                               if monotonic and gap_threshold_seconds is not None else None),
                           'clock_source_verified': False},
            'sample_references': {'artifact_ids': references, 'count': len(references),
                                  'unique_count': len(set(references)),
                                  'count_matches_samples': len(references) == len(samples),
                                  'contents_verified': False},
            'large_integer_encoding': 'decimal_string_above_2^53_minus_1',
            'recording_completion_verified': False, 'derived_events_used': False,
            'capture_provenance_verified': False, 'wire_evidence_checked': False,
            'controller_contacted': False, 'controller_written': False, 'evidence_only': True}


def self_test():
    rows = [{'values': {'DI[1]': False}}, {'values': {}, 'errors': {'DI[1]': 'unavailable'}},
            {'values': {'DI[1]': True}}, {'values': {'DI[1]': False}}]
    result = _signal_summary(rows, 'DI[1]')
    return result['changed_after_gap'] == 1 and result['adjacent_changes'] == 1


def handle(operation_id, arguments, context):
    if operation_id != 'workbench.recording.summarize':
        raise ValueError('Unknown recording operation')
    options = {key: value for key, value in arguments.items() if key not in {'artifact_id', 'path'}}
    return summarize_recording(Path(context['recording']['file']), **options)
