from __future__ import annotations

import json
from pathlib import Path

from fanuc_workbench.core.models import ProgramInfo
from fanuc_workbench.parsers.comments import CommentRecord, parse_comment_file
from fanuc_workbench.parsers.ls import parse_program_info, parse_positions
from fanuc_workbench.parsers.va import parse_assignments


def scan_programs(root: Path) -> list[ProgramInfo]:
    out: list[ProgramInfo] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.upper() != ".LS":
            continue
        try:
            out.append(parse_program_info(path))
        except Exception:
            continue
    return sorted(out, key=lambda p: p.program_name.upper())


def scan_comments(root: Path) -> list[CommentRecord]:
    out: list[CommentRecord] = []
    supported_names = {"NUMREG.VA", "POSREG.VA", "IOSTATE.DG", "FRAME.DG"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.upper() != ".LS" and path.name.upper() not in supported_names:
            continue
        # Ignore our safety copies so a changed record is not shown twice.
        if ".before_comment_edit" in path.name.lower():
            continue
        try:
            out.extend(parse_comment_file(path))
        except Exception:
            continue
    return sorted(out, key=lambda r: (r.kind.upper(), r.identifier.upper(), str(r.path).upper()))


def scan_positions(root: Path):
    out = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.upper() != ".LS":
            continue
        try:
            out.extend(parse_positions(path))
        except Exception:
            continue
    return out


def scan_variables(root: Path):
    out = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.upper() != ".VA":
            continue
        try:
            for row in parse_assignments(path):
                out.append((path, row))
        except Exception:
            continue
    return out


def load_manifest(root: Path) -> dict:
    path = root / "backup_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
