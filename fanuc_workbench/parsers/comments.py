from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .ls import parse_program_info, read_text, write_text


@dataclass(slots=True)
class CommentRecord:
    kind: str
    identifier: str
    comment: str
    value: str
    path: Path
    line_number: int | None = None
    editable: bool = True
    metadata: dict[str, str | int] = field(default_factory=dict)


# FANUC ASCII register listings commonly use:
#   NUMREG.VA:  [1] = 12.5 'Part count'
#   POSREG.VA:  [1,1] = 'Home' ...
_NUMREG_RE = re.compile(r"^(?P<prefix>\s*\[(?P<index>\d+)\]\s*=\s*(?P<value>.*?)\s+)'(?P<comment>[^']*)'(?P<suffix>\s*)$")
_POSREG_RE = re.compile(r"^(?P<prefix>\s*\[(?P<group>\d+)\s*,\s*(?P<index>\d+)\]\s*=\s*)'(?P<comment>[^']*)'(?P<suffix>.*)$")

# /POS labels in an LS listing can carry a point description:
#   P[1:"LOAD POS"]{
_LS_POINT_RE = re.compile(
    r'^(?P<prefix>\s*P\[(?P<index>\d+))(?::"(?P<comment>[^"]*)")?(?P<suffix>\]\s*\{?\s*)$',
    re.IGNORECASE,
)

# IOSTATE.DG-like diagnostic lines. FANUC comments are displayed after the value.
_IO_RE = re.compile(
    r"^(?P<prefix>\s*(?P<type>AIN|AOUT|DIN|DOUT|GIN|GOUT|SI|SO|FLG|RI|RO|UI|UO)\[\s*(?P<index>\d+)\]\s+(?P<value>ON|OFF|-?\d+(?:\.\d+)?)\s+)(?P<comment>.*?)(?P<suffix>\s*)$",
    re.IGNORECASE,
)

_FRAME_SECTION_RE = re.compile(r"^\s*(Tool Frame|Jog Frame|User Frame)\s*$", re.IGNORECASE)
_FRAME_RE = re.compile(
    r"^(?P<prefix>\s*-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+"
    r"-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+)(?P<comment>.*?)(?P<suffix>\s*)$"
)


def _lines(path: Path) -> list[str]:
    return read_text(path).splitlines()


def parse_numreg_comments(path: Path) -> list[CommentRecord]:
    out: list[CommentRecord] = []
    for line_no, line in enumerate(_lines(path), 1):
        m = _NUMREG_RE.match(line)
        if not m:
            continue
        out.append(CommentRecord(
            kind="Numeric Register",
            identifier=f"R[{int(m.group('index'))}]",
            comment=m.group("comment"),
            value=m.group("value").strip(),
            path=path,
            line_number=line_no,
            metadata={"index": int(m.group("index"))},
        ))
    return out


def parse_posreg_comments(path: Path) -> list[CommentRecord]:
    out: list[CommentRecord] = []
    for line_no, line in enumerate(_lines(path), 1):
        m = _POSREG_RE.match(line)
        if not m:
            continue
        group = int(m.group("group")); index = int(m.group("index"))
        rest = m.group("suffix").strip()
        out.append(CommentRecord(
            kind="Position Register",
            identifier=f"PR[{index}]" if group == 1 else f"PR[{group},{index}]",
            comment=m.group("comment"),
            value=rest or "Position data",
            path=path,
            line_number=line_no,
            metadata={"group": group, "index": index},
        ))
    return out


def parse_ls_comments(path: Path) -> list[CommentRecord]:
    out: list[CommentRecord] = []
    info = parse_program_info(path)
    out.append(CommentRecord(
        kind="Program",
        identifier=info.program_name,
        comment=info.comment,
        value="LS program",
        path=path,
        metadata={"program": info.program_name},
    ))

    in_pos = False
    for line_no, line in enumerate(_lines(path), 1):
        stripped = line.strip()
        if stripped.upper() == "/POS":
            in_pos = True
            continue
        if in_pos and stripped.upper() == "/END":
            break
        if not in_pos:
            continue
        m = _LS_POINT_RE.match(line)
        if not m:
            continue
        index = int(m.group("index"))
        out.append(CommentRecord(
            kind="Program Position",
            identifier=f"{info.program_name} P[{index}]",
            comment=m.group("comment") or "",
            value=f"P[{index}] in /POS",
            path=path,
            line_number=line_no,
            metadata={"index": index, "program": info.program_name},
        ))
    return out


def parse_io_comments(path: Path) -> list[CommentRecord]:
    out: list[CommentRecord] = []
    for line_no, line in enumerate(_lines(path), 1):
        m = _IO_RE.match(line)
        if not m:
            continue
        io_type = m.group("type").upper(); index = int(m.group("index"))
        out.append(CommentRecord(
            kind="I/O",
            identifier=f"{io_type}[{index}]",
            comment=m.group("comment").rstrip(),
            value=m.group("value"),
            path=path,
            line_number=line_no,
            metadata={"io_type": io_type, "index": index},
        ))
    return out


