from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from fanuc_workbench.parsers.ls import read_text


@dataclass(slots=True)
class FileChange:
    relative_path: str
    status: str
    old_size: int = 0
    new_size: int = 0
    semantic: bool = False
    detail: str = ""


_LINE_NO = re.compile(r"^(\s*)\d+\s*:\s*")
_VOLATILE_ATTR = re.compile(r"^\s*(DATE|MODIFIED|CREATE_DATE|MOD_DATE)\s*=.*$", re.I)


def _rel_files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in root.rglob('*'):
        if not p.is_file() or p.name.endswith('.before_comment_edit') or p.name == 'backup_manifest.json': continue
        out[p.relative_to(root).as_posix()] = p
    return out


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def semantic_ls_lines(path: Path) -> list[str]:
    rows: list[str] = []
    for line in read_text(path).splitlines():
        if _VOLATILE_ATTR.match(line): continue
        m = _LINE_NO.match(line)
        if m:
            line = line[m.end():].strip()
        rows.append(line.rstrip())
    return rows


def text_diff(old: Path, new: Path, max_lines: int = 5000) -> str:
    try:
        a = read_text(old).splitlines(); b = read_text(new).splitlines()
    except Exception:
        return "Binary/non-text file changed"
    if old.suffix.upper() == '.LS' and new.suffix.upper() == '.LS':
        a = semantic_ls_lines(old); b = semantic_ls_lines(new)
    diff = list(difflib.unified_diff(a, b, fromfile=str(old), tofile=str(new), lineterm=''))
    if len(diff) > max_lines: diff = diff[:max_lines] + [f"... diff truncated after {max_lines} lines ..."]
    return "\n".join(diff)


def compare_workspaces(old_root: Path, new_root: Path) -> list[FileChange]:
    old = _rel_files(old_root); new = _rel_files(new_root); changes: list[FileChange] = []
    for rel in sorted(set(old) | set(new), key=str.upper):
        a = old.get(rel); b = new.get(rel)
        if a is None:
            changes.append(FileChange(rel, 'Added', 0, b.stat().st_size if b else 0)); continue
        if b is None:
            changes.append(FileChange(rel, 'Deleted', a.stat().st_size, 0)); continue
        if _hash(a) == _hash(b): continue
        semantic = False
        if a.suffix.upper() == '.LS' and b.suffix.upper() == '.LS':
            semantic = semantic_ls_lines(a) == semantic_ls_lines(b)
        changes.append(FileChange(rel, 'Metadata-only' if semantic else 'Modified', a.stat().st_size, b.stat().st_size, semantic,
                                  '' if semantic else text_diff(a,b)))
    return changes
