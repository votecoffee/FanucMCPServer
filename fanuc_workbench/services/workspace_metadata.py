from __future__ import annotations

import json
import re
from pathlib import Path

from fanuc_workbench.services.commissioning import ChecklistItem, load_checklist, save_checklist
from fanuc_workbench.services.handshake import HandshakeSignal, load_handshake, save_handshake
from fanuc_workbench.services.io_map import load_plc_map, save_plc_map
from fanuc_workbench.services.workspace_api import serial


FILES = {"checklist": "commissioning_checklist.json", "plc_map": ".fanuc_workbench_io_map.json",
         "handshake": "plc_handshake.json"}
SIGNAL = re.compile(r"(?:R|PR|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|DIN|DOUT|GIN|GOUT|AIN|AOUT|FLG|F)\[[1-9][0-9]*(?:,[1-9][0-9]*)?\]")


def _text(value, maximum=256):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("Metadata requires bounded text without control characters")
    return value


class WorkspaceMetadataService:
    """Local metadata shared with the existing Workbench GUI; no robot writes."""

    @staticmethod
    def read(root: Path, kind: str) -> dict:
        if kind not in FILES:
            raise ValueError("Metadata kind must be checklist, plc_map or handshake")
        path = root / FILES[kind]
        if path.exists():
            json.loads(path.read_text(encoding="utf-8"))  # Do not hide malformed saved metadata.
        values = {"checklist": lambda: load_checklist(path), "plc_map": lambda: load_plc_map(root),
                  "handshake": lambda: load_handshake(path)}[kind]()
        return {"kind": kind, "present": path.exists(), "data": serial(values),
                "controller_written": False, "acceptance": "user-authored evidence only"}

    @staticmethod
    def write(root: Path, kind: str, data):
        if kind not in FILES:
            raise ValueError("Metadata kind must be checklist, plc_map or handshake")
        path = root / FILES[kind]
        if kind == "plc_map":
            if not isinstance(data, dict) or len(data) > 2000:
                raise ValueError("PLC map must contain at most 2000 signal-to-tag entries")
            for key, value in data.items():
                if not isinstance(key, str) or not SIGNAL.fullmatch(key):
                    raise ValueError("PLC map keys must be explicit FANUC signal identifiers")
                _text(value)
            save_plc_map(root, data)
        elif kind == "checklist":
            if not isinstance(data, list) or len(data) > 100:
                raise ValueError("Checklist must contain at most 100 items")
            rows = []
            names = set()
            for item in data:
                if not isinstance(item, dict) or set(item) - {"name", "done", "note"}:
                    raise ValueError("Checklist fields are name, done and note")
                name = _text(item["name"])
                if not name.strip() or name in names or type(item.get("done", False)) is not bool:
                    raise ValueError("Checklist names must be unique and done must be a boolean")
                names.add(name)
                rows.append(ChecklistItem(name, item.get("done", False), _text(item.get("note", ""), 1000)))
            save_checklist(path, rows)
        else:
            if not isinstance(data, list) or len(data) > 100:
                raise ValueError("Handshake map must contain at most 100 rows")
            rows = []
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError("Handshake rows must be objects")
                row = HandshakeSignal(**item)
                for value in serial(row).values():
                    _text(value)
                if not SIGNAL.fullmatch(row.robot_signal) or (row.expected_peer and not SIGNAL.fullmatch(row.expected_peer)):
                    raise ValueError("Handshake signals must use explicit FANUC watch expressions")
                rows.append(row)
            save_handshake(path, rows)
        return WorkspaceMetadataService.read(root, kind)
