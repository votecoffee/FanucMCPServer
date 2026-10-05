from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .models import RobotProfile


class SettingsStore:
    def __init__(self) -> None:
        base = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "profiles.json"

    def load_profiles(self) -> list[RobotProfile]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return [RobotProfile.from_dict(item) for item in payload.get("profiles", [])]
        except Exception:
            return []

    def save_profiles(self, profiles: list[RobotProfile]) -> None:
        payload = {"profiles": [p.to_dict() for p in profiles]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