def parse_frame_comments(path: Path) -> list[CommentRecord]:
    out: list[CommentRecord] = []
    section = ""
    section_index = 0
    for line_no, line in enumerate(_lines(path), 1):
        sm = _FRAME_SECTION_RE.match(line)
        if sm:
            section = sm.group(1).title()
            section_index = 0
            continue
        if not section:
            continue
        m = _FRAME_RE.match(line)
        if not m:
            continue
        section_index += 1
        prefix = {"Tool Frame": "UTOOL", "Jog Frame": "JOG", "User Frame": "UFRAME"}.get(section, section.upper())
        out.append(CommentRecord(
            kind=section,
            identifier=f"{prefix}[{section_index}]",
            comment=m.group("comment").rstrip(),
            value="Frame XYZ WPR",
            path=path,
            line_number=line_no,
            # FRAME.DG is a generated diagnostic listing. Editing the local copy is
            # useful for documentation/diff work, but it is not a restorable frame file.
            editable=True,
            metadata={"section": section, "index": section_index, "local_only": 1},
        ))
    return out


def parse_comment_file(path: Path) -> list[CommentRecord]:
    name = path.name.upper()
    if path.suffix.upper() == ".LS":
        return parse_ls_comments(path)
    if name == "NUMREG.VA":
        return parse_numreg_comments(path)
    if name == "POSREG.VA":
        return parse_posreg_comments(path)
    if name == "IOSTATE.DG":
        return parse_io_comments(path)
    if name == "FRAME.DG":
        return parse_frame_comments(path)
    return []


def _validate_comment(new_comment: str, quote: str | None = None) -> None:
    if "\n" in new_comment or "\r" in new_comment:
        raise ValueError("Comments/descriptions cannot contain line breaks")
    if quote and quote in new_comment:
        raise ValueError(f"Comment cannot contain {quote!r}")


def _replace_line(path: Path, line_number: int, replacer) -> None:
    text = read_text(path)
    newline = "\r\n" if "\r\n" in text else "\n"
    had_trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    if line_number < 1 or line_number > len(lines):
        raise ValueError(f"Source line {line_number} no longer exists")
    lines[line_number - 1] = replacer(lines[line_number - 1])
    result = newline.join(lines) + (newline if had_trailing_newline else "")
    write_text(path, result)


def set_numreg_comment(record: CommentRecord, new_comment: str) -> None:
    _validate_comment(new_comment, "'")
    def replace(line: str) -> str:
        m = _NUMREG_RE.match(line)
        if not m or int(m.group("index")) != int(record.metadata["index"]):
            raise ValueError("NUMREG.VA record changed; refresh the workspace and try again")
        return f"{m.group('prefix')}'{new_comment}'{m.group('suffix')}"
    _replace_line(record.path, int(record.line_number or 0), replace)


def set_posreg_comment(record: CommentRecord, new_comment: str) -> None:
    _validate_comment(new_comment, "'")
    def replace(line: str) -> str:
        m = _POSREG_RE.match(line)
        if not m:
            raise ValueError("POSREG.VA record changed; refresh the workspace and try again")
        if int(m.group("group")) != int(record.metadata["group"]) or int(m.group("index")) != int(record.metadata["index"]):
            raise ValueError("POSREG.VA record changed; refresh the workspace and try again")
        return f"{m.group('prefix')}'{new_comment}'{m.group('suffix')}"
    _replace_line(record.path, int(record.line_number or 0), replace)


def set_ls_point_comment(record: CommentRecord, new_comment: str) -> None:
    _validate_comment(new_comment, '"')
    def replace(line: str) -> str:
        m = _LS_POINT_RE.match(line)
        if not m or int(m.group("index")) != int(record.metadata["index"]):
            raise ValueError("LS position record changed; refresh the workspace and try again")
        comment = f':"{new_comment}"' if new_comment else ""
        return f"{m.group('prefix')}{comment}{m.group('suffix')}"
    _replace_line(record.path, int(record.line_number or 0), replace)


def set_io_comment(record: CommentRecord, new_comment: str) -> None:
    _validate_comment(new_comment)
    def replace(line: str) -> str:
        m = _IO_RE.match(line)
        if not m:
            raise ValueError("I/O record changed; refresh the workspace and try again")
        if m.group("type").upper() != str(record.metadata["io_type"]) or int(m.group("index")) != int(record.metadata["index"]):
            raise ValueError("I/O record changed; refresh the workspace and try again")
        return f"{m.group('prefix')}{new_comment}{m.group('suffix')}"
    _replace_line(record.path, int(record.line_number or 0), replace)


def set_frame_comment(record: CommentRecord, new_comment: str) -> None:
    _validate_comment(new_comment)
    def replace(line: str) -> str:
        m = _FRAME_RE.match(line)
        if not m:
            raise ValueError("Frame record changed; refresh the workspace and try again")
        return f"{m.group('prefix')}{new_comment}{m.group('suffix')}"
    _replace_line(record.path, int(record.line_number or 0), replace)


def set_comment(record: CommentRecord, new_comment: str) -> None:
    if record.kind == "Program":
        from .ls import set_program_comment
        set_program_comment(record.path, new_comment)
    elif record.kind == "Program Position":
        set_ls_point_comment(record, new_comment)
    elif record.kind == "Numeric Register":
        set_numreg_comment(record, new_comment)
    elif record.kind == "Position Register":
        set_posreg_comment(record, new_comment)
    elif record.kind == "I/O":
        set_io_comment(record, new_comment)
    elif record.kind in {"Tool Frame", "Jog Frame", "User Frame"}:
        set_frame_comment(record, new_comment)
    else:
        raise ValueError(f"Editing is not implemented for {record.kind}")
