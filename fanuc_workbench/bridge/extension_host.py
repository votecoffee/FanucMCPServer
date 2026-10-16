"""Stable feature catalog, generation leases and core-controlled proposal dispatch."""
from __future__ import annotations

import copy
import hashlib
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fanuc_workbench.bridge.extension_process import FeatureWorker
from fanuc_workbench.bridge.extension_release import (
    HOST_API, MAX_MESSAGE, install_release, json_bytes, load_registry, load_release,
    release_path, snapshot_release, validate_data,
)


def object_schema(properties, required):
    return {"type": "object", "properties": {key: {"type": "string"} for key in properties},
            "required": list(required), "additionalProperties": False}


def core_operations():
    specs = [
        ("core.files.replace", "Prepare replacement of an existing exact-allowlisted application file",
         ["robot_id", "path", "source_file", "expected_sha256"]),
        ("core.files.create", "Prepare creation of an exact-allowlisted TXT/CSV storage file outside MD:",
         ["robot_id", "path", "source_file"]),
        ("core.files.restore", "Prepare one manifest-bound file recovery using the existing upload policy",
         ["robot_id", "artifact_id", "path", "remote", "expected_sha256"]),
        ("core.comments.update", "Prepare an exact-allowlisted description change; never a value write",
         ["robot_id", "listing_path", "target", "comment", "expected_sha256"]),
    ]
    return {name: {"id": name, "description": description, "effect": "prepare", "revision": "core@1",
                   "input_schema": object_schema(fields, fields), "output_schema": {"type": "object"},
                   "extension_id": "core"} for name, description, fields in specs}


@dataclass(eq=False)
class Generation:
    fingerprint: str
    workers: dict = field(default_factory=dict)
    operations: dict = field(default_factory=dict)
    references: int = 0
    retired: bool = False

    def close(self):
        for worker in self.workers.values():
            worker.close()


