"""Source-bound desktop recordings without Qt or controller connections."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from fanuc_workbench.services.monitoring import (CycleEvent, CycleRecorder, Sample, TimelineRecorder,
    TrendRecorder, WATCH_RE)


def recording_source(*, live=False, profile=None, workspace=None) -> dict:
    if live:
        data = profile.to_dict() if profile is not None else None
        if data is not None:
            data.pop('password', None)
        return {'kind': 'controller_files', 'profile': data}
    return {'kind': 'workspace', 'path': str(Path(workspace).resolve()) if workspace is not None else None}


def watch_definitions(specs) -> list[dict]:
    result, keys = [], set()
    for spec in specs:
        expression, alias = spec.expression.strip(), spec.alias.strip()
        if not WATCH_RE.fullmatch(expression):
            raise ValueError(f'Unsupported watch expression: {expression}')
        key = alias or expression
        if key in keys:
            raise ValueError(f'Watch names must be unique: {key}')
        keys.add(key)
        result.append({'expression': expression, 'alias': alias})
    if not result:
        raise ValueError('Add at least one watch expression.')
    return result


def numeric_segments(samples, key, *, break_before=()):
    """Split plotted points wherever a signal is missing, invalid or paused."""
    segments, segment, previous = [], [], None
    breaks = set(break_before)
    for index, sample in enumerate(samples):
        try:
            value = float(sample.values.get(key))
            valid = key not in sample.errors and math.isfinite(value) and math.isfinite(sample.timestamp)
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid or index in breaks or (previous is not None and sample.timestamp < previous):
            if segment:segments.append(segment)
            segment = []
        if valid:segment.append((sample.timestamp, value));previous = sample.timestamp
        else:previous = None
    if segment:segments.append(segment)
    return segments


class LiveRecording:
    def __init__(self, *, sample_limit=10000):
        if type(sample_limit) is not int or sample_limit < 1:
            raise ValueError('Sample limit must be a positive integer.')
        self.recording_id = uuid4().hex
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.sample_limit, self.received_samples = sample_limit, 0
        self.source, self.watches = None, []
        self.trend, self.timeline, self.cycle = TrendRecorder(), TimelineRecorder(), CycleRecorder()
        self.last_sample = None
        self.cycle_recording = False
        self.pauses, self.gap_before_samples = [], []
        self._gap_pending, self._timeline_reset_pending = False, False

    @property
    def has_data(self):
        return bool(self.received_samples or self.cycle.events or self.timeline.events)

    def matches(self, source, specs):
        return self.source == source and self.watches == watch_definitions(specs)

    def ensure_context(self, source, specs):
        definitions = watch_definitions(specs)
        if self.has_data and (self.source != source or self.watches != definitions):
            raise ValueError('The source or watches differ from this recording. Export it and choose New recording first.')
        if self.received_samples >= self.sample_limit:
            raise ValueError(f'Recording reached {self.sample_limit} samples. Export it and choose New recording to continue.')
        if not self.has_data:
            self.source, self.watches = deepcopy(source), definitions

    def add(self, sample: Sample, *, cycle_key=''):
        if self.source is None:
            raise ValueError('Bind the recording source before adding samples.')
        if self.received_samples >= self.sample_limit:
            raise ValueError('Recording sample limit reached.')
        if type(sample.timestamp) not in (int, float) or not math.isfinite(sample.timestamp):
            raise ValueError('Sample timestamp must be finite.')
        try:datetime.fromtimestamp(sample.timestamp, timezone.utc)
        except (OSError, ValueError, OverflowError) as exc:raise ValueError('Sample timestamp is outside the supported date range.') from exc
        if self.last_sample is not None and sample.timestamp < self.last_sample.timestamp:
            raise ValueError('Sample time moved backwards. Start a new recording before continuing.')
        captured = deepcopy(sample)
        if not isinstance(captured.values, dict) or not isinstance(captured.errors, dict):
            raise ValueError('Sample values and errors must be dictionaries.')
        if any(not isinstance(key, str) for key in [*captured.values, *captured.errors]) or any(not isinstance(error, str) for error in captured.errors.values()):
            raise ValueError('Sample names and error messages must be strings.')
        json.dumps(asdict(captured), allow_nan=False)
        timeline = TimelineRecorder()
        timeline.events = self.timeline.events
        if self.last_sample is not None and not self._gap_pending and not self._timeline_reset_pending:
            previous = self.last_sample
            valid = {key: value for key, value in previous.values.items()
                     if value is not None and key not in previous.errors and key in captured.values
                     and captured.values[key] is not None and key not in captured.errors}
            timeline.add(Sample(previous.timestamp, valid))
        timeline.add(captured)
        if self._gap_pending and self.received_samples:
            self.gap_before_samples.append(self.received_samples)
        self.trend.add(captured);self.timeline = timeline
        if self.cycle_recording and cycle_key:
            value = captured.values.get(cycle_key)
            if cycle_key in captured.errors or value is None or isinstance(value, (dict,list)):
                if not self.cycle.events or self.cycle.events[-1].state!='UNAVAILABLE':
                    self.cycle.events.append(CycleEvent(captured.timestamp,'UNAVAILABLE',cycle_key))
            else:self.cycle.add_sample_state(captured, cycle_key)
        self.last_sample = captured
        self.received_samples += 1
        self._gap_pending = self._timeline_reset_pending = False

    def pause(self, reason, *, timestamp=None):
        if not self.has_data or self._gap_pending:return
        timestamp = time.time() if timestamp is None else timestamp
        if type(timestamp) not in (int,float) or not math.isfinite(timestamp):
            raise ValueError('Pause timestamp must be finite.')
        self.pauses.append({'timestamp':timestamp, 'after_sample':self.received_samples-1, 'reason':reason})
        self._gap_pending = True
        if self.cycle_recording:self.cycle.events.append(CycleEvent(timestamp,'PAUSED',reason))

    def clear_timeline(self):
        self.timeline = TimelineRecorder()
        self._timeline_reset_pending = True

    @property
    def trend_breaks(self):
        first = self.received_samples - len(self.trend.samples)
        return [index-first for index in self.gap_before_samples if first<=index<self.received_samples]

    def document(self):
        return {'format': 'fanuc-desktop-recording', 'schema_version': 1,
            'recording_id': self.recording_id, 'created_at_utc': self.created_at,
            'source': deepcopy(self.source), 'watches': deepcopy(self.watches),
            'sample_limit': self.sample_limit, 'received_samples': self.received_samples,
            'first_retained_sample': self.received_samples-len(self.trend.samples),
            'gap_before_samples': list(self.gap_before_samples), 'pauses': deepcopy(self.pauses),
            'samples': [asdict(sample) for sample in self.trend.samples],
            'timeline': [asdict(event) for event in self.timeline.events],
            'cycle': [asdict(event) for event in self.cycle.events],
            'cycle_recording': self.cycle_recording}

    def export_json(self, path: Path) -> Path:
        path = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.recording-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.document(), stream, indent=2, allow_nan=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return path
