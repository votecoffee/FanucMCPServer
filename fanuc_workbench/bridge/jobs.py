from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fanuc_workbench.bridge.storage import ArtifactStore, RequestCancelled, RobotLocks, atomic_json, utc_now


class JobCancelled(RuntimeError):
    pass


class JobContext:
    def __init__(self, jobs: "JobManager", identifier: str, cancel: threading.Event):
        self.jobs, self.identifier, self.cancel = jobs, identifier, cancel

    def check(self):
        if self.cancel.is_set() or self.jobs.status(self.identifier)["status"] == "cancel_requested":
            raise JobCancelled("Cancelled between controller operations")

    def progress(self, message):
        self.check()
        self.jobs.update(self.identifier, progress=str(message)[:500])


class JobManager:
    """Durable read-job history; cooperative cancellation, never write retries."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fanuc-read")
        self.events: dict[str, threading.Event] = {}
        self.leases = RobotLocks(directory / "leases")
        self.instance = uuid.uuid4().hex
        self.owner_lease = None
        self.closed = False

    def _path(self, identifier: str) -> Path:
        return self.directory / (ArtifactStore.check_id(identifier) + ".json")

    def update(self, identifier: str, **changes):
        with self.lock, self.leases.acquire("metadata-" + identifier, timeout=15):
            path = self._path(identifier)
            item = json.loads(path.read_text(encoding="utf-8"))
            if changes.get("status") == "cancel_requested" and item["status"] not in {"queued", "running", "cancel_requested"}:
                return item
            if item["status"] == "cancel_requested" and changes.get("status") in {"running", "complete"}:
                changes["status"] = "cancelled" if changes["status"] == "complete" else "cancel_requested"
            item.update(changes, updated_at=utc_now())
            atomic_json(path, item)
            return item

    def status(self, identifier: str) -> dict:
        with self.lock:
            try:
                item = json.loads(self._path(identifier).read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise ValueError("Unknown job ID") from None
            if item["status"] in {"running", "queued", "cancel_requested"} and identifier not in self.events:
                try:
                    with self.leases.acquire(item.get("owner", identifier)):
                        item = self.update(identifier, status="interrupted", progress="Worker stopped; inspect partial artifacts")
                except RuntimeError:
                    pass
            return item

    def start(self, kind: str, robot_id: str, work) -> dict:
        with self.lock:
            if self.closed:
                raise ValueError("Job manager has closed")
            if len(self.events) >= 16:
                raise ValueError("Read-job queue is full")
            if self.owner_lease is None:
                self.owner_lease = self.leases.acquire(self.instance)
                self.owner_lease.__enter__()
            identifier = uuid.uuid4().hex
            event = threading.Event()
            self.events[identifier] = event
            item = {"job_id": identifier, "kind": kind, "robot_id": robot_id, "status": "queued",
                    "created_at": utc_now(), "updated_at": utc_now(), "owner": self.instance}
            atomic_json(self._path(identifier), item)

            def run():
                with self.leases.acquire(identifier):
                    try:
                        context = JobContext(self, identifier, event)
                        context.check()
                        self.update(identifier, status="running")
                        result = work(context)
                        context.check()
                        self.update(identifier, status="complete", result=result)
                    except (JobCancelled, RequestCancelled):
                        self.update(identifier, status="cancelled")
                    except Exception as exc:
                        self.update(identifier, status="failed", error_type=type(exc).__name__,
                                    progress="Read job failed; inspect audit and partial artifacts")
                    finally:
                        with self.lock:
                            self.events.pop(identifier, None)

            self.pool.submit(run)
            return item

    def cancel(self, identifier: str) -> dict:
        with self.lock:
            item = self.status(identifier)
            event = self.events.get(identifier)
            if event is None:
                return self.update(identifier, status="cancel_requested") if item["status"] in {"running", "queued"} else item
            event.set()
            return self.update(identifier, status="cancel_requested")

    def list(self, limit: int = 100) -> dict:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        paths = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return {"jobs": [self.status(path.stem) for path in paths[:limit]], "total": len(paths)}

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for event in self.events.values():
                event.set()
        self.pool.shutdown(wait=True, cancel_futures=False)
        if self.owner_lease is not None:
            self.owner_lease.__exit__(None, None, None)
