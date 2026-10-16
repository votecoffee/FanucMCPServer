from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def confined(root: Path, relative: str) -> Path:
    argument = Path(relative)
    result = (root / argument).resolve()
    if argument.is_absolute() or argument.drive or ".." in argument.parts or not result.is_relative_to(root.resolve()):
        raise ValueError("Path must be relative and remain inside its configured directory")
    return result


class RobotBusyError(RuntimeError):
    pass


class RobotLocks:
    """Reentrant per-thread and cross-process advisory locks for bridge clients."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.local = threading.local()

    @contextmanager
    def acquire(self, identity: str):
        owned = getattr(self.local, "owned", None)
        if owned is None:
            owned = self.local.owned = set()
        key = hashlib.sha256(identity.encode()).hexdigest()
        if key in owned:
            yield
            return
        stream = (self.directory / (key + ".lock")).open("a+b")
        try:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RobotBusyError("Robot is busy in another bridge operation; inspect running jobs") from None
            owned.add(key)
            try:
                yield
            finally:
                owned.remove(key)
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


class ArtifactStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir.resolve()
        self.catalog = self.state_dir / "catalog"
        self.catalog.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def check_id(identifier: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid artifact or job ID")
        return identifier

    def register(self, root: Path, kind: str, *, robot_id: str | None = None, note: str = "") -> dict:
        root = root.resolve()
        if not root.is_relative_to(self.state_dir):
            raise ValueError("Artifacts must remain inside state_dir")
        identifier = uuid.uuid4().hex
        files = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Artifact cannot contain symlinks or external paths")
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": digest})
        item = {"artifact_id": identifier, "kind": kind, "robot_id": robot_id, "created_at": utc_now(),
                "root": str(root), "note": note[:1000], "files": files}
        atomic_json(self.catalog / (identifier + ".json"), item)
        return item

    def get(self, identifier: str) -> dict:
        self.check_id(identifier)
        try:
            item = json.loads((self.catalog / (identifier + ".json")).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError("Unknown artifact ID") from None
        root = Path(item["root"]).resolve()
        if not root.is_relative_to(self.state_dir):
            raise ValueError("Artifact root is outside state_dir")
        return item

    def root(self, identifier: str) -> Path:
        return Path(self.get(identifier)["root"])

    def list(self, *, offset: int = 0, limit: int = 100) -> dict:
        if not 0 <= offset or not 1 <= limit <= 500:
            raise ValueError("Use offset >= 0 and limit between 1 and 500")
        names = sorted(self.catalog.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        rows = []
        for path in names[offset:offset + limit]:
            item = self.get(path.stem)
            rows.append({key: value for key, value in item.items() if key != "files"} |
                        {"file_count": len(item["files"]), "bytes": sum(x["bytes"] for x in item["files"])})
        return {"items": rows, "total": len(names), "offset": offset}

    def file(self, identifier: str, relative: str) -> Path:
        item = self.get(identifier)
        path = confined(Path(item["root"]), relative)
        rel = path.relative_to(Path(item["root"])).as_posix()
        record = next((row for row in item["files"] if row["path"] == rel), None)
        if record is None:
            raise ValueError("File is not part of this artifact manifest")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != record["sha256"]:
            raise ValueError("Artifact bytes changed after capture; import a fresh copy")
        return path

    def verify(self, identifier: str) -> Path:
        item = self.get(identifier)
        root = Path(item["root"])
        expected = {row["path"] for row in item["files"]}
        actual = set()
        for path in root.rglob("*"):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Workspace contains an external path")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
        if actual != expected:
            raise ValueError("Workspace file set changed after capture")
        for relative in expected:
            self.file(identifier, relative)
        return root

    def read(self, identifier: str, relative: str, offset: int = 0, length: int = 32768) -> dict:
        if offset < 0 or not 1 <= length <= 65536:
            raise ValueError("Use byte offset >= 0 and length between 1 and 65536")
        path = self.file(identifier, relative)
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(length)
        return {"artifact_id": identifier, "path": relative, "offset": offset, "bytes": len(data),
                "size": path.stat().st_size, "base64": base64.b64encode(data).decode("ascii"),
                "next_offset": offset + len(data) if offset + len(data) < path.stat().st_size else None}
