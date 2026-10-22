"""Bounded process-local worker inventory, not a crash-recovery ledger.

Only private launches belong here. Attached user applications and shared native
software are never inferred from a PID or admitted for generic termination.
"""
from collections import deque
import threading
import uuid


class WorkerInventory:
    def __init__(self, limit=16):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("Invalid private worker limit")
        self.limit = limit
        self.lock = threading.Lock()
        self.owners = {}
        self.records = {}
        self.history = deque(maxlen=64)

    def reserve(self, owner, kind):
        if kind not in {"feature-worker", "native-worker"}:
            raise ValueError("Unknown private worker kind")
        with self.lock:
            if len(self.owners) >= self.limit:
                raise RuntimeError("Private worker capacity reached; inspect retained ownership before launching more")
            identifier = uuid.uuid4().hex
            self.owners[identifier] = owner
            self.records[identifier] = {"resource_id": identifier, "kind": kind,
                "ownership": "exclusive-app-launch", "state": "starting", "pid": None, "creation_time": None}
            return identifier

    def update(self, identifier, state, *, pid=None, creation_time=None):
        if state not in {"starting", "running", "stopping", "uncertain"}:
            raise ValueError("Invalid private worker state")
        with self.lock:
            record = self.records[identifier]
            record["state"] = state
            if pid is not None:
                record.update(pid=pid, creation_time=creation_time)

    def release(self, identifier):
        with self.lock:
            record = self.records.pop(identifier)
            self.owners.pop(identifier)
            self.history.append({**record, "state": "closed"})

    def snapshot(self):
        # Cached identity and bookkeeping only. No process/IPC/native probes.
        with self.lock:
            return {"limit": self.limit, "active": len(self.records),
                    "uncertain": sum(row["state"] == "uncertain" for row in self.records.values()),
                    "resources": [dict(row) for row in self.records.values()],
                    "durable_recovery": "pending shared ownership-ledger integration"}


workers = WorkerInventory()
