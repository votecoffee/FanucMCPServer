"""Local operator settings; deliberately not exposed as a chat tool."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.storage import atomic_json


@dataclass(frozen=True)
class AccessSettings:
    path: Path
    sha256: str
    read_only: bool


def load_access_settings(path: Path) -> AccessSettings:
    path = path.resolve(strict=True)
    original = path.read_bytes()
    config = BridgeConfig.load(path)
    if path.read_bytes() != original:
        raise ValueError("The policy file changed while loading; load it again")
    return AccessSettings(path, hashlib.sha256(original).hexdigest(), config.read_only)


def save_access_settings(settings: AccessSettings, *, read_only: bool) -> AccessSettings:
    """Change only allow_writes, refusing a stale settings view before replacement.

    Other processes retain their loaded policy until reconnected. This is an
    operator-managed local file, not a cross-process atomic compare-and-swap.
    """
    if type(read_only) is not bool:
        raise ValueError("Read-only access must be a boolean")
    original = settings.path.read_bytes()
    if hashlib.sha256(original).hexdigest() != settings.sha256:
        raise ValueError("The policy file changed since loading; load it again before saving")
    payload = json.loads(original.decode("utf-8"))
    if payload.get("allow_writes", False) == (not read_only):
        return settings
    payload["allow_writes"] = not read_only
    atomic_json(settings.path, payload)
    return load_access_settings(settings.path)
