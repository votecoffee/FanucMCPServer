from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.backends.factory import create_backend
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.backup import BackupService
from fanuc_workbench.services.compare import text_diff
from fanuc_workbench.services.restore import RestoreCandidate, restore_selected
from fanuc_workbench.services.transfer import download_bounded


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ControllerService:
    """UI-independent controller operations for the GUI, plugins and MCP.

    Callers own user interaction and access policy. Every operation owns its
    connection; do not share a live FTP/SFTP session between worker threads.
    """

    def __init__(self, profile: RobotProfile, backend_factory: Callable = create_backend):
        self.profile = profile
        self.backend_factory = backend_factory

    @contextmanager
    def session(self):
        backend: RobotBackend = self.backend_factory(self.profile)
        try:
            backend.connect()
            yield backend
        finally:
            backend.close()

    def test_connection(self) -> dict[str, int]:
        with self.session() as backend:
            return {root: len(backend.list_dir(root)) for root in self.profile.roots}

    def list_files(self, remote_dir: str):
        with self.session() as backend:
            return backend.list_dir(remote_dir)

    def download_file(self, remote: str, local: Path, *, maximum: int | None = None) -> Path:
        with self.session() as backend:
            if maximum is None:
                backend.download(remote, local)
            else:
                download_bounded(backend, remote, local, maximum)
        return local

    def upload_file(self, local: Path, remote: str) -> str:
        """Legacy raw upload; caller must obtain its existing write confirmation.

        Chat adapters must use replace_file_verified with explicit access policy.
        """
        with self.session() as backend:
            backend.upload(local, remote)
        return remote

    def backup(self, destination: Path, **options) -> Path:
        return BackupService(self.backend_factory(self.profile), self.profile).run(destination, **options)

    def restore(self, rows: list[RestoreCandidate], progress=None) -> list[str]:
        return restore_selected(self.backend_factory(self.profile), rows, progress)

    def compare_file(self, local: Path, remote: str) -> str:
        with tempfile.TemporaryDirectory(prefix="fanuc_live_") as folder:
            live = Path(folder) / local.name
            self.download_file(remote, live)
            return text_diff(local, live)

    def preview_restore(self, rows: list[RestoreCandidate]) -> str:
        out = []
        with tempfile.TemporaryDirectory(prefix="fanuc_restore_preview_") as folder, self.session() as backend:
            for index, row in enumerate(rows):
                live = Path(folder) / f"{index}_{row.path.name}"
                try:
                    backend.download(row.remote_path, live)
                    if row.path.suffix.upper() in {".LS", ".VA", ".DG", ".TXT", ".CSV"}:
                        detail = text_diff(live, row.path, max_lines=80) or "No text/semantic difference."
                    elif live.read_bytes() == row.path.read_bytes():
                        detail = "Binary files identical."
                    else:
                        detail = f"Binary files differ: live {live.stat().st_size} bytes -> backup {row.path.stat().st_size} bytes."
                except Exception as exc:
                    detail = f"Could not retrieve live file: {exc}"
                out.append(f"=== {row.remote_path} ===\n{detail}")
        return "\n\n".join(out)

    def replace_file_verified(self, local: Path, remote: str, expected_sha256: str,
                              backup: Path, readback: Path, maximum: int,
                              before_upload: Callable[[], None]) -> dict:
        """Preserve existing bytes, reject stale bases, upload and check readback.

        FTP/SFTP provide no atomic compare-and-swap. Another client can race the
        preflight. A failed upload/readback leaves controller state uncertain;
        callers must inspect it and must not blindly retry or auto-restore.
        """
        proposed_sha256 = sha256(local)
        with self.session() as backend:
            download_bounded(backend, remote, backup, maximum)
            if sha256(backup) != expected_sha256:
                raise ValueError("Controller file changed since review; prepare a new plan")
            # Windows FlushFileBuffers requires a writable handle.
            with backup.open("r+b") as stream:
                os.fsync(stream.fileno())
            before_upload()
            backend.upload(local, remote)
            download_bounded(backend, remote, readback, maximum)
            actual = sha256(readback)
            if actual != proposed_sha256:
                raise RuntimeError("Uploaded file readback differs; inspect the controller before further writes")
        return {"status": "verified", "remote_path": remote, "sha256": actual,
                "backup_path": str(backup), "readback_path": str(readback)}
