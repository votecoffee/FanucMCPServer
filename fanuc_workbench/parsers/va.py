from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .ls import read_text


@dataclass(slots=True)
class VariableRow:
    name: str
    value: str
    source_line: int


_ASSIGN_RE = re.compile(r"^\s*([^!#;][^=]{0,120}?)\s*=\s*(.*?)\s*;?\s*$")


def parse_assignments(path: Path) -> list[VariableRow]:
    rows: list[VariableRow] = []
    for line_no, line in enumerate(read_text(path).splitlines(), 1):
        m = _ASSIGN_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        value = m.group(2).strip()
        if name and value:
            rows.append(VariableRow(name=name, value=value, source_line=line_no))
    return rows