class ExtensionHost:
    def __init__(self, bridge, *, startup_timeout=15.0, operation_timeout=30.0):
        self.bridge = bridge
        self.root = bridge.config.state_dir.resolve() / "extensions"
        self.lock = threading.RLock()
        self.reload_lock = threading.Lock()
        self.current: Generation | None = None
        self.retired = []
        self.closed = False
        self.startup_timeout = startup_timeout
        self.operation_timeout = operation_timeout

    def _registry(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.resolve() != self.root:
            raise ValueError("Extension state cannot redirect outside bridge state")
        if not (self.root / "registry.json").exists():
            builtin = Path(__file__).resolve().parents[1] / "extensions" / "analysis"
            install_release(self.root, builtin, bootstrap=True, packaged=True)
        return load_registry(self.root)

    @staticmethod
    def _fingerprint(rows):
        return hashlib.sha256(json_bytes(sorted(rows, key=lambda row: row["id"]))).hexdigest()

    def ensure(self):
        with self.lock:
            missing = self.current is None
            if self.closed:
                raise ValueError("Extension host has closed")
        if missing:
            self.reload()

    def status(self):
        with self.lock:
            return {"host_api": HOST_API, "host_pid": os.getpid(), "loaded": self.current is not None,
                    "generation": self.current.fingerprint if self.current else None,
                    "extensions": [{"id": key, "version": worker.manifest["version"],
                                    "manifest_sha256": worker.checksum, "worker_pid": worker.pid,
                                    "healthy": worker.healthy}
                                   for key, worker in (self.current.workers.items() if self.current else [])],
                    "retiring_generations": len(self.retired)}

    def reload(self):
        with self.reload_lock:
            with self.lock:
                if self.closed:
                    raise ValueError("Extension host has closed")
            candidate = None
            try:
                rows = self._registry()
                fingerprint = self._fingerprint(rows)
                # Even a no-op checks pinned release bytes, without executing them.
                loaded = []
                for row in rows:
                    source = release_path(self.root, row["id"], row["version"])
                    manifest, _ = load_release(source, row["manifest_sha256"])
                    if manifest["id"] != row["id"] or manifest["version"] != row["version"]:
                        raise ValueError("Selected extension identity changed")
                    loaded.append((row, source, manifest))
                if sum(len(m["operations"]) for _, _, m in loaded) > 256:
                    raise ValueError("Extension catalog exceeds 256 operations")
                with self.lock:
                    if (self.current is not None and fingerprint == self.current.fingerprint
                            and all(worker.healthy for worker in self.current.workers.values())):
                        return {"status": "unchanged", "invalidated_upload_plans": 0,
                                "invalidated_comment_plans": 0, "controller_contacted": False, **self.status()}
                candidate = Generation(fingerprint)
                for row, source, manifest in loaded:
                    snapshot = self.root / "snapshots" / uuid.uuid4().hex
                    copied = snapshot_release(source, snapshot, row["manifest_sha256"])
                    worker = FeatureWorker(snapshot, copied, row["manifest_sha256"], row.get("python", sys.executable),
                        startup_timeout=self.startup_timeout, operation_timeout=self.operation_timeout)
                    candidate.workers[row["id"]] = worker
                    for descriptor in manifest["operations"]:
                        op_id = descriptor["id"]
                        if op_id in candidate.operations:
                            raise ValueError("Duplicate extension operation")
                        candidate.operations[op_id] = {**descriptor, "extension_id": row["id"],
                            "revision": f"{row['id']}@{row['version']}:{row['manifest_sha256']}"}
                old = None
                with self.bridge._lock, self.bridge._connection_lock, self.bridge.operations.jobs.lock, self.lock:
                    if self.closed:
                        raise ValueError("Extension host has closed")
                    if self._fingerprint(load_registry(self.root)) != fingerprint:
                        raise ValueError("Extension selection changed during validation; reload again")
                    initial = self.current is None
                    if not initial and (self.bridge._active_robot_operations or self.bridge.operations.jobs.events):
                        raise ValueError("Controller operations or read jobs are active; finish them before extension reload")
                    uploads = len(self.bridge._plans) if not initial else 0
                    comments = len(self.bridge.comments.plans) if not initial else 0
                    self.bridge._audit("extensions_loaded" if initial else "extensions_reloaded",
                        generation=fingerprint, extensions=[{"id": key, "sha256": w.checksum} for key, w in candidate.workers.items()],
                        invalidated_upload_plans=uploads, invalidated_comment_plans=comments)
                    if not initial:
                        self.bridge._plans.clear()
                        self.bridge.comments.plans.clear()
                    old, self.current = self.current, candidate
                    candidate = None
                    if old is not None:
                        old.retired = True
                        if old.references:
                            self.retired.append(old)
                if old is not None and not old.references:
                    old.close()
                return {"status": "loaded" if initial else "reloaded", "invalidated_upload_plans": uploads,
                        "invalidated_comment_plans": comments, "controller_contacted": False,
                        "mcp_tools_changed": False, **self.status()}
            except Exception:
                if candidate is not None:
                    candidate.close()
                # Local metadata and exception text may contain private paths/data.
                raise ValueError("Extension reload failed; active features and pending plans were preserved") from None

    def list(self, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Use offset >= 0 and limit between 1 and 500")
        self.ensure()
        with self.lock:
            rows = sorted([*core_operations().values(), *self.current.operations.values()], key=lambda row: row["id"])
            selected = rows[offset:offset + limit]
            return {"items": [{key: copy.deepcopy(value) for key, value in row.items()
                               if key in {"id", "description", "effect", "revision", "extension_id"}} for row in selected],
                    "total": len(rows), "offset": offset,
                    "next_offset": offset + limit if offset + limit < len(rows) else None, **self.status()}

    def describe(self, operation_id):
        self.ensure()
        with self.lock:
            row = core_operations().get(operation_id) or self.current.operations.get(operation_id)
            if row is None:
                raise ValueError("Unknown operation ID; list the current catalog")
            return copy.deepcopy(row)

    @contextmanager
    def _lease(self, operation_id, revision):
        self.ensure()
        with self.lock:
            generation = self.current
            row = generation.operations.get(operation_id)
            if row is None or row["revision"] != revision:
                raise ValueError("Operation is unknown or its revision changed; describe it again")
            generation.references += 1
            worker = generation.workers[row["extension_id"]]
        try:
            yield generation, row, worker
        finally:
            close = False
            with self.lock:
                generation.references -= 1
                if generation.retired and not generation.references:
                    if generation in self.retired:
                        self.retired.remove(generation)
                    close = True
            if close:
                generation.close()

    def _context(self, descriptor, arguments):
        contexts = {}
        for binding in descriptor.get("contexts", []):
            identifier = arguments[binding["argument"]]
            item = self.bridge.operations.store.get(identifier)
            if binding["kind"] != "any" and item["kind"] != binding["kind"]:
                raise ValueError("Saved artifact does not match the operation's context kind")
            root = self.bridge.operations.store.verify(identifier)
            selected = {"artifact_id": identifier, "root": str(root)}
            if "file_argument" in binding:
                selected["file"] = str(self.bridge.operations.store.file(identifier, arguments[binding["file_argument"]]))
            contexts[binding["name"]] = selected
        return contexts

    def run_read(self, operation_id, arguments, revision):
        if operation_id.startswith("core."):
            raise ValueError("Core prepared changes cannot be invoked through a read operation")
        with self._lease(operation_id, revision) as (_, descriptor, worker):
            if descriptor["effect"] != "read":
                raise ValueError("This operation prepares a change; use prepare_change")
            validate_data(descriptor["input_schema"], arguments)
            context = self._context(descriptor, arguments)
            result = worker.call(operation_id, arguments, context)
            validate_data(descriptor["output_schema"], result, maximum=MAX_MESSAGE)
            self.bridge._audit("extension_read", operation_id=operation_id, revision=revision)
            return {"operation_id": operation_id, "revision": revision, "result": result}

    def legacy_analysis(self, artifact_id, kind, offset=0, limit=100, query=""):
        name = "workbench.analysis." + kind
        descriptor = self.describe(name)
        return self.run_read(name, {"artifact_id": artifact_id, "offset": offset, "limit": limit, "query": query},
                             descriptor["revision"])["result"]

    def prepare(self, operation_id, arguments, revision):
        core = core_operations().get(operation_id)
        if core is not None:
            if revision != core["revision"]:
                raise ValueError("Core operation revision changed")
            validate_data(core["input_schema"], arguments)
            with self.bridge._lock:
                return self._prepare_core(operation_id, arguments)
        with self._lease(operation_id, revision) as (generation, descriptor, worker):
            if descriptor["effect"] != "prepare":
                raise ValueError("Read operations cannot prepare a controller change")
            validate_data(descriptor["input_schema"], arguments)
            with self.bridge._lock:
                original_config = self.bridge.config
                context = self._context(descriptor, arguments)
                kind = descriptor["proposal_kind"]
                robot_id = arguments.get("robot_id")
                if not isinstance(robot_id, str):
                    raise ValueError("Prepared extension operations require an explicit robot_id")
                if kind in {"file_replace", "file_create"}:
                    remote = self.bridge._path(robot_id, arguments.get("path"), write=kind == "file_replace", create=kind == "file_create")
                    if kind == "file_replace":
                        capture = self.bridge.reference_capture(robot_id, remote, arguments.get("expected_sha256"))
                        context["original"] = {"file": capture["local_path"], "sha256": capture["sha256"]}
                else:
                    remote = arguments.get("listing_path")
                    self.bridge.comments._policy(robot_id, remote, arguments.get("target"))
                    capture = self.bridge.reference_capture(robot_id, remote, arguments.get("expected_sha256"))
                    context["original"] = {"file": capture["local_path"], "sha256": capture["sha256"]}
            proposal = worker.call(operation_id, arguments, context)
            validate_data(descriptor["output_schema"], proposal, maximum=MAX_MESSAGE)
            with self.bridge._lock, self.lock:
                if self.current is not generation or self.bridge.config is not original_config:
                    raise ValueError("Feature or connections changed while computing the proposal; retrieve and review again")
                if kind in {"file_replace", "file_create"}:
                    if not isinstance(proposal, dict) or set(proposal) != {"data_base64", "sha256"}:
                        raise ValueError("File proposals must contain only exact bytes and their SHA-256")
                    suffix = Path(remote).suffix
                    staged = self.bridge.stage_file("proposal" + suffix, proposal["data_base64"], proposal["sha256"])
                    if kind == "file_replace":
                        result = self.bridge.prepare_upload(robot_id, remote, staged["source_file"], arguments["expected_sha256"])
                    else:
                        result = self.bridge.prepare_file_create(robot_id, remote, staged["source_file"])
                else:
                    if not isinstance(proposal, dict) or set(proposal) != {"comment"}:
                        raise ValueError("Description proposals must contain only the proposed comment")
                    result = self.bridge.comments.prepare(robot_id, remote, arguments["target"], proposal["comment"], arguments["expected_sha256"])
                try:
                    self.bridge._audit("extension_proposal_prepared", operation_id=operation_id,
                                       revision=revision, plan_id=result["plan_id"])
                except Exception:
                    self.bridge._plans.pop(result["plan_id"], None)
                    self.bridge.comments.plans.pop(result["plan_id"], None)
                    raise
                return {**result, "operation_id": operation_id, "operation_revision": revision}

    def _prepare_core(self, operation_id, args):
        if operation_id == "core.files.replace":
            return self.bridge.prepare_upload(**args)
        if operation_id == "core.files.create":
            return self.bridge.prepare_file_create(**args)
        if operation_id == "core.files.restore":
            return self.bridge.operations.prepare_restore(args["robot_id"], args["artifact_id"], args["path"], args["remote"], args["expected_sha256"])
        if operation_id == "core.comments.update":
            return self.bridge.comments.prepare(args["robot_id"], args["listing_path"], args["target"], args["comment"], args["expected_sha256"])
        raise ValueError("Unknown core preparation")

    def close(self):
        with self.reload_lock, self.lock:
            if self.closed:
                return
            self.closed = True
            generations = [*self.retired, *([self.current] if self.current else [])]
            self.retired = []
        for generation in generations:
            generation.close()
