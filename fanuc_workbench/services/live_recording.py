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
from uuid import uuid4

from fanuc_workbench.services.monitoring import (CycleRecorder, Sample, TimelineRecorder,
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
        self.trend.add(captured);self.timeline.add(captured)
        if self.cycle_recording and cycle_key and cycle_key not in captured.errors:
            self.cycle.add_sample_state(captured, cycle_key)
        self.last_sample = captured
        self.received_samples += 1

    def document(self):
        return {'format': 'fanuc-desktop-recording', 'schema_version': 1,
            'recording_id': self.recording_id, 'created_at_utc': self.created_at,
            'source': deepcopy(self.source), 'watches': deepcopy(self.watches),
            'sample_limit': self.sample_limit, 'received_samples': self.received_samples,
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
