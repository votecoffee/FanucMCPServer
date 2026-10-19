from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from fanuc_workbench.parsers.ls import read_text, write_text


@dataclass(slots=True)
class NumericRegister:
    path: Path
    index: int
    value: str
    comment: str = ""
    line_number: int = 0


@dataclass(slots=True)
class PositionRegister:
    path: Path
    group: int
    index: int
    comment: str = ""
    representation: str = "Unknown"
    user_frame: str = ""
    user_tool: str = ""
    config: str = ""
    values: dict[str, float] = field(default_factory=dict)
    start_line: int = 0
    end_line: int = 0
    unavailable_axes: tuple[str, ...] = ()


@dataclass(slots=True)
class FrameData:
    path: Path
    kind: str
    index: int
    values: dict[str, float]
    comment: str = ""
    line_number: int = 0


@dataclass(slots=True)
class IOData:
    path: Path
    io_type: str
    index: int
    value: str
    comment: str = ""
    rack: str = ""
    slot: str = ""
    start: str = ""
    line_number: int = 0


_NUMREG = re.compile(r"^\s*\[(?P<idx>\d+)\]\s*=\s*(?P<value>.*?)\s+'(?P<comment>[^']*)'\s*$")
_PR_START = re.compile(r"^\s*\[(?P<group>\d+)\s*,\s*(?P<idx>\d+)\]\s*=\s*'(?P<comment>[^']*)'(?P<tail>.*)$")
_REAL = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
_AXIS = re.compile(r"\b(X|Y|Z|W|P|R|J\d+|E\d+)\s*[:=]\s*([^\s,;]+)", re.I)
_UF = re.compile(r"\b(?:UF|UFRAME)\s*[:=]\s*(-?\d+)", re.I)
_UT = re.compile(r"\b(?:UT|UTOOL)\s*[:=]\s*(-?\d+)", re.I)
_CONFIG = re.compile(r"\bCONFIG\s*[:=]\s*'?([^'\r\n,]+(?:,[^'\r\n]+){0,3})'?", re.I)
_FRAME_HEADER = re.compile(r"^\s*(Tool Frame|Jog Frame|User Frame)\s*$", re.I)
_FRAME_LINE = re.compile(
    rf"^\s*(?P<x>{_REAL})\s+(?P<y>{_REAL})\s+(?P<z>{_REAL})\s+"
    rf"(?P<w>{_REAL})\s+(?P<p>{_REAL})\s+(?P<r>{_REAL})(?:\s+(?P<comment>.*?))?\s*$"
)
_IO = re.compile(
    r"^\s*(?P<type>AIN|AOUT|DIN|DOUT|GIN|GOUT|SI|SO|FLG|RI|RO|UI|UO)\[\s*(?P<idx>\d+)\]\s+"
    r"(?P<value>ON|OFF|-?\d+(?:\.\d+)?)(?:\s+(?P<comment>.*?))?\s*$", re.I
)
_IO_MAP = re.compile(
    r"(?P<type>DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|DIN|DOUT|GIN|GOUT|AIN|AOUT)\[\s*(?P<idx>\d+)\].*?"
    r"(?:RACK\s*[:=]\s*(?P<rack>\d+)).*?(?:SLOT\s*[:=]\s*(?P<slot>\d+)).*?(?:START\s*[:=]\s*(?P<start>\d+))",
    re.I,
)


def parse_numeric_registers(path: Path) -> list[NumericRegister]:
    rows: list[NumericRegister] = []
    for line_no, line in enumerate(read_text(path).splitlines(), 1):
        m = _NUMREG.match(line)
        if m:
            rows.append(NumericRegister(path, int(m.group('idx')), m.group('value').strip(), m.group('comment'), line_no))
    return rows


def set_numeric_register_value(record: NumericRegister, new_value: str) -> None:
    if "\n" in new_value or "\r" in new_value or "'" in new_value:
        raise ValueError("Invalid numeric register value")
    text = read_text(record.path)
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    i = record.line_number - 1
    m = _NUMREG.match(lines[i]) if 0 <= i < len(lines) else None
    if not m or int(m.group('idx')) != record.index:
        raise ValueError("NUMREG.VA changed; refresh before editing")
    prefix = lines[i][:m.start('value')]
    gap = lines[i][m.end('value'):m.start('comment')-1]
    lines[i] = f"{prefix}{new_value}{gap}'{record.comment}'"
    write_text(record.path, newline.join(lines) + (newline if trailing else ""))


