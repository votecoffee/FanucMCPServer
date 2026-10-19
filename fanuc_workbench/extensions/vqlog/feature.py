"""Offline VQLOG analysis with no controller dependencies.

Event formats and the conventional 2 ms tick default are adapted from
onerobotics/vqlog.js, Copyright (c) 2014 Jay Strybis, MIT (see LICENSE.txt).
Python adaptation adds bounded input, unknown records and explicit timing limits.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import math
from pathlib import Path
import re


_PART = r"area (?P<area_id>\d+), work_id (?P<work_id>\d+), tray (?P<tray_id>\d+), model_id (?P<model_id>\d+)"
_PATTERNS = [
    ("Acknowledgement", r"Receive ACKQUE, " + _PART + r", ack (?P<ack>\d+)"),
    ("Addition", r"Put a part to queue, " + _PART),
    ("Allocation", r"Part is allocated, " + _PART),
    ("Clear", r"Clear load stat, area (?P<area_id>\d+), npicked (?P<npicked_num>\d+)/(?P<npicked_den>\d+)"),
    ("LoadBalance", r"Load-balance, area (?P<area_id>\d+), model_id (?P<model_id>\d+), "
                  r"npicked (?P<npicked_num>\d+)/(?P<npicked_den>\d+), n2pick (?P<n2pick_num>\d+)/(?P<n2pick_den>\d+)"),
    ("Overlap", r"Part is overlapped, " + _PART),
    ("Request", r"Receive GETQUE, area (?P<area_id>\d+), timeout (?P<timeout>\d+), consec (?P<consec>\d+), "
                r"model_id (?P<model_id>\d+|\*+), work_id (?P<work_id>\d+|\*+)"),
    ("SetLBD", r"Receive SETLBD, line (?P<line_id>\d+)"),
    ("Skip", r"Part is skipped \((?P<reason>exit boundary|npicked > n2pick)\), " + _PART
             + r"(?:, cur_x (?P<current_x>-?\d+), bound (?P<bound>-?\d+))?"),
    ("Timeout", r"Get_queue timeout, area (?P<area_id>\d+)"),
]
_EVENTS = [(name, re.compile(pattern)) for name, pattern in _PATTERNS]
EVENT_TYPES = tuple(name for name, _ in _EVENTS) + ("Unknown", "Malformed", "Empty")
_LINE = re.compile(r"\s*(?P<tick>-?\d+)\s*,\s*(?P<message>.*?)\s*", re.DOTALL)
MAX_BYTES, MAX_LINES, MAX_LINE_LENGTH = 16 * 1024 * 1024, 50000, 4096


def _integer(value):
    number = int(value)
    if not -(2 ** 63) <= number < 2 ** 63:
        raise ValueError("VQLOG integer exceeds its supported range")
    return number


def parse_line(line: str, line_number: int = 1) -> dict:
    if len(line) > MAX_LINE_LENGTH:
        raise ValueError("VQLOG line exceeds its length limit")
    row = {"line_number": line_number, "tick": None, "event_type": "Malformed", "message": line, "fields": {}}
    match = _LINE.fullmatch(line)
    if match is None:
        return row
    try:
        row["tick"] = _integer(match["tick"])
    except ValueError:
        return row
    row["message"] = match["message"]
    row["event_type"] = "Unknown" if row["message"] else "Empty"
    for name, pattern in _EVENTS:
        event = pattern.fullmatch(row["message"])
        if event is None:
            continue
        fields = {}
        try:
            for key, value in event.groupdict().items():
                fields[key] = (value if key == "reason" else None if value is None or value.startswith('*')
                               else _integer(value))
        except ValueError:
            row["event_type"] = "Malformed"
            return row
        row.update(event_type=name, fields=fields)
        break
    return row


def analyze_file(path: Path, *, offset: int = 0, limit: int = 100, event_type: str = "",
                 area_id: int | None = None, work_id: int | None = None, tick_seconds: float = 0.002) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Use offset >= 0 and a page limit between 1 and 100")
    if event_type and event_type not in EVENT_TYPES:
        raise ValueError("Unknown VQLOG event type")
    for value in (area_id, work_id):
        if value is not None and (type(value) is not int or not 0 <= value < 2 ** 63):
            raise ValueError("VQLOG filters require nonnegative integer IDs")
    if (type(tick_seconds) not in {int, float} or not math.isfinite(tick_seconds)
            or not 0 < tick_seconds <= 3600):
        raise ValueError("Supply a finite positive tick period, at most 3600 seconds")
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("VQLOG file exceeds the 16 MiB analysis limit")
    try:
        text, encoding = data.decode('utf-8-sig'), 'utf-8'
    except UnicodeDecodeError:
        try:
            text, encoding = data.decode('cp1252'), 'cp1252'
        except UnicodeDecodeError:
            raise ValueError("VQLOG encoding is unsupported") from None
    rows = []
    for number, line in enumerate(io.StringIO(text), 1):
        if number > MAX_LINES:
            raise ValueError("VQLOG file exceeds its 50000-line analysis limit")
        line = line.rstrip('\r\n')
        if line.strip():
            rows.append(parse_line(line, number))
    ticks = [row['tick'] for row in rows if row['tick'] is not None]
    regressions = sum(after < before for before, after in zip(ticks, ticks[1:]))
    clock_available = bool(ticks) and len(ticks) == len(rows) and not regressions
    start_tick = ticks[0] if clock_available else None
    for row in rows:
        row['elapsed_seconds'] = ((row['tick'] - start_tick) * tick_seconds if clock_available else None)
    counts = dict(sorted(Counter(row['event_type'] for row in rows).items()))
    selected = [row for row in rows if (not event_type or row['event_type'] == event_type)
                and (area_id is None or row['fields'].get('area_id') == area_id)
                and (work_id is None or row['fields'].get('work_id') == work_id)]
    return {'source_sha256': hashlib.sha256(data).hexdigest(), 'source_bytes': len(data), 'encoding': encoding,
            'records_total': len(rows), 'total': len(selected), 'offset': offset,
            'next_offset': offset + limit if offset + limit < len(selected) else None,
            'items': selected[offset:offset + limit], 'event_counts': counts,
            'tick_seconds': tick_seconds, 'tick_period_verified': False,
            'clock_available': clock_available, 'tick_regressions': regressions,
            'duration_seconds': (ticks[-1] - ticks[0]) * tick_seconds if clock_available else None,
            'unknown_events': counts.get('Unknown', 0), 'malformed_records': counts.get('Malformed', 0),
            'evidence_only': True}


def self_test():
    row = parse_line('0, Part is allocated, area 1, work_id 2, tray 3, model_id 4')
    return row['event_type'] == 'Allocation' and row['fields']['work_id'] == 2


def handle(operation_id, arguments, context):
    if operation_id != 'workbench.vqlog.analyze':
        raise ValueError("Unknown VQLOG operation")
    options = {key: value for key, value in arguments.items() if key not in {'artifact_id', 'path'}}
    return analyze_file(Path(context['log']['file']), **options)
