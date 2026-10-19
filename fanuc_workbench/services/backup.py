from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.core.models import BackupManifest, BackupRecord, RobotProfile
from fanuc_workbench.services.transfer import TransferLimitError, download_bounded


class BackupLimitError(RuntimeError):
    pass


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
        maximum_file_bytes: int | None = None,
        maximum_total_bytes: int | None = None,
        maximum_files: int | None = None,
        checkpoint: Callable[[], None] | None = None,
        validate_path: Callable[[str], str] | None = None,
        redact_errors: bool = False,
        selected_paths: list[str] | None = None,
    ) -> Path:
        progress = progress or (lambda _: None)
        roots = roots or self.profile.roots
        self.maximum_file_bytes = maximum_file_bytes
        self.remaining_bytes = maximum_total_bytes
        self.maximum_files = maximum_files
        self.checkpoint = checkpoint or (lambda: None)
        self.validate_path = validate_path or (lambda p: p)
        self.redact_errors = redact_errors
        self.attempts = 0
        self.listed_entries = 0
        self.seen: set[str] = set()
        self.pending, self.queued = [], set()
        self.completed = 0
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = destination / f"{safe_component(self.profile.name)}_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        manifest = BackupManifest(
            robot_name=self.profile.name,
            host=self.profile.host,
            created_at=datetime.now().isoformat(timespec="seconds"),
            roots=roots,
        )

        try:
            with self.backend:
                if selected_paths is not None:
                    for path in selected_paths:
                        path = self.validate_path(path)
                        root = next(r for r in roots if path == r or path.startswith(r if r.endswith(':') else r.rstrip('/') + '/'))
                        self._queue(root, path)
                else:
                    for root in roots:
                        self._walk(root, root, backup_dir, manifest, 0, max_depth, attempt_ascii, progress)
                def transfer_progress(event):
                    progress({**event, **self._counts(manifest), 'phase': 'downloading'})
                transfer_progress({'message': f'Discovered {len(self.pending)} files'})
                for root, path, size in self.pending:
                    self._file(root, path, backup_dir, manifest, attempt_ascii, transfer_progress, size)
                    self.completed += 1
                    transfer_progress({'message': f'Processed {self.completed}/{len(self.pending)} listed files'})
        except BaseException as exc:
            manifest.warnings.append(f"Capture interrupted: {type(exc).__name__}")
            progress({'message': 'Backup interrupted', 'phase': 'failed', **self._counts(manifest)})
            raise
        finally:
            manifest_path = backup_dir / "backup_manifest.json"
            manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        progress({'message': 'Backup finished; manifest saved', 'phase': 'complete', **self._counts(manifest), 'percent': 100})
        return backup_dir

    def _counts(self, manifest):
        return {'completed_files': self.completed, 'total_files': len(self.pending),
            'percent': min(99, self.completed * 100 // len(self.pending)) if self.pending else 0,
            'failed_files': sum(record.error is not None for record in manifest.records),
            'warning_count': len(manifest.warnings)}

    def _queue(self, root, path, size=None):
        if path not in self.queued:
            self.queued.add(path)
            self.pending.append((root, path, size))

    def _download(self, remote: str, local: Path):
        self.checkpoint()
        if self.maximum_files is not None and self.attempts >= self.maximum_files:
            raise BackupLimitError("Backup file-count limit reached")
        self.attempts += 1
        if self.maximum_file_bytes is None:
            self.backend.download(remote, local)
        else:
            maximum = min(self.maximum_file_bytes, self.remaining_bytes) if self.remaining_bytes is not None else self.maximum_file_bytes
            if maximum <= 0:
                raise BackupLimitError("Backup byte budget exhausted")
            download_bounded(self.backend, remote, local, maximum)
        if self.remaining_bytes is not None:
            self.remaining_bytes -= local.stat().st_size

    def _file(self, root, remote, backup_dir, manifest, attempt_ascii, progress, size=None):
        self.checkpoint()
        remote = self.validate_path(remote)
        if remote in self.seen:
            return
        self.seen.add(remote)
        rel = remote_to_relative(root, remote)
        local = backup_dir / rel
        progress({"message": f"Downloading {remote}", "remote": remote})
        try:
            self._download(remote, local)
            manifest.records.append(BackupRecord(remote, str(rel), local.stat().st_size))
        except (TransferLimitError, BackupLimitError):
            raise
        except Exception as exc:
            manifest.records.append(BackupRecord(remote, str(rel), size, error=type(exc).__name__ if self.redact_errors else str(exc)))
            return
        if attempt_ascii:
            self._try_ascii_companion(root, remote, backup_dir, manifest, progress)

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
        self.checkpoint()
        remote_dir = self.validate_path(remote_dir)
        if depth > max_depth:
            manifest.warnings.append(f"Maximum recursion depth reached at {remote_dir}")
            return
        try:
            progress({'message': f'Listing {remote_dir}', 'phase': 'discovering', 'percent': None})
            entries = self.backend.list_dir(remote_dir)
        except Exception as exc:
            manifest.warnings.append(f"Could not list {remote_dir}: {type(exc).__name__ if self.redact_errors else exc}")
            return

        self.listed_entries += len(entries)
        if self.maximum_files is not None and self.listed_entries > self.maximum_files * 2:
            raise BackupLimitError("Directory listing exceeds capture limit")

        for entry in entries:
            self.checkpoint()
            path = self.validate_path(entry.path)
            prefix = remote_dir if remote_dir.endswith(":") else remote_dir.rstrip("/") + "/"
            if path == remote_dir or not path.startswith(prefix):
                raise ValueError("Directory listing contains a path outside its parent")
            if entry.is_dir:
                self._walk(root, path, backup_dir, manifest, depth + 1, max_depth, attempt_ascii, progress)
                continue
            self._queue(root, path, entry.size)

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
        companion = self.validate_path(companion)
        if companion in self.seen:
            return
        self.seen.add(companion)
        rel = remote_to_relative(root, companion)
        local = backup_dir / rel
        progress({"message": f"Attempting ASCII companion {companion}", "remote": companion})
        try:
            self._download(companion, local)
            manifest.records.append(
                BackupRecord(companion, str(rel), local.stat().st_size, kind="ascii", ascii_companion_of=remote_path)
            )
        except (TransferLimitError, BackupLimitError):
            raise
        except Exception as exc:
            manifest.warnings.append(f"ASCII companion unavailable for {remote_path}: {type(exc).__name__ if self.redact_errors else exc}")
