from __future__ import annotations

import re
from pathlib import Path

from fanuc_workbench.core.models import PositionRecord, ProgramInfo


_PROG_RE = re.compile(r"^/PROG\s+([^\s]+)", re.MULTILINE | re.IGNORECASE)
_COMMENT_RE = re.compile(r'^(\s*COMMENT\s*=\s*)"([^"]*)"(\s*;?\s*)$', re.MULTILINE | re.IGNORECASE)
_POS_START_RE = re.compile(r'^P\[(\d+)(?::"[^"]*")?\](?:\s*\{)?\s*$', re.IGNORECASE)
_GP_RE = re.compile(r"^\s*GP(\d+):", re.IGNORECASE)
_KV_RE = re.compile(r"\b(UF|UT)\s*:\s*([^,]+)", re.IGNORECASE)
_AXIS_RE = re.compile(r"\b(X|Y|Z|W|P|R|J\d+)\s*=\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
_CONFIG_RE = re.compile(r"CONFIG\s*:\s*'([^']+)'", re.IGNORECASE)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def write_text(path: Path, text: str) -> None:
    # Retain the original text encoding when editing an existing listing.
    encoding = "cp1252"
    if path.exists():
        original = path.read_bytes()
        try:
            original.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            original.decode("cp1252")
    try:
        data = text.encode(encoding)
    except UnicodeEncodeError as exc:
        raise ValueError("Edited text cannot be represented in the original file encoding") from exc
    path.write_bytes(data)


def parse_program_info(path: Path) -> ProgramInfo:
    text = read_text(path)
    pm = _PROG_RE.search(text)
    cm = _COMMENT_RE.search(text)
    return ProgramInfo(
        path=path,
        program_name=pm.group(1).strip() if pm else path.stem,
        comment=cm.group(2) if cm else "",
    )


def set_program_comment(path: Path, new_comment: str) -> ProgramInfo:
    if '"' in new_comment or "\n" in new_comment or "\r" in new_comment:
        raise ValueError("Program comments cannot contain quotes or line breaks")
    text = read_text(path)
    if _COMMENT_RE.search(text):
        text = _COMMENT_RE.sub(lambda m: f'{m.group(1)}"{new_comment}"{m.group(3)}', text, count=1)
    else:
        attr = re.search(r"^/ATTR\s*$", text, re.MULTILINE | re.IGNORECASE)
        if not attr:
            raise ValueError("No /ATTR section found in LS file")
        insert_at = attr.end()
        text = text[:insert_at] + f'\nCOMMENT = "{new_comment}";' + text[insert_at:]
    write_text(path, text)
    return parse_program_info(path)


def parse_positions(path: Path) -> list[PositionRecord]:
    text = read_text(path)
    pm = _PROG_RE.search(text)
    program = pm.group(1).strip() if pm else path.stem
    in_pos = False
    current_index: int | None = None
    current_group = "GP1"
    current_lines: list[str] = []
    out: list[PositionRecord] = []

    def flush() -> None:
        nonlocal current_index, current_group, current_lines
        if current_index is None:
            return
        blob = " ".join(current_lines)
        kv = {m.group(1).upper(): m.group(2).strip() for m in _KV_RE.finditer(blob)}
        axes = {m.group(1).upper(): float(m.group(2)) for m in _AXIS_RE.finditer(blob)}
        cm = _CONFIG_RE.search(blob)
        if axes:
            out.append(
                PositionRecord(
                    program=program,
                    index=current_index,
                    group=current_group,
                    user_frame=kv.get("UF", ""),
                    user_tool=kv.get("UT", ""),
                    config=cm.group(1) if cm else "",
                    values=axes,
                )
            )
        current_index = None
        current_group = "GP1"
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.upper() == "/POS":
            in_pos = True
            continue
        if not in_pos:
            continue
        if line.upper() == "/END":
            flush()
            break
        m = _POS_START_RE.match(line)
        if m:
            flush()
            current_index = int(m.group(1))
            continue
        if current_index is not None:
            gm = _GP_RE.match(line)
            if gm:
                current_group = f"GP{gm.group(1)}"
            current_lines.append(line)
            if line.endswith("};") or line == "};":
                flush()
    flush()
    return out
