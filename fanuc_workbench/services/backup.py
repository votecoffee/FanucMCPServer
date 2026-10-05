from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.core.models import BackupManifest, BackupRecord, RobotProfile


_INVALID = re.compile(r"[<>:\\|?*\x00-\x1f]")


def safe_component(value: str) -> str:
    value = _INVALID.sub("_", value).strip().strip(".")
    return value or "root"


def remote_to_relative(root: str, remote_path: str) -> Path:
    if remote_path == root:
        return Path(safe_component(root))
    suffix = remote_path[len(root):].lstrip("/") if remote_path.startswith(root) else remote_path
    parts = [safe_component(root)] + [safe_component(p) for p in suffix.split("/") if p]
    return Path(*parts)


ASCII_COMPANIONS = {
    ".TP": ".LS",
    ".VR": ".VA",
}


class BackupService:
    def __init__(self, backend: RobotBackend, profile: RobotProfile) -> None:
        self.backend = backend
        self.profile = profile

    def run(
        self,
        destination: Path,
        *,
        roots: list[str] | None = None,
        attempt_ascii: bool = True,
        max_depth: int = 8,
        progress: Callable[[object], None] | None = None,
    ) -> Path:
        progress = progress or (lambda _: None)
        roots = roots or self.profile.roots
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = destination / f"{safe_component(self.profile.name)}_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        manifest = BackupManifest(
            robot_name=self.profile.name,
            host=self.profile.host,
            created_at=datetime.now().isoformat(timespec="seconds"),
            roots=roots,
        )

        with self.backend:
            for root in roots:
                self._walk(root, root, backup_dir, manifest, 0, max_depth, attempt_ascii, progress)

        manifest_path = backup_dir / "backup_manifest.json"
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        return backup_dir

    def _walk(
        self,
        root: str,
        remote_dir: str,
        backup_dir: Path,
        manifest: BackupManifest,
        depth: int,
        max_depth: int,
        attempt_ascii: bool,
        progress: Callable[[object], None],
    ) -> None:
        if depth > max_depth:
            manifest.warnings.append(f"Maximum recursion depth reached at {remote_dir}")
            return
        try:
            entries = self.backend.list_dir(remote_dir)
        except Exception as exc:
            manifest.warnings.append(f"Could not list {remote_dir}: {exc}")
            return

        for entry in entries:
            if entry.is_dir:
                self._walk(root, entry.path, backup_dir, manifest, depth + 1, max_depth, attempt_ascii, progress)
                continue
            rel = remote_to_relative(root, entry.path)
            local = backup_dir / rel
            progress({"message": f"Downloading {entry.path}", "remote": entry.path})
            try:
                self.backend.download(entry.path, local)
                manifest.records.append(BackupRecord(entry.path, str(rel), entry.size))
            except Exception as exc:
                manifest.records.append(BackupRecord(entry.path, str(rel), entry.size, error=str(exc)))
                continue

            if attempt_ascii:
                self._try_ascii_companion(root, entry.path, backup_dir, manifest, progress)

    def _try_ascii_companion(
        self,
        root: str,
        remote_path: str,
        backup_dir: Path,
        manifest: BackupManifest,
        progress: Callable[[object], None],
    ) -> None:
        suffix = Path(remote_path).suffix.upper()
        companion_ext = ASCII_COMPANIONS.get(suffix)
        if not companion_ext:
            return
        companion = str(Path(remote_path).with_suffix(companion_ext)).replace("\\", "/")
        # Preserve FANUC device notation when pathlib has normalized it oddly.
        if ":" in remote_path and ":/" in companion and ":/" not in remote_path:
            companion = companion.replace(":/", ":", 1)
        rel = remote_to_relative(root, companion)
        local = backup_dir / rel
        progress({"message": f"Attempting ASCII companion {companion}", "remote": companion})
        try:
            self.backend.download(companion, local)
            manifest.records.append(
                BackupRecord(companion, str(rel), local.stat().st_size, kind="ascii", ascii_companion_of=remote_path)
            )
        except Exception as exc:
            manifest.warnings.append(f"ASCII companion unavailable for {remote_path}: {exc}")