def parse_position_registers(path: Path) -> list[PositionRegister]:
    lines = read_text(path).splitlines()
    starts: list[tuple[int, re.Match[str]]] = []
    for i, line in enumerate(lines):
        m = _PR_START.match(line)
        if m:
            starts.append((i, m))
    rows: list[PositionRegister] = []
    for n, (start, m) in enumerate(starts):
        end = starts[n + 1][0] - 1 if n + 1 < len(starts) else len(lines) - 1
        blob = "\n".join([m.group('tail')] + lines[start + 1:end + 1])
        vals, unavailable, seen = {}, set(), set()
        for axis in _AXIS.finditer(blob):
            name, token = axis.group(1).upper(), axis.group(2)
            if name in seen or not re.fullmatch(_REAL, token):
                unavailable.add(name)
            elif math.isfinite(number := float(token)):
                vals[name] = number
            else:
                unavailable.add(name)
            seen.add(name)
        for name in unavailable:
            vals.pop(name, None)
        uf = _UF.search(blob); ut = _UT.search(blob); cfg = _CONFIG.search(blob)
        rep = "Joint" if any(k.startswith("J") for k in seen) else ("Cartesian" if seen else "Uninitialized")
        rows.append(PositionRegister(
            path=path, group=int(m.group('group')), index=int(m.group('idx')), comment=m.group('comment'),
            representation=rep, user_frame=uf.group(1) if uf else "", user_tool=ut.group(1) if ut else "",
            config=cfg.group(1).strip() if cfg else "", values=vals, start_line=start + 1, end_line=end + 1,
            unavailable_axes=tuple(sorted(unavailable)),
        ))
    return rows


def _replace_record_lines(path: Path, start_line: int, end_line: int, new_lines: list[str]) -> None:
    text = read_text(path); newline = "\r\n" if "\r\n" in text else "\n"; trailing = text.endswith(("\n", "\r"))
    lines = text.splitlines(); lines[start_line-1:end_line] = new_lines
    write_text(path, newline.join(lines) + (newline if trailing else ""))


def set_position_register(record: PositionRegister, *, comment: str | None = None, values: dict[str, float] | None = None,
                          user_frame: str | None = None, user_tool: str | None = None) -> None:
    if comment is not None and "'" in comment:
        raise ValueError("PR comments cannot contain apostrophes in POSREG.VA")
    text_lines = read_text(record.path).splitlines()[record.start_line-1:record.end_line]
    if not text_lines:
        raise ValueError("Position register record no longer exists")
    first = _PR_START.match(text_lines[0])
    if not first or int(first.group('group')) != record.group or int(first.group('idx')) != record.index:
        raise ValueError("POSREG.VA changed; refresh before editing")
    new_comment = record.comment if comment is None else comment
    text_lines[0] = re.sub(r"'[^']*'", f"'{new_comment}'", text_lines[0], count=1)
    replacements = dict(values or {})
    if user_frame is not None:
        for i, line in enumerate(text_lines):
            if _UF.search(line): text_lines[i] = _UF.sub(lambda m: m.group(0).replace(m.group(1), str(user_frame)), line, count=1)
    if user_tool is not None:
        for i, line in enumerate(text_lines):
            if _UT.search(line): text_lines[i] = _UT.sub(lambda m: m.group(0).replace(m.group(1), str(user_tool)), line, count=1)
    for axis, val in replacements.items():
        axis = axis.upper()
        pat = re.compile(rf"\b{re.escape(axis)}\s*([:=])\s*({_REAL})(?![\w.])", re.I)
        for i, line in enumerate(text_lines):
            if pat.search(line):
                text_lines[i] = pat.sub(lambda m: f"{axis} {m.group(1)} {float(val):.3f}", line, count=1)
                break
    _replace_record_lines(record.path, record.start_line, record.end_line, text_lines)


def parse_frames(path: Path) -> list[FrameData]:
    rows: list[FrameData] = []; section = ""; idx = 0
    for line_no, line in enumerate(read_text(path).splitlines(), 1):
        hm = _FRAME_HEADER.match(line)
        if hm:
            section = hm.group(1).title(); idx = 0; continue
        if not section: continue
        m = _FRAME_LINE.match(line)
        if not m: continue
        idx += 1
        kind = {"Tool Frame":"UTOOL", "User Frame":"UFRAME", "Jog Frame":"JOG"}[section]
        vals = {k.upper(): float(m.group(k)) for k in ("x","y","z","w","p","r")}
        rows.append(FrameData(path, kind, idx, vals, m.group('comment') or "", line_no))
    return rows


def parse_io(path: Path) -> list[IOData]:
    rows: list[IOData] = []
    for line_no, line in enumerate(read_text(path).splitlines(), 1):
        m = _IO.match(line)
        if m:
            rows.append(IOData(path, m.group('type').upper(), int(m.group('idx')), m.group('value'), m.group('comment') or "", line_number=line_no))
    # Enrich mappings from any mapping-looking lines in the same file.
    mappings: dict[tuple[str,int], tuple[str,str,str]] = {}
    for line in read_text(path).splitlines():
        m = _IO_MAP.search(line)
        if m:
            typ = m.group('type').upper().replace('DIN','DI').replace('DOUT','DO').replace('GIN','GI').replace('GOUT','GO').replace('AIN','AI').replace('AOUT','AO')
            mappings[(typ,int(m.group('idx')))] = (m.group('rack'),m.group('slot'),m.group('start'))
    for row in rows:
        typ = row.io_type.replace('DIN','DI').replace('DOUT','DO').replace('GIN','GI').replace('GOUT','GO').replace('AIN','AI').replace('AOUT','AO')
        if (typ,row.index) in mappings:
            row.rack,row.slot,row.start = mappings[(typ,row.index)]
    return rows
