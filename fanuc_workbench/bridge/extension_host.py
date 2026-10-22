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
    operations = {name: {"id": name, "description": description, "effect": "prepare", "revision": "core@1",
                   "input_schema": object_schema(fields, fields), "output_schema": {"type": "object"},
                   "extension_id": "core"} for name, description, fields in specs}
    native = [
        ("core.controller.status", "Read native current position, task status and alarm evidence", "read", {"robot_id": {"type": "string"}, "group": {"type": "integer", "minimum": 1, "maximum": 8}, "alarm_limit": {"type": "integer", "minimum": 0, "maximum": 100}}, ["robot_id"]),
        ("core.controller.sample", "Read native R/PR/I/O values through the configured vendor data API", "read", {"robot_id": {"type": "string"}, "expressions": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100, "uniqueItems": True}}, ["robot_id", "expressions"]),
        ("core.controller.reconnect", "Explicitly reconnect a native worker after failure; invalidates native plans; no controller writes", "read", {"robot_id": {"type": "string"}}, ["robot_id"]),
        ("core.controller.programs", "Browse native program names and metadata", "read", {"robot_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["robot_id"]),
        ("core.registers.read", "Preserve a typed native R/PR value and exact endpoint-bound SHA-256 before editing", "read", {"robot_id": {"type": "string"}, "target": {"type": "string"}}, ["robot_id", "target"]),
        ("core.programs.inspect", "Capture native program presence/metadata and exact TP/LS evidence before deployment", "read", {"robot_id": {"type": "string"}, "program": {"type": "string"}}, ["robot_id", "program"]),
        ("core.registers.update", "Prepare an exact-allowlisted R/PR value change from a preserved native read; no write", "prepare", {"robot_id": {"type": "string"}, "target": {"type": "string"}, "expected_sha256": {"type": "string"}, "change": {"type": "object"}}, ["robot_id", "target", "expected_sha256", "change"]),
        ("core.programs.deploy", "Prepare native LS loading/compilation with preserved old program, source SHA and readback checks; never executes", "prepare", {"robot_id": {"type": "string"}, "source_file": {"type": "string"}, "expected_sha256": {"type": "string"}}, ["robot_id", "source_file", "expected_sha256"]),
    ]
    for name, description, effect, properties, required in native:
        operations[name] = {"id": name, "description": description, "effect": effect, "revision": "core.native@1",
            "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "output_schema": {"type": "object"}, "extension_id": "core"}
    operations["core.snpx.read_words"] = {"id": "core.snpx.read_words", "description":
        "Capture exact-allowlisted raw SNPX %R words; assignment meaning is unverified; no mapping changes",
        "effect": "read", "revision": "core.snpx@1", "extension_id": "core",
        "input_schema": {"type": "object", "properties": {"robot_id": {"type": "string"},
            "address": {"type": "integer", "minimum": 1, "maximum": 65535},
            "count": {"type": "integer", "minimum": 1, "maximum": 128}},
            "required": ["robot_id", "address"], "additionalProperties": False},
        "output_schema": {"type": "object"}}
    operations['core.recordings.bundle_evidence'] = {
        'id': 'core.recordings.bundle_evidence', 'description':
            'Bundle a saved recording and selected hash-verified sample artifacts locally; no controller access',
        'effect': 'read', 'revision': 'core.recordings@1', 'extension_id': 'core',
        'input_schema': {'type': 'object', 'properties': {
            'artifact_id': {'type': 'string'}, 'sample_offset': {'type': 'integer', 'minimum': 0},
            'sample_limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}},
            'required': ['artifact_id'], 'additionalProperties': False}, 'output_schema': {'type': 'object'}}
    operations['core.requests.status'] = {
        'id': 'core.requests.status', 'description': 'Inspect bounded queued/running requests and recent outcomes; no controller contact',
        'effect': 'read', 'revision': 'core.requests@1', 'extension_id': 'core',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'output_schema': {'type': 'object'}}
    return operations


@dataclass(eq=False)
class Generation:
    fingerprint: str
    workers: dict = field(default_factory=dict)
    operations: dict = field(default_factory=dict)
    references: int = 0
    retired: bool = False

    def close(self):
        failed = 0
        for identifier, worker in tuple(self.workers.items()):
            try:
                worker.close()
            except Exception:
                failed += 1
            else:
                self.workers.pop(identifier)
        if failed:
            raise RuntimeError("Retired feature worker cleanup is uncertain; failed owners retained")


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
            published = False
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
                    native = len(self.bridge.native.plans) if not initial else 0
                    self.bridge._audit("extensions_loaded" if initial else "extensions_reloaded",
                        generation=fingerprint, extensions=[{"id": key, "sha256": w.checksum} for key, w in candidate.workers.items()],
                        invalidated_upload_plans=uploads, invalidated_comment_plans=comments, invalidated_native_plans=native)
                    if not initial:
                        self.bridge._plans.clear()
                        self.bridge.comments.plans.clear()
                        self.bridge.native.plans.clear()
                    old, self.current = self.current, candidate
                    candidate = None
                    published = True
                    if old is not None:
                        old.retired = True
                        if old.references:
                            self.retired.append(old)
                if old is not None and not old.references:
                    self._close_generation(old)
                return {"status": "loaded" if initial else "reloaded", "invalidated_upload_plans": uploads,
                        "invalidated_native_plans": native,
                        "invalidated_comment_plans": comments, "controller_contacted": False,
                        "mcp_tools_changed": False, **self.status()}
            except Exception:
                if candidate is not None:
                    self._close_generation(candidate)
                if published:
                    raise ValueError("New feature generation is active but retired cleanup is uncertain; inspect ownership. No automatic rollback was attempted") from None
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

    def discovery_snapshot(self):
        """Installation metadata only: never start/reload workers or select a robot."""
        with self.lock:
            if self.closed:
                raise ValueError("Extension host has closed")
            if self.current is not None:
                return copy.deepcopy([*core_operations().values(), *self.current.operations.values()])
        operations = list(core_operations().values())
        if (self.root / "registry.json").exists():
            rows = load_registry(self.root)
            releases = [(row, release_path(self.root, row["id"], row["version"])) for row in rows]
        else:
            source = Path(__file__).resolve().parents[1] / "extensions" / "analysis"
            manifest, checksum = load_release(source, packaged=True)
            releases = [({"id": manifest["id"], "version": manifest["version"],
                          "manifest_sha256": checksum}, source)]
        for row, source in releases:
            manifest, _ = load_release(source, row["manifest_sha256"], packaged=not (self.root / "registry.json").exists())
            if manifest["id"] != row["id"] or manifest["version"] != row["version"]:
                raise ValueError("Selected extension identity changed")
            operations.extend({**descriptor, "extension_id": row["id"],
                "revision": f"{row['id']}@{row['version']}:{row['manifest_sha256']}"}
                for descriptor in manifest["operations"])
        ids = [row["id"] for row in operations]
        if len(ids) != len(set(ids)) or len(ids) > 1000:
            raise ValueError("Invalid discovery catalog")
        return operations

    def describe(self, operation_id):
        core = core_operations().get(operation_id)
        if core is not None:
            return copy.deepcopy(core)
        self.ensure()
        with self.lock:
            row = self.current.operations.get(operation_id)
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
                self._close_generation(generation)

    def _close_generation(self, generation):
        try:
            generation.close()
        except Exception:
            with self.lock:
                generation.retired = True
                if generation not in self.retired:
                    self.retired.append(generation)
            raise

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
            descriptor = core_operations().get(operation_id)
            if descriptor is None or descriptor["effect"] != "read":
                raise ValueError("Core prepared changes cannot be invoked through a read operation")
            if revision != descriptor["revision"]:
                raise ValueError("Native operation revision changed; describe it again")
            validate_data(descriptor["input_schema"], arguments)
            handlers = {"core.controller.status": self.bridge.native.status,
                "core.controller.sample": self.bridge.native.sample, "core.controller.reconnect": self.bridge.native.reconnect,
                "core.controller.programs": self.bridge.native.programs, "core.registers.read": self.bridge.native.read_register,
                "core.programs.inspect": self.bridge.native.read_program,
                "core.snpx.read_words": self.bridge.snpx.read_words,
                "core.recordings.bundle_evidence": self.bridge.operations.bundle_recording,
                "core.requests.status": self.bridge.requests.status}
            return {"operation_id": operation_id, "revision": revision, "result": handlers[operation_id](**arguments)}
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
            if core["effect"] != "prepare":
                raise ValueError("Read operations cannot prepare a controller change")
            if revision != core["revision"]:
                raise ValueError("Core operation revision changed")
            validate_data(core["input_schema"], arguments)
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
            with self.bridge._robot_lock(robot_id):
                with self.bridge._lock, self.lock:
                    if self.current is not generation or self.bridge.config is not original_config:
                        raise ValueError("Feature or connections changed while computing the proposal; retrieve and review again")
                    if kind in {"file_replace", "file_create"}:
                        if not isinstance(proposal, dict) or set(proposal) != {"data_base64", "sha256"}:
                            raise ValueError("File proposals must contain only exact bytes and their SHA-256")
                        suffix = Path(remote).suffix
                        staged = self.bridge.stage_file("proposal" + suffix, proposal["data_base64"], proposal["sha256"])
                    elif not isinstance(proposal, dict) or set(proposal) != {"comment"}:
                        raise ValueError("Description proposals must contain only the proposed comment")
                # The controller lease pins connections and prevents generation
                # replacement while preparation performs its controller reads.
                if kind in {"file_replace", "file_create"}:
                    if kind == "file_replace":
                        result = self.bridge.prepare_upload(robot_id, remote, staged["source_file"], arguments["expected_sha256"])
                    else:
                        result = self.bridge.prepare_file_create(robot_id, remote, staged["source_file"])
                else:
                    result = self.bridge.comments.prepare(robot_id, remote, arguments["target"], proposal["comment"], arguments["expected_sha256"])
                try:
                    self.bridge._audit("extension_proposal_prepared", operation_id=operation_id,
                                       revision=revision, plan_id=result["plan_id"])
                except Exception:
                    with self.bridge._lock:
                        self.bridge._plans.pop(result["plan_id"], None)
                        self.bridge.comments.plans.pop(result["plan_id"], None)
                    raise
                return {**result, "operation_id": operation_id, "operation_revision": revision}

    def _prepare_core(self, operation_id, args):
        if operation_id == "core.registers.update":
            return self.bridge.native.prepare_register(**args)
        if operation_id == "core.programs.deploy":
            return self.bridge.native.prepare_program(**args)
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
                if self.retired:
                    raise RuntimeError("Previous feature cleanup is uncertain; retained owners require recovery")
                return
            self.closed = True
            generations = [*self.retired, *([self.current] if self.current else [])]
            self.retired = []
        failed = 0
        for generation in generations:
            try:
                self._close_generation(generation)
            except Exception:
                failed += 1
        if failed:
            raise RuntimeError("Feature cleanup is incomplete; retained owners require recovery")
