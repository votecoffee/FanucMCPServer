"""Shared durable ownership for resources of this long-lived FANUC process.

No process is adopted, restarted or cleaned up on construction/status reads.
The app closes its own IPC/jobs first and attests only observed process exit.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading
from contextlib import contextmanager


APP_ID = "fanuc-mcp-server"
_lock = threading.RLock()
_registry = None
_context = None


class ResourceContext:
    def __init__(self, ledger):
        self.ledger = ledger
        self.owner = ledger.owner(APP_ID)

    def begin(self, kind):
        labels = {"feature-worker": "FANUC private feature worker", "native-worker": "FANUC private native API worker",
                  "stdio-worker": "FANUC stdio worker", "python-launcher": "FANUC Python launcher"}
        labels.update({"native-tool-worker": "FANUC offline tool worker", "compiler-process": "FANUC offline compiler tool"})
        labels["policy-worker"] = "FANUC local host-policy worker"
        if kind not in labels:
            raise ValueError("Unknown FANUC owned resource kind")
        mode = "report" if kind == "stdio-worker" else "terminate"
        record = self.owner.begin_launch(labels[kind], kind=kind, cleanup_mode=mode)
        return ResourceClaim(self, record.resource_id, kind)

    def close(self):
        self.owner.close()  # Framework gates unresolved barriers/receipts; no replay.


class ResourceClaim:
    def __init__(self, context, identifier, kind):
        self.context, self.resource_id, self.kind = context, identifier, kind
        self.cleanup_mode = "report" if kind == "stdio-worker" else "terminate"
        self.identifiers = [identifier]
        self.processes = {}

    def bind(self, pid, expected_creation_time):
        record = self.context.owner.bind_process(self.resource_id, pid)
        if record.process.created != str(expected_creation_time):
            self.uncertain("Process creation identity changed before app activation")
            raise ValueError("Durable process creation identity differs from the retained handle")
        self.processes[pid] = record.resource_id
        return record

    def bind_bootstrap(self, pid, expected_creation_time):
        if pid in self.processes:
            record = self.context.ledger.get(self.processes[pid])
            if record.process.created != str(expected_creation_time):
                self.uncertain("Bootstrap creation identity changed before app activation")
                raise ValueError("Bootstrap process identity changed")
            return record
        record = self.context.owner.begin_launch("FANUC activated Python worker", kind=self.kind, cleanup_mode=self.cleanup_mode)
        self.identifiers.append(record.resource_id)
        bound = self.context.owner.bind_process(record.resource_id, pid)
        if bound.process.created != str(expected_creation_time):
            self.uncertain("Bootstrap creation identity changed before app activation")
            raise ValueError("Bootstrap process identity changed")
        self.processes[pid] = bound.resource_id
        return bound

    def uncertain(self, reason):
        for identifier in self.identifiers:
            record = self.context.ledger.get(identifier)
            if record.state not in {"released", "terminated", "failed"}:
                self.context.owner.set_recovery_barrier(identifier, reason)

    def begin_native_write(self):
        for identifier in self.identifiers:
            if self.context.ledger.get(identifier).recovery_barrier:
                raise ValueError("Prior native operation needs app-owned inspection before another write")
        self.uncertain("Native register write may be in progress or unverified; inspect domain receipts before cleanup")

    def verified_native_write(self, sha256):
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("Native readback needs its exact value SHA-256")
        for identifier in self.identifiers:
            record = self.context.ledger.get(identifier)
            self.context.owner.resolve_recovery_barrier(identifier, record.revision,
                "Native worker verified register readback; value SHA-256 " + sha256)

    def exited(self, evidence="App-owned IPC/job shutdown completed; independently verified recorded process exit"):
        for identifier in self.identifiers:
            record = self.context.ledger.get(identifier)
            if record.state in {"released", "terminated", "failed"}:
                continue
            if record.process is None:
                self.context.owner.set_recovery_barrier(identifier,
                    "Launch outcome lacks a bound process identity; inspect without guessing or replay")
                raise RuntimeError("Launch identity is unresolved; durable reservation retained")
            self.context.owner.reconcile(identifier, record.revision, evidence)


def configure_resource_registry(registry):
    global _registry
    selected = Path(registry).absolute() if registry is not None else None
    with _lock:
        if _context is not None and selected != _registry:
            raise RuntimeError("Cannot change the resource registry of a live FANUC supervisor")
        _registry = selected


def resource_context():
    global _context
    with _lock:
        if _context is None:
            from universal_tools.resources import ResourceLedger
            registry = _registry or os.environ.get("FANUC_MCP_MANAGEMENT_REGISTRY")
            _context = ResourceContext(ResourceLedger(registry, max_active_per_app=32))
        return _context


def close_resource_context():
    with _lock:
        if _context is not None:
            _context.close()


@contextmanager
def registry_environment(registry=None):
    """Explicitly propagate the host's registry through the SDK's env allowlist."""
    from universal_tools.management import default_registry
    key = "FANUC_MCP_MANAGEMENT_REGISTRY"
    previous = os.environ.get(key)
    selected = Path(registry or previous or default_registry()).absolute()
    os.environ[key] = str(selected)
    try:
        yield selected
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
