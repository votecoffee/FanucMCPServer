"""Bounded, offline reading of desktop recording exports. No target resolution."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from fanuc_workbench.services.live_recording import watch_definitions
from fanuc_workbench.services.monitoring import Sample, WatchSpec

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_SAMPLES = 10000
MAX_SIGNALS = 1000
MAX_EVENTS = 100000
MAX_NODES = 500000
MAX_DEPTH = 32
MAX_STRING = 65536


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(value, required, optional=()):
    _require(isinstance(value, dict), 'Expected a recording object.')
    _require(set(required) <= value.keys() <= set(required) | set(optional),
             'Recording fields are missing or unsupported.')


def _array(value, limit):
    _require(isinstance(value, list) and len(value) <= limit, f'Expected a list of at most {limit} entries.')


def _text(value, *, nonempty=False):
    _require(isinstance(value, str) and (not nonempty or bool(value.strip())), 'Expected recording text.')


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high, f'Expected an integer in {low}..{high}.')


def _timestamp(value):
    _require(type(value) in (int, float) and math.isfinite(value), 'Recording timestamps must be finite numbers.')
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OSError, OverflowError) as exc:
        raise ValueError('Recording timestamp is outside the supported date range.') from exc


def _check_depth(text):
    # Bound containers and value tokens before the decoder allocates them.
    depth, nodes, quoted, escaped, literal = 0, 0, False, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted, literal = True, False
            nodes += 1
        elif character in '[{':
            depth += 1
            nodes += 1
            literal = False
            _require(depth <= MAX_DEPTH, f'Recording nesting exceeds {MAX_DEPTH} levels.')
        elif character in ']}':
            depth -= 1
            literal = False
        elif character in ':, \t\r\n':
            literal = False
        elif not literal:
            literal = True
            nodes += 1
        _require(nodes <= MAX_NODES, f'Recording exceeds {MAX_NODES} JSON values.')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Duplicate JSON fields are unsupported.')
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f'Nonfinite JSON number is unsupported: {value}')


def _check_values(document):
    pending, count = [document], 0
    while pending:
        value = pending.pop()
        count += 1
        _require(count <= MAX_NODES, f'Recording exceeds {MAX_NODES} JSON values.')
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            _require(len(value) <= MAX_STRING, f'Recording text exceeds {MAX_STRING} characters.')
            _require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), 'Recording contains invalid Unicode text.')
        elif type(value) in (int, float):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            _require(finite, 'Recording contains an unsupported or nonfinite number.')


def _source(source):
    if source is None:
        return
    _require(isinstance(source, dict), 'Expected source metadata.')
    if source.get('kind') == 'workspace':
        _object(source, ('kind', 'path'))
        if source['path'] is not None:
            _text(source['path'])
    else:
        _object(source, ('kind', 'profile'))
        _require(source['kind'] == 'controller_files', 'Unsupported recording source kind.')
        profile = source['profile']
        if profile is None:
            return
        _object(profile, ('name', 'host', 'port', 'username', 'roots', 'timeout', 'transport', 'sftp_trust_unknown_host'))
        for key in ('name', 'host', 'username', 'transport'):
            _text(profile[key])
        _integer(profile['port'], 1, 65535)
        _require(type(profile['timeout']) in (int, float) and profile['timeout'] > 0, 'Invalid source timeout.')
        _require(type(profile['sftp_trust_unknown_host']) is bool, 'Invalid source trust flag.')
        _array(profile['roots'], 1000)
        for root in profile['roots']:
            _text(root)


def _validate(document):
    _object(document, ('format', 'schema_version', 'recording_id', 'created_at_utc', 'source', 'watches',
                      'sample_limit', 'received_samples', 'samples', 'timeline', 'cycle', 'cycle_recording'),
            ('first_retained_sample', 'gap_before_samples', 'pauses'))
    _require(document['format'] == 'fanuc-desktop-recording', 'This is not a desktop recording export.')
    _require(type(document['schema_version']) is int and document['schema_version'] == 1,
             'Unsupported desktop recording schema version.')
    _text(document['recording_id'], nonempty=True)
    _text(document['created_at_utc'], nonempty=True)
    try:
        created = datetime.fromisoformat(document['created_at_utc'])
        _require(created.utcoffset() is not None and created.utcoffset().total_seconds() == 0,
                 'Recording creation time must include a UTC offset.')
    except (ValueError, OverflowError) as exc:
        raise ValueError('Invalid UTC recording creation time.') from exc
    _source(document['source'])
    _array(document['watches'], MAX_SIGNALS)
    for watch in document['watches']:
        _object(watch, ('expression', 'alias'))
        _text(watch['expression'], nonempty=True)
        _text(watch['alias'])
    if document['watches']:
        watch_definitions([WatchSpec(**watch) for watch in document['watches']])
    _integer(document['sample_limit'], 1, MAX_SAMPLES)
    received = document['received_samples']
    _integer(received, 0, document['sample_limit'])
    _require(type(document['cycle_recording']) is bool, 'Invalid exported cycle state.')
    _array(document['samples'], MAX_SAMPLES)
    _require(len(document['samples']) <= received, 'Retained samples exceed the received count.')
    first = received - len(document['samples'])
    document.setdefault('first_retained_sample', first)
    _integer(document['first_retained_sample'], first, first)
    signals = {watch['alias'].strip() or watch['expression'].strip() for watch in document['watches']}
    previous = None
    for sample in document['samples']:
        _object(sample, ('timestamp', 'values', 'errors'))
        _timestamp(sample['timestamp'])
        _require(previous is None or sample['timestamp'] >= previous, 'Sample timestamps moved backwards.')
        previous = sample['timestamp']
        for key in ('values', 'errors'):
            _require(isinstance(sample[key], dict), 'Sample values and errors must be objects.')
            signals.update(sample[key])
        for error in sample['errors'].values():
            _text(error)
    _array(document['timeline'], MAX_EVENTS)
    for event in document['timeline']:
        _object(event, ('timestamp', 'signal', 'old', 'new'))
        _timestamp(event['timestamp'])
        _text(event['signal'])
        signals.add(event['signal'])
    _require(len(signals) <= MAX_SIGNALS, f'Recording exceeds {MAX_SIGNALS} signal names.')
    _array(document['cycle'], MAX_EVENTS)
    for event in document['cycle']:
        _object(event, ('timestamp', 'state', 'detail'))
        _timestamp(event['timestamp'])
        _text(event['state'])
        _text(event['detail'])
    _require(not (received or document['timeline'] or document['cycle']) or
             (document['source'] is not None and bool(document['watches'])), 'Recorded data lacks source/watch metadata.')
    document.setdefault('gap_before_samples', [])
    gaps = document['gap_before_samples']
    _array(gaps, MAX_SAMPLES)
    for gap in gaps:
        _integer(gap, 1, received - 1)
    _require(gaps == sorted(set(gaps)), 'Gap sequence numbers must be unique and increasing.')
    document.setdefault('pauses', [])
    _array(document['pauses'], MAX_SAMPLES + 1)
    for pause in document['pauses']:
        _object(pause, ('timestamp', 'after_sample', 'reason'))
        _timestamp(pause['timestamp'])
        _integer(pause['after_sample'], -1, received - 1)
        _text(pause['reason'])


@dataclass(frozen=True, slots=True)
class SavedRecording:
    path: Path
    sha256: str
    byte_count: int
    _data: dict

    def document(self):
        """Return a defensive copy; history is retained as supplied, not re-derived."""
        return deepcopy(self._data)

    @property
    def samples(self):
        return tuple(Sample(**deepcopy(row)) for row in self._data['samples'])

    @property
    def trend_breaks(self):
        first = self._data['first_retained_sample']
        return [index - first for index in self._data['gap_before_samples'] if index >= first]


def load_recording(path: Path) -> SavedRecording:
    """Read only the selected file; embedded paths/targets are display metadata."""
    path = Path(path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    _require(len(raw) <= MAX_FILE_BYTES, 'Desktop recording exceeds the 32 MiB file limit.')
    try:
        text = raw.decode('utf-8-sig')
        _check_depth(text)
        document = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_constant)
        _check_values(document)
        _validate(document)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Desktop recording must be bounded UTF-8 JSON.') from exc
    return SavedRecording(path, hashlib.sha256(raw).hexdigest(), len(raw), document)
