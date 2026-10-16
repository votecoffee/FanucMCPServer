from __future__ import annotations

import base64
import binascii
import difflib
import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fanuc_workbench.backends.factory import create_backend
from fanuc_workbench.bridge.config import BridgeConfig, remote_path, under_root
from fanuc_workbench.services.controller import ControllerService
from fanuc_workbench.services.diagnostics import scan_alarm_file
from fanuc_workbench.bridge.storage import RobotLocks, atomic_json


_TEXT = {".LS", ".KL", ".VA", ".DG", ".TXT", ".CSV", ".LOG", ".INI", ".XML", ".JSON"}
_UPLOAD = {".LS", ".KL", ".TP", ".PC", ".TXT", ".CSV"}
_HASH = re.compile(r"[a-f0-9]{64}")
_STAGE_LIMIT = 1048576


@dataclass(frozen=True)
class UploadPlan:
    robot_id: str
    remote: str
    original_sha256: str | None
    proposed_sha256: str
    payload: bytes
    expires: float
    folder: Path


class RobotBridge:
    """Configured access policy and audit for chat-facing controller tools.

    The local operator owns this configuration and the process environment.
    Tool arguments cannot select arbitrary hosts, credentials or write targets.
    This is not a security boundary against a client with local shell access.
    """

    def __init__(self, config: BridgeConfig, backend_factory=create_backend, *, config_path: Path | None = None):
        self.config = config
        self.config_path = config_path.resolve() if config_path is not None else None
        self.backend_factory = backend_factory
        self._plans: dict[str, UploadPlan] = {}
        self._lock = threading.RLock()
        self._audit_lock = threading.RLock()
        self._connection_lock = threading.RLock()
        self._active_robot_operations = 0
        config.state_dir.mkdir(parents=True, exist_ok=True)
        self.robot_locks = RobotLocks(config.state_dir / "robot-locks")
        from fanuc_workbench.bridge.operations import WorkbenchOperations
        self.operations = WorkbenchOperations(self)
        from fanuc_workbench.bridge.comments import CommentChanges
        self.comments = CommentChanges(self)
        from fanuc_workbench.bridge.extension_host import ExtensionHost
        self.extensions = ExtensionHost(self)

    @classmethod
    def from_config(cls, path: Path, backend_factory=create_backend):
        path = path.resolve()
        return cls(BridgeConfig.load(path), backend_factory, config_path=path)

    def close(self):
        self.operations.jobs.close()
        self.extensions.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

    def cancel_change(self, plan_id: str) -> dict:
        with self._lock:
            if plan_id in self.comments.plans:
                self.comments.plans.pop(plan_id)
                self._audit("comment_cancelled", plan_id=plan_id)
                return {"plan_id": plan_id, "status": "cancelled"}
            return self.cancel_upload(plan_id)

    def change_review(self, plan_id: str, approved_sha256: str) -> dict:
        with self._lock:
            if plan_id in self.comments.plans:
                return self.comments.review(plan_id, approved_sha256)
            return self.upload_review(plan_id, approved_sha256)

    def commit_change(self, plan_id: str, approved_sha256: str, *, client_approved=False) -> dict:
        with self._lock:
            if plan_id in self.comments.plans:
                return self.comments.commit(plan_id, approved_sha256, client_approved=client_approved)
            return self.commit_upload(plan_id, approved_sha256, client_approved=client_approved)

    @contextmanager
    def _robot_lock(self, robot_id: str):
        # Pin this operation's endpoint until its final controller access.
        with self._connection_lock:
            host = self._access(robot_id).profile.host.casefold()
            self._active_robot_operations += 1
        try:
            with self.robot_locks.acquire(host):
                yield
        finally:
            with self._connection_lock:
                self._active_robot_operations -= 1

    @staticmethod
    def _connection_policy(config: BridgeConfig):
        policy = asdict(config)
        for access in policy["robots"].values():
            for key in ("host", "port", "timeout"):
                access["profile"].pop(key)
            # Preserve whether HTTP writes are enabled and prohibit scheme changes.
            origin = access["comment_service_url"]
            access["comment_service_url"] = urlsplit(origin).scheme if origin else None
        return policy

    def reload_connections(self) -> dict:
        """Apply endpoint-only changes from the bound local file, never tool-supplied hosts."""
        if self.config_path is None:
            raise ValueError("No configuration file is bound; start the bridge with from_config or the MCP --config option")
        with self._lock, self._connection_lock:
            if self._active_robot_operations:
                raise ValueError("A controller operation is active; wait for it to finish before reloading connections")
            with self.operations.jobs.lock:
                if self.operations.jobs.events:
                    raise ValueError("Read jobs are active or queued; finish or cancel them before reloading connections")
                try:
                    candidate = BridgeConfig.load(self.config_path)
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    raise ValueError("The bound configuration could not be validated; active connections were not changed") from None
                if self._connection_policy(candidate) != self._connection_policy(self.config):
                    raise ValueError("Connection reload cannot change robots, credentials, roots, write policy, approval or storage settings; restart for those operator changes")
                changed = [key for key in candidate.robots if candidate.robots[key] != self.config.robots[key]]
                if not changed:
                    return {"status": "unchanged", "changed_robots": [], "invalidated_upload_plans": 0,
                            "invalidated_comment_plans": 0, "controller_contacted": False}
                uploads, comments = len(self._plans), len(self.comments.plans)
                endpoints = [{"robot_id": key, "before": {"host": self.config.robots[key].profile.host,
                              "port": self.config.robots[key].profile.port,
                              "timeout": self.config.robots[key].profile.timeout,
                              "comment_service_url": self.config.robots[key].comment_service_url},
                              "after": {"host": candidate.robots[key].profile.host,
                              "port": candidate.robots[key].profile.port,
                              "timeout": candidate.robots[key].profile.timeout,
                              "comment_service_url": candidate.robots[key].comment_service_url}}
                             for key in changed]
                # Persist the event before applying the in-memory switch. Preserve all evidence.
                self._audit("connections_reloaded", robot_ids=changed,
                            invalidated_upload_plans=uploads, invalidated_comment_plans=comments,
                            connections=endpoints)
                self._plans.clear()
                self.comments.plans.clear()
                self.config = candidate
                self.operations._last_sample.clear()
                return {"status": "reloaded", "changed_robots": changed,
                        "invalidated_upload_plans": uploads, "invalidated_comment_plans": comments,
                        "controller_contacted": False, "requires_fresh_reads": True,
                        "connections": [{"robot_id": key, "host": candidate.robots[key].profile.host,
                                         "port": candidate.robots[key].profile.port,
                                         "comment_service_url": candidate.robots[key].comment_service_url}
                                        for key in changed]}

    def _access(self, robot_id: str):
        try:
            return self.config.robots[robot_id]
        except KeyError:
            raise ValueError("Unknown configured robot ID") from None

    def _controller(self, robot_id: str) -> ControllerService:
        return ControllerService(self._access(robot_id).connection_profile(), self.backend_factory)

    def _path(self, robot_id: str, value: str, *, write: bool = False, create: bool = False) -> str:
        access = self._access(robot_id)
        value = remote_path(value)
        if not any(under_root(value, root) for root in access.profile.roots):
            raise ValueError("Remote path is outside this robot's configured roots")
        if write or create:
            if not self.config.allow_writes:
                raise ValueError("Controller writes are disabled by the local bridge configuration")
            allowed = access.creatable_files if create else access.writable_files
            if value not in allowed:
                raise ValueError("Remote file is not explicitly listed in " + ("creatable_files" if create else "writable_files"))
            name = value.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if create and (value.startswith("md:") or Path(name).suffix.upper() not in {".TXT", ".CSV"}):
                raise ValueError("Creation is limited to TXT/CSV storage files outside MD:")
            if Path(name).suffix.upper() not in _UPLOAD or any(
                word in name.upper() for word in ("DCS", "MASTER", "SYSMAC", "SYSVARS", "SAFETY")
            ):
                raise ValueError("This bridge only replaces application program/text files")
        return value

    def _audit(self, action: str, **fields) -> None:
        record = {"at": datetime.now(timezone.utc).isoformat(), "action": action, **fields}
        with self._audit_lock, (self.config.state_dir / "audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _folder(self) -> Path:
        folder = self.config.state_dir / uuid.uuid4().hex
        folder.mkdir()
        return folder

    @staticmethod
    def _display(path: Path, remote: str) -> dict:
        data = path.read_bytes()
        result = {"remote_path": remote, "local_path": str(path), "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()}
        if Path(remote).suffix.upper() in _TEXT and b"\x00" not in data:
            try:
                text = data.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                text = data.decode("cp1252", errors="replace")
                encoding = "cp1252 (display only)"
            result.update(text=text[:65536], text_truncated=len(text) > 65536, encoding=encoding)
        else:
            result["text"] = None
        return result

    def list_robots(self) -> dict:
        return {"allow_writes": self.config.allow_writes, "robots": [
            {"id": key, "name": access.profile.name, "host": access.profile.host,
             "port": access.profile.port, "timeout": access.profile.timeout,
             "transport": access.profile.transport, "roots": access.profile.roots,
             "writable_files": list(access.writable_files), "comment_targets": list(access.comment_targets),
             "creatable_files": list(access.creatable_files),
             "comment_service_url": access.comment_service_url, "comment_max_bytes": access.comment_max_bytes,
             "comment_encoding": access.comment_encoding}
            for key, access in self.config.robots.items()
        ]}

    def list_files(self, robot_id: str, remote_dir: str, offset: int = 0, limit: int = 1000) -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Directory pagination requires offset >= 0 and limit between 1 and 1000")
        with self._lock, self._robot_lock(robot_id):
            remote = self._path(robot_id, remote_dir)
            self._audit("list_requested", robot_id=robot_id, remote_path=remote)
            try:
                entries = self._controller(robot_id).list_files(remote)
                # Do not trust paths/names supplied by the remote directory listing.
                rows = []
                for entry in entries:
                    try:
                        path = self._path(robot_id, entry.path)
                    except ValueError:
                        continue
                    if not under_root(path, remote) or path == remote:
                        continue
                    rows.append({"path": path, "is_dir": entry.is_dir,
                                 "size": entry.size, "modified": entry.modified})
                selected = rows[offset:offset + limit]
                more = offset + len(selected) < len(rows)
                self._audit("list_complete", robot_id=robot_id, count=len(selected), total=len(rows), offset=offset)
                return {"entries": selected, "limit": limit, "offset": offset, "total": len(rows),
                        "next_offset": offset + len(selected) if more else None, "possibly_truncated": more}
            except Exception as exc:
                self._audit("list_failed", robot_id=robot_id, error_type=type(exc).__name__)
                raise RuntimeError("Controller listing failed; check connection and profile") from None

    def read_file(self, robot_id: str, path: str) -> dict:
        with self._lock, self._robot_lock(robot_id):
            remote = self._path(robot_id, path)
            folder = self._folder()
            name = remote.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            # A fixed local filename prevents remote names becoming local paths.
            local = folder / ("original" + Path(name).suffix.lower())
            self._audit("read_requested", robot_id=robot_id, remote_path=remote)
            try:
                self._controller(robot_id).download_file(remote, local, maximum=self.config.max_file_bytes)
                result = self._display(local, remote)
                self._audit("read_complete", robot_id=robot_id, remote_path=remote,
                            sha256=result["sha256"], local_path=str(local))
                atomic_json(self._read_receipt(robot_id, remote, result["sha256"]),
                            {"sha256": result["sha256"], "local_path": str(local)})
                return result
            except Exception as exc:
                self._audit("read_failed", robot_id=robot_id, remote_path=remote, error_type=type(exc).__name__)
                raise RuntimeError("Controller read failed; check path, connection and max_file_bytes") from None

    def _read_receipt(self, robot_id: str, remote: str, digest: str) -> Path:
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError("Reference SHA-256 must identify a preserved controller read")
        profile = self._access(robot_id).profile
        key = json.dumps([robot_id, profile.host.casefold(), profile.port, profile.transport,
                          remote.casefold() if ":" in remote else remote, digest])
        return self.config.state_dir / "read-receipts" / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def reference_capture(self, robot_id: str, path: str, digest: str) -> dict:
        """Resolve only an intact read captured for this configured robot/path."""
        remote = self._path(robot_id, path)
        try:
            receipt = json.loads(self._read_receipt(robot_id, remote, digest).read_text(encoding="utf-8"))
            local = Path(receipt["local_path"]).resolve()
            if (not local.is_relative_to(self.config.state_dir.resolve()) or not local.is_file()
                    or local.stat().st_size > self.config.max_file_bytes):
                raise ValueError("Reference capture is outside retained evidence or exceeds the file limit")
            capture = self._display(local, remote)
            if capture["sha256"] != digest or receipt["sha256"] != digest:
                raise ValueError("Preserved reference bytes changed")
            return capture
        except (OSError, KeyError, ValueError):
            raise ValueError("No intact preserved read matches this robot, path and SHA-256; retrieve a fresh listing") from None

    def read_captured_file(self, robot_id: str, path: str, sha256: str,
                           offset: int = 0, length: int = 32768) -> dict:
        """Return exact retained bytes without another controller connection."""
        if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 65536:
            raise ValueError("Use byte offset >= 0 and length between 1 and 65536")
        with self._lock:
            capture = self.reference_capture(robot_id, path, sha256)
            # Hash the same bytes returned, including if a trusted local process edits the capture.
            with Path(capture["local_path"]).open("rb") as stream:
                data = stream.read(self.config.max_file_bytes + 1)
            if len(data) > self.config.max_file_bytes or hashlib.sha256(data).hexdigest() != sha256:
                raise ValueError("Preserved reference bytes changed")
            part = data[offset:offset + length]
            return {"robot_id": robot_id, "remote_path": self._path(robot_id, path), "sha256": sha256,
                    "offset": offset, "bytes": len(part), "size": len(data),
                    "base64": base64.b64encode(part).decode("ascii"),
                    "next_offset": offset + len(part) if offset + len(part) < len(data) else None,
                    "controller_contacted": False}

    def stage_file(self, filename: str, data_base64: str, expected_sha256: str) -> dict:
        """Create a unique local proposal from bounded, hash-checked exact bytes."""
        if (not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", filename)
                or filename.endswith(".") or Path(filename).suffix.upper() not in _UPLOAD
                or filename.split(".", 1)[0].upper() in {
                    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}):
            raise ValueError("filename must be a simple application filename (LS/KL/TP/PC/TXT/CSV), not a path or device")
        if not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256):
            raise ValueError("expected_sha256 must be the SHA-256 of the proposed bytes")
        maximum = min(_STAGE_LIMIT, self.config.max_file_bytes)
        if not isinstance(data_base64, str) or not 1 <= len(data_base64) <= 4 * ((maximum + 2) // 3):
            raise ValueError("Staged data must be nonempty and within the staging byte limit")
        try:
            payload = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("data_base64 must contain strict ASCII base64") from None
        if not payload or len(payload) > maximum:
            raise ValueError("Staged data must be nonempty and within the staging byte limit")
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected_sha256:
            raise ValueError("Proposed bytes do not match expected_sha256")
        with self._lock:
            root = self.config.source_dir.resolve()
            staging = root / "staged"
            staging.mkdir(parents=True, exist_ok=True)
            if staging.is_symlink() or staging.resolve() != staging:
                raise ValueError("The staging directory must not redirect outside source_dir")
            folder = staging / uuid.uuid4().hex
            folder.mkdir()
            local = folder / filename
            with local.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            source = local.relative_to(root).as_posix()
            self._audit("file_staged", source_file=source, sha256=digest, bytes=len(payload))
            return {"source_file": source, "filename": filename, "sha256": digest, "bytes": len(payload),
                    "max_staging_bytes": maximum, "controller_written": False, "controller_contacted": False,
                    "status": "staged_only"}

    def collect_logs(self, robot_id: str, paths: list[str]) -> dict:
        if not 1 <= len(paths) <= 10:
            raise ValueError("Supply between one and ten explicit log/diagnostic paths")
        for path in paths:
            if Path(path).suffix.upper() not in _TEXT:
                raise ValueError("Log capture requires text/diagnostic files")
            self._path(robot_id, path)
        files, failures, alarms = [], [], []
        for path in paths:
            try:
                item = self.read_file(robot_id, path)
                files.append(item)
                for alarm in scan_alarm_file(Path(item["local_path"])):
                    if len(alarms) >= 1000:
                        break
                    alarms.append({"remote_path": item["remote_path"], "line": alarm.line_number,
                                   "timestamp": alarm.timestamp, "code": alarm.code, "message": alarm.message})
            except RuntimeError:
                failures.append({"remote_path": path, "status": "unavailable"})
        return {"files": files, "unavailable": failures, "alarms": alarms,
                "alarm_limit": 1000, "capture_is_atomic": False}

    def _source_payload(self, remote: str, source_file: str) -> tuple[bytes, Path]:
        source_arg = Path(source_file)
        source = (self.config.source_dir / source_arg).resolve()
        if (source_arg.is_absolute() or source_arg.drive or ".." in source_arg.parts
                or not source.is_relative_to(self.config.source_dir.resolve())):
            raise ValueError("source_file must be relative to the configured source_dir and remain inside it")
        if source.suffix.upper() != Path(remote).suffix.upper():
            raise ValueError("Source and remote file extensions must match")
        with source.open("rb") as stream:
            payload = stream.read(self.config.max_file_bytes + 1)
        if not payload or len(payload) > self.config.max_file_bytes:
            raise ValueError("Upload file must be nonempty and within max_file_bytes")
        return payload, source

    def _trim_plans(self):
        now = time.monotonic()
        self._plans = {key: plan for key, plan in self._plans.items() if plan.expires > now}
        if len(self._plans) >= 64:
            raise ValueError("Too many pending plans; cancel plans before preparing more")

    def prepare_file_create(self, robot_id: str, path: str, source_file: str) -> dict:
        with self._lock, self._robot_lock(robot_id):
            remote = self._path(robot_id, path, create=True)
            payload, source = self._source_payload(remote, source_file)
            self._trim_plans()
            self._audit("create_prepare_requested", robot_id=robot_id, remote_path=remote)
            try:
                absent = self._controller(robot_id).inspect_file_absence(remote)
            except FileExistsError:
                raise
            except Exception as exc:
                self._audit("create_prepare_failed", robot_id=robot_id, error_type=type(exc).__name__)
                raise RuntimeError("Cannot establish target absence from its parent directory; no create plan prepared") from None
            folder = self._folder()
            atomic_json(folder / "original_absent.json", absent)
            proposed = folder / ("proposed" + source.suffix.lower())
            proposed.write_bytes(payload)
            preview = self._display(proposed, remote)
            proposed_hash = hashlib.sha256(payload).hexdigest()
            plan_id = uuid.uuid4().hex
            self._plans[plan_id] = UploadPlan(robot_id, remote, None, proposed_hash, payload,
                                             time.monotonic() + self.config.plan_ttl_seconds, folder)
            result = {"plan_id": plan_id, "operation": "create", "robot_id": robot_id, "remote_path": remote,
                      "original_sha256": None, "proposed_sha256": proposed_hash, "bytes": len(payload),
                      "absence_evidence_path": str(folder / "original_absent.json"), "proposed_path": str(proposed),
                      "diff": "\n".join(difflib.unified_diff([], (preview["text"] or "").splitlines(),
                                 fromfile="absent", tofile="proposed", lineterm=""))[:32768],
                      "preview_may_be_truncated": True, "atomic_no_clobber": False,
                      "expires_in_seconds": self.config.plan_ttl_seconds, "status": "prepared_only",
                      "requires_user_approval": True}
            atomic_json(folder / "plan.json", result)
            self._audit("create_prepared", plan_id=plan_id, robot_id=robot_id, remote_path=remote,
                        proposed_sha256=proposed_hash)
            return result

    def prepare_upload(self, robot_id: str, path: str, source_file: str, expected_sha256: str) -> dict:
        with self._lock:
            remote = self._path(robot_id, path, write=True)
            if not _HASH.fullmatch(expected_sha256):
                raise ValueError("expected_sha256 must be the SHA-256 returned by read_file")
            payload, source = self._source_payload(remote, source_file)
            self._trim_plans()
            original = self.read_file(robot_id, remote)
            if original["sha256"] != expected_sha256:
                raise ValueError("Controller file changed; retrieve it and review the change again")
            proposed_hash = hashlib.sha256(payload).hexdigest()
            if proposed_hash == expected_sha256:
                raise ValueError("Proposed bytes are identical to the controller file")
            folder = Path(original["local_path"]).parent
            proposed = folder / ("proposed" + source.suffix.lower())
            proposed.write_bytes(payload)
            preview = self._display(proposed, remote)
            diff = None
            if original["text"] is not None and preview["text"] is not None:
                diff = "\n".join(difflib.unified_diff(original["text"].splitlines(), preview["text"].splitlines(),
                                                       fromfile="controller", tofile="proposed", lineterm=""))[:32768]
            plan_id = uuid.uuid4().hex
            self._plans[plan_id] = UploadPlan(robot_id, remote, expected_sha256, proposed_hash,
                                             payload, time.monotonic() + self.config.plan_ttl_seconds, folder)
            result = {"plan_id": plan_id, "robot_id": robot_id, "remote_path": remote,
                      "original_sha256": expected_sha256, "proposed_sha256": proposed_hash,
                      "bytes": len(payload), "backup_path": original["local_path"],
                      "proposed_path": str(proposed), "diff": diff,
                      "preview_may_be_truncated": True, "expires_in_seconds": self.config.plan_ttl_seconds,
                      "status": "prepared_only", "requires_user_approval": True}
            (folder / "plan.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            self._audit("upload_prepared", plan_id=plan_id, robot_id=robot_id,
                        remote_path=remote, original_sha256=expected_sha256, proposed_sha256=proposed_hash)
            return result

    def cancel_upload(self, plan_id: str) -> dict:
        with self._lock:
            if plan_id not in self._plans:
                raise ValueError("Unknown or already consumed plan")
            self._plans.pop(plan_id)
            self._audit("upload_cancelled", plan_id=plan_id)
            return {"status": "cancelled", "plan_id": plan_id}

    def commit_upload(self, plan_id: str, approved_sha256: str, *, client_approved: bool = False) -> dict:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise ValueError("Unknown or already consumed plan; prepare a new plan")
            if self.config.approval_mode == "elicitation" and not client_approved:
                raise ValueError("This bridge requires confirmation through the MCP client's elicitation interface")
            with self._robot_lock(plan.robot_id):
                return self._commit_upload_locked(plan_id, approved_sha256)

    def upload_review(self, plan_id: str, approved_sha256: str) -> dict:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None or plan.expires <= time.monotonic():
                raise ValueError("Unknown or expired plan")
            if approved_sha256 != plan.proposed_sha256:
                raise ValueError("Approval SHA-256 does not match the prepared bytes")
            return {"robot_id": plan.robot_id, "host": self._access(plan.robot_id).profile.host,
                    "remote_path": plan.remote, "sha256": plan.proposed_sha256, "bytes": len(plan.payload),
                    "operation": "create" if plan.original_sha256 is None else "replace"}

    def _commit_upload_locked(self, plan_id: str, approved_sha256: str) -> dict:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise ValueError("Unknown or already consumed plan; prepare a new plan")
            self._path(plan.robot_id, plan.remote, write=True, create=plan.original_sha256 is None)
            if plan.expires <= time.monotonic():
                self._plans.pop(plan_id)
                raise ValueError("Plan expired; prepare a new plan")
            if approved_sha256 != plan.proposed_sha256:
                raise ValueError("Approval SHA-256 does not match the prepared bytes")
            # One use, including failures. Restart also discards all pending plans.
            self._plans.pop(plan_id)
            upload = plan.folder / "upload.bin"
            with upload.open("xb") as stream:
                stream.write(plan.payload)
                stream.flush()
                os.fsync(stream.fileno())
            started = False

            def before_upload():
                nonlocal started
                self._audit("upload_started", plan_id=plan_id, robot_id=plan.robot_id,
                            remote_path=plan.remote, sha256=plan.proposed_sha256,
                            operation="create" if plan.original_sha256 is None else "replace")
                started = True

            try:
                controller = self._controller(plan.robot_id)
                if plan.original_sha256 is None:
                    result = controller.create_file_verified(upload, plan.remote, plan.folder / "preflight_absent.json",
                        plan.folder / "readback.bin", self.config.max_file_bytes, before_upload)
                else:
                    result = controller.replace_file_verified(
                        upload, plan.remote, plan.original_sha256, plan.folder / "preflight.bin",
                        plan.folder / "readback.bin", self.config.max_file_bytes, before_upload)
                self._audit("upload_verified", plan_id=plan_id, **result)
                return {**result, "plan_id": plan_id, "program_loaded_or_running": "not_verified"}
            except Exception as exc:
                state = "uncertain" if started else "not_attempted"
                self._audit("upload_failed", plan_id=plan_id, controller_state=state,
                            error_type=type(exc).__name__)
                raise RuntimeError(f"Upload failed; controller write state: {state}. "
                                   "Inspect current bytes before preparing another plan. "
                                   "The plan is consumed; no automatic retry or rollback occurred.") from None
