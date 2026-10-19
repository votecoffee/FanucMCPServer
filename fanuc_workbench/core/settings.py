from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .models import RobotProfile


class SettingsStore:
    def __init__(self) -> None:
        base = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "profiles.json"
        self.preferences_path = base / "desktop.json"
        self.legacy_path = base.parent / "FANUC Workbench" / "profiles.json"

    def load_profiles(self) -> list[RobotProfile]:
        source = self.path if self.path.exists() else self.legacy_path
        if not source.exists():
            return []
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            return [RobotProfile.from_dict(item) for item in payload.get("profiles", [])]
        except Exception:
            return []

    def save_profiles(self, profiles: list[RobotProfile]) -> None:
        payload = {"profiles": [p.to_dict() for p in profiles]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def preferences(self) -> dict:
        try:
            data = json.loads(self.preferences_path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def update_preferences(self, **changes) -> None:
        data = self.preferences()
        if all(data.get(key) == value for key, value in changes.items()):
            return
        data.update(changes)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.desktop-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, indent=2, allow_nan=False)
            os.replace(temporary, self.preferences_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def profile_key(profile: RobotProfile) -> dict:
        return {key: getattr(profile, key) for key in ('name', 'host', 'port', 'transport', 'username')}

    def remember_profile(self, profile: RobotProfile, *, connected=False) -> None:
        key = self.profile_key(profile)
        self.update_preferences(**({'last_connected_robot': key} if connected else {'last_selected_robot': key}))

    def preferred_profile(self, profiles: list[RobotProfile]) -> RobotProfile | None:
        saved = self.preferences()
        for key in ('last_connected_robot', 'last_selected_robot'):
            found = next((profile for profile in profiles if self.profile_key(profile) == saved.get(key)), None)
            if found is not None:
                return found
        return profiles[0] if profiles else None
