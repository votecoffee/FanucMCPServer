"""One-shot Windows workbench commands over the existing typed engineering services."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import sys
import time
from typing import get_args, get_origin, get_type_hints

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.extension_host import core_operations
from fanuc_workbench.bridge.extension_release import load_release, read_json, validate_data
from fanuc_workbench.bridge.jobs import JobManager
from fanuc_workbench.bridge.operations import WorkbenchOperations
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.storage import ArtifactStore, atomic_json
from fanuc_workbench.local_plans import LocalPlans
from fanuc_workbench.services.workspace_api import WorkspaceService, page, serial


VERSION = "1.0.1"
ACTIVE_JOB = {"queued", "running", "cancel_requested"}
NATIVE_TARGET_ROUTES = {
    "core.controller.status", "core.controller.sample", "core.controller.programs",
    "core.registers.read", "core.registers.update", "core.programs.inspect",
    "core.programs.deploy", "monitor.diagnostics",
}


def schema(annotation):
    origin = get_origin(annotation)
    types = {str: "string", int: "integer", float: "number", bool: "boolean",
             dict: "object", list: "array", type(None): "null"}
    if origin is list:
        return {"type": "array", "items": schema(get_args(annotation)[0])}
    if origin is dict:
        return {"type": "object"}
    if origin is not None:
        return {"anyOf": [schema(item) for item in get_args(annotation)]}
    return {"type": types[annotation]} if annotation in types else {}


def input_schema(function, overrides=None):
    hints = get_type_hints(function)
    properties, required = {}, []
    for name, parameter in inspect.signature(function).parameters.items():
        if name == "self" or parameter.kind in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}:
            continue
        default = parameter.default
        fallback = str if default is parameter.empty else type(default) if default is not None else object
        properties[name] = (overrides or {}).get(name, schema(hints.get(name, fallback)))
        if default is parameter.empty:
            required.append(name)
        else:
            properties[name] = {**properties[name], "default": default}
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


@dataclass
class Route:
    descriptor: dict
    call: object


def bounded_jobs(bridge, jobs, timeout_seconds):
    manager = bridge.operations.jobs
    ids = [job["job_id"] for job in jobs]
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        for identifier in ids:
            atomic_json(job_owner(bridge, identifier), {"job_id": identifier, "app": "fanuc-local"})
        while True:
            rows = [manager.status(identifier) for identifier in ids]
            if not any(row["status"] in ACTIVE_JOB for row in rows):
                return {"jobs": rows, "deadline_reached": timed_out}
            if not timed_out and time.monotonic() >= deadline:
                timed_out = True
                for identifier in ids:
                    manager.cancel(identifier)
            time.sleep(0.05)
    except BaseException:
        for identifier in ids:
            manager.cancel(identifier)
        raise


def backup(robot_id: str, paths: list[str] | None = None, max_depth: int = 8,
           attempt_ascii: bool = True, timeout_seconds: float = 300, *, ctx):
    validate_timeout(timeout_seconds)
    job = ctx.bridge.operations.start_backup(robot_id, paths, max_depth, attempt_ascii)
    return bounded_jobs(ctx.bridge, [job], timeout_seconds)


def fleet_backup(robot_ids: list[str], attempt_ascii: bool = True, timeout_seconds: float = 600, *, ctx):
    validate_timeout(timeout_seconds)
    started = ctx.bridge.operations.start_fleet_backup(robot_ids, attempt_ascii)
    return {**started, **bounded_jobs(ctx.bridge, started["jobs"], timeout_seconds)}


def record(robot_id: str, expressions: list[str], samples: int = 10, interval_seconds: float = 2,
           cycle_signal: str = "", timeout_seconds: float = 300, *, ctx):
    validate_timeout(timeout_seconds)
    job = ctx.bridge.operations.start_watch(robot_id, expressions, samples, interval_seconds, cycle_signal)
    return bounded_jobs(ctx.bridge, [job], timeout_seconds)


def validate_timeout(timeout_seconds):
    if not 0 < timeout_seconds <= 3600:
        raise ValueError("Set timeout_seconds in (0, 3600]")


def job_owner(bridge, identifier):
    return bridge.config.state_dir / "cli-jobs" / (ArtifactStore.check_id(identifier) + ".json")


def cancel_job(job_id: str, *, ctx):
    if not job_owner(ctx.bridge, job_id).is_file():
        raise ValueError("Only a read job started by the local CLI can be cancelled here")
    return ctx.bridge.operations.jobs.cancel(job_id)


def diagnostic_capture(robot_id: str, expressions: list[str], task_name: str,
                       status_states: dict[str, str] | None = None, signals: list[str] | None = None,
                       before: int = 20, after: int = 20, samples: int = 40,
                       interval_seconds: float = 2, timeout_seconds: float = 300, *, ctx):
    from fanuc_workbench.services.diagnostic_capture import DiagnosticCaptureSession, native_source_id

    validate_timeout(timeout_seconds)
    if not 1 <= samples <= 300 or not 0.5 <= interval_seconds <= 60:
        raise ValueError("Use 1–300 samples and intervals of 0.5–60 seconds")
    bridge = ctx.bridge
    source = native_source_id(bridge, robot_id)
    session = DiagnosticCaptureSession(source, task_name=task_name, status_states=status_states,
                                       signals=signals or [], before=before, after=after)
    deadline = time.monotonic() + timeout_seconds
    folder = bridge._folder() / "diagnostics"
    interrupted = False
    try:
        for index in range(samples):
            if not session.armed or time.monotonic() >= deadline:
                break
            with bridge._robot_lock(robot_id):
                data = bridge.native.sample(robot_id, expressions)
                status = bridge.native.status(robot_id)
                session.observe_native(data, status, source, lambda program: bridge.native.read_program(robot_id, program))
            if index + 1 < samples and session.armed:
                time.sleep(max(0, min(interval_seconds, deadline - time.monotonic())))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        session.save(folder)
        artifact = bridge.operations.store.register(folder, "diagnostic-capture", robot_id=robot_id)
        bridge._audit("diagnostic_capture_retained", robot_id=robot_id, artifact_id=artifact["artifact_id"])
    return {"artifact_id": artifact["artifact_id"], "root": str(folder),
            "interrupted": interrupted, "deadline_reached": time.monotonic() >= deadline}


def artifact_files(artifact_id: str, offset: int = 0, limit: int = 100, *, ctx):
    return page(ctx.bridge.operations.store.get(artifact_id)["files"], offset, limit)


def analyze_workspace(artifact_id: str, kind: str, offset: int = 0, limit: int = 100,
                      query: str = "", *, ctx):
    store = ctx.bridge.operations.store
    if store.get(artifact_id)["kind"] != "workspace":
        raise ValueError("Select a saved workspace artifact")
    return WorkspaceService.analyze(store.verify(artifact_id), kind, offset, limit, query)


def plc_tags(artifact_id: str, path: str, signals: list[str] | None = None,
             current: dict[str, str] | None = None, offset: int = 0, limit: int = 100, *, ctx):
    from fanuc_workbench.services.plc_tags import load_plc_tags, suggest_mappings

    catalog = load_plc_tags(ctx.bridge.operations.store.file(artifact_id, path))
    entries = catalog.pop("entries")
    rows = entries if signals is None else suggest_mappings(entries, signals, current)
    return {**catalog, **page(rows, offset, limit)}


def saved_recording(artifact_id: str, path: str, view: str = "samples",
                    offset: int = 0, limit: int = 100, *, ctx):
    from fanuc_workbench.services.saved_recording import load_recording

    recording = load_recording(ctx.bridge.operations.store.file(artifact_id, path))
    document = recording.document()
    if view not in document or not isinstance(document[view], list):
        raise ValueError("Select a saved recording array such as samples or events")
    metadata = {key: value for key, value in document.items() if not isinstance(value, list)}
    return {**metadata, **page(document[view], offset, limit), "view": view, "sha256": recording.sha256,
            "available_views": sorted(key for key, value in document.items() if isinstance(value, list))}


def change_package(artifact_id: str, paths: list[str], entry_programs: list[str],
                   inventory_path: str | None = None, allow_incomplete: bool = False, *, ctx):
    from fanuc_workbench.services.program_packages import build_change_package

    store = ctx.bridge.operations.store
    root = store.verify(artifact_id)
    destination = ctx.bridge._folder() / "change-package"
    result = build_change_package(root, paths, destination, entry_programs=entry_programs,
                                  inventory_path=inventory_path, allow_incomplete=allow_incomplete)
    item = store.register(destination, "deployment", note="Dependency review only")
    manifest = store.file(item["artifact_id"], "package.json")
    return {**result, "artifact_id": item["artifact_id"], "manifest": str(manifest),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "deployment_authorized": False}


def prepare_compile(artifact_id: str, path: str, toolchain_id: str, expected_sha256: str,
                    timeout: float = 30, *, ctx):
    validator = ctx.compiler()
    return ctx.plans.save(validator.prepare(artifact_id, path, toolchain_id, expected_sha256, timeout), validator)


def run_compile(plan_id: str, review_sha256: str, expected_plan_sha256: str, *, ctx):
    return ctx.plans.run_compile(ctx.compiler(), plan_id, review_sha256, expected_plan_sha256)


def compiler_list(*, ctx):
    return {"toolchains": ctx.compiler().policy.summary(), "controller_contacted": False}


def compiler_manifest(maketp: str, printtp: str, configuration: str, robot_context: str,
                      version: str, components: list[str], component_evidence: str, *, ctx):
    from fanuc_workbench.services.local_native import capture_toolchain, document_hash

    manifest = capture_toolchain(maketp=Path(maketp), printtp=Path(printtp), configuration=Path(configuration),
                                 robot_context=Path(robot_context), version=version,
                                 components=[Path(item) for item in components], component_evidence=component_evidence)
    return {"manifest": manifest, "sha256": document_hash(manifest), "controller_contacted": False}


def confirmation(record):
    if not sys.stdin.isatty():
        raise ValueError("This policy requires an operator to run commit in an interactive console")
    token = "APPROVE " + record["plan_id"] + " " + record["approved_sha256"]
    print(json.dumps(record["review"], indent=2), file=sys.stderr)
    print("After reviewing the exact target and preimage, type " + token, file=sys.stderr, flush=True)
    if input().strip() != token:
        raise ValueError("Operator did not approve this exact change")


def commit_change(plan_id: str, review_sha256: str, approved_sha256: str, *, ctx):
    return ctx.plans.commit(plan_id, review_sha256, approved_sha256, confirmation)


def reconcile_change(plan_id: str, review_sha256: str, evidence_note: str, *, ctx):
    def confirm(identifier, evidence):
        if not sys.stdin.isatty():
            raise ValueError("Reconciliation requires the operator's interactive console")
        token = "RECONCILE " + identifier
        print(f"Inspection evidence: {evidence}\nTo release this hold without replay, type {token}",
              file=sys.stderr, flush=True)
        if input().strip() != token:
            raise ValueError("Operator did not confirm reconciliation")

    return ctx.plans.reconcile(plan_id, review_sha256, evidence_note, confirm)


def registered_features(*, ctx):
    extensions = ctx.bridge.extensions
    if not (extensions.root / "registry.json").is_file():
        raise ValueError("This profile has no operator-staged feature registry")
    return {"operations": [row for row in extensions.discovery_snapshot() if row["extension_id"] != "core"]}


def registered_operation(operation_id: str, revision: str, arguments: dict, *, ctx):
    descriptor = next((row for row in registered_features(ctx=ctx)["operations"]
                       if row["id"] == operation_id and row["revision"] == revision), None)
    if descriptor is None:
        raise ValueError("Select an operator-staged feature and its exact catalog revision")
    extensions = ctx.bridge.extensions
    if descriptor["effect"] == "prepare":
        return ctx.plans.save(extensions.prepare(operation_id, arguments, revision))
    return extensions.run_read(operation_id, arguments, revision)["result"]


class Context:
    def __init__(self, bridge, config_sha256, native_policy):
        self.bridge = bridge
        self.config_sha256 = config_sha256
        self.native_policy = native_policy

    @property
    def plans(self):
        return LocalPlans(self.bridge, self.config_sha256)

    def compiler(self):
        from fanuc_workbench.services.local_native import LocalNativeValidation, NativeToolPolicy

        if self.native_policy is None:
            raise ValueError("Select the operator-pinned compiler policy with --native-policy")
        return LocalNativeValidation(self.bridge, NativeToolPolicy(self.native_policy))


@lru_cache(maxsize=1)
def catalog():
    routes = {}

    def add(name, family, effect, description, function, dispatch, overrides=None):
        parameters = input_schema(function, overrides)
        parameters["properties"].pop("ctx", None)
        parameters["required"] = [item for item in parameters["required"] if item != "ctx"]
        routes[name] = Route({"id": name, "family": family, "effect": effect, "description": description,
                              "revision": "local@" + VERSION, "input_schema": parameters,
                              "output_schema": {"type": "object"}}, dispatch)

    def bound(owner, class_, rows):
        for name, method, family, effect, description in rows:
            def dispatch(ctx, args, method=method, owner=owner):
                target = ctx
                for part in owner.split("."):
                    target = getattr(target, part)
                return getattr(target, method)(**args)
            overrides = {"data": {"type": ["object", "array"]}} if name == "workspace.metadata_write" else None
            add(name, family, effect, description, getattr(class_, method), dispatch, overrides)

    bound("bridge", RobotBridge, [
        ("robots.list", "list_robots", "controller", "read", "List configured targets without contacting them"),
        ("files.list", "list_files", "files", "controller_read", "List a configured controller directory"),
        ("files.read", "read_file", "files", "controller_read", "Capture exact controller file bytes"),
        ("files.read_capture", "read_captured_file", "files", "read", "Read exact captured bytes in a bounded range"),
        ("files.stage", "stage_file", "files", "local_output", "Stage hash-verified bytes for a future proposal"),
        ("logs.collect", "collect_logs", "logs", "controller_read", "Capture explicit controller log files"),
    ])
    bound("bridge.operations", WorkbenchOperations, [
        ("robots.capabilities", "capabilities", "controller", "read", "Describe configured access and providers"),
        ("robots.probe", "probe", "controller", "controller_read", "Probe configured file services"),
        ("workspace.import", "import_workspace", "workspace", "local_output", "Import a source-root-relative backup into preserved evidence"),
        ("workspace.compare", "compare", "workspace", "read", "Compare saved backup and program bytes"),
        ("workspace.edit", "edit", "workspace", "local_output", "Edit a separate workspace copy"),
        ("workspace.metadata_read", "read_metadata", "workspace", "read", "Read saved checklist, PLC map or handshake data"),
        ("workspace.metadata_write", "write_metadata", "workspace", "local_output", "Write metadata into a new workspace copy"),
        ("workspace.project", "project", "workspace", "local_output", "Create/read a project or add a captured workspace"),
        ("program.inspect", "inspect_program", "program", "read", "Inspect saved LS structure, flow and direct calls"),
        ("monitor.sample", "sample", "monitoring", "controller_read", "Capture configured file/native/SNPX watches"),
        ("snapshot.capture", "snapshot", "diagnostics", "local_output", "Capture a saved troubleshooting snapshot"),
        ("snapshot.compare", "compare_snapshots", "diagnostics", "read", "Compare preserved troubleshooting snapshots"),
        ("artifact.package", "make_package", "workspace", "local_output", "Generate documentation, baseline, deployment or ROBOGUIDE handoff"),
        ("recording.analyze", "analyze_recording", "monitoring", "read", "Analyze a saved cycle, handshake or alarm correlation"),
        ("recording.compare", "compare_recordings", "monitoring", "read", "Compare saved cycle profiles"),
        ("pose.transform", "transform_pose", "diagnostics", "read", "Calculate an offline frame conversion"),
        ("audit.read", "audit", "workspace", "read", "Read the paged operation audit"),
    ])
    bound("bridge.operations.store", ArtifactStore, [
        ("artifact.list", "list", "workspace", "read", "Browse preserved artifact receipts"),
        ("artifact.read", "read", "workspace", "read", "Read a manifest-bound byte range"),
    ])
    bound("bridge.operations.jobs", JobManager, [
        ("job.list", "list", "monitoring", "read", "List saved job outcomes"),
        ("job.status", "status", "monitoring", "read", "Read a job's progress and final evidence"),
    ])
    bound("plans", LocalPlans, [
        ("change.status", "status", "review", "read", "Read a retained change review and outcome"),
        ("change.cancel", "cancel", "review", "local_output", "Cancel an unused reviewed plan"),
    ])
    for name, function, family, effect, description in [
        ("backup.capture", backup, "backup", "controller_read", "Run one bounded backup to a terminal receipt"),
        ("backup.fleet", fleet_backup, "backup", "controller_read", "Run a bounded fleet backup and retain all outcomes"),
        ("monitor.record", record, "monitoring", "controller_read", "Record bounded trend, timeline and cycle evidence"),
        ("job.cancel", cancel_job, "monitoring", "local_output", "Request cooperative cancellation of a CLI-owned read"),
        ("monitor.diagnostics", diagnostic_capture, "diagnostics", "controller_read", "Capture native triggered diagnostics and available LS revisions"),
        ("artifact.files", artifact_files, "workspace", "read", "List exact artifact file hashes"),
        ("workspace.analyze", analyze_workspace, "workspace", "read", "Run a saved workspace analysis"),
        ("plc.tags", plc_tags, "plc", "read", "Inspect CSV/L5X tags or suggest I/O mappings"),
        ("recording.open_saved", saved_recording, "monitoring", "read", "Read a page of a saved desktop recording"),
        ("program.change_package", change_package, "program", "local_output", "Publish an artifact-bound dependency review package"),
        ("compile.toolchains", compiler_list, "compiler", "read", "List explicitly pinned local toolchains"),
        ("compile.capture_toolchain", compiler_manifest, "compiler", "read", "Fingerprint selected toolchain files and robot context"),
        ("compile.prepare", prepare_compile, "compiler", "prepare", "Prepare a saved LS and toolchain for review"),
        ("compile.run", run_compile, "compiler", "local_native", "Consume one reviewed local MakeTP/PrintTP plan"),
        ("change.commit", commit_change, "review", "controller_write", "Commit one exact approved change with fresh preflight and readback"),
        ("change.reconcile", reconcile_change, "review", "operator_only", "Record an independent inspection and release its controller hold without replay"),
        ("feature.list", registered_features, "extensions", "read", "Describe already registered operator-trusted features"),
        ("feature.run", registered_operation, "extensions", "registered", "Run a reviewed revision from an existing trusted feature registry"),
    ]:
        add(name, family, effect, description, function, lambda ctx, args, fn=function: fn(**args, ctx=ctx))

    for name, original in core_operations().items():
        if name in {"core.requests.status", "core.controller.reconnect"}:
            continue
        descriptor = {**original, "family": "core"}
        if descriptor["effect"] == "read" and name != "core.recordings.bundle_evidence":
            descriptor["effect"] = "controller_read"
        elif descriptor["effect"] == "prepare":
            descriptor["effect"] = "controller_prepare"
        if name in {"core.registers.update", "core.programs.deploy"}:
            inputs = original["input_schema"]
            descriptor["input_schema"] = {**inputs, "properties": {**inputs["properties"], "reference_path": {"type": "string"}},
                                          "required": [*inputs["required"], "reference_path"]}
        routes[name] = Route(descriptor, lambda ctx, args, spec=original: run_core(ctx, args, spec))

    for path, manifest, checksum in bundled_releases():
        for spec in manifest["operations"]:
            descriptor = {**spec, "family": "bundled", "extension_id": manifest["id"],
                          "revision": f"{manifest['id']}@{manifest['version']}:{checksum}"}
            routes[spec["id"]] = Route(descriptor, lambda ctx, args, spec=spec, path=path: run_bundled(ctx, args, spec, path))
    return routes


def bundled_releases():
    latest = {}
    for path in sorted((Path(__file__).resolve().parent / "extensions").iterdir()):
        if not (path / "extension.json").is_file():
            continue
        manifest, checksum = load_release(path, packaged=True)
        version = tuple(map(int, manifest["version"].split(".")))
        if manifest["id"] not in latest or version > latest[manifest["id"]][0]:
            latest[manifest["id"]] = (version, path, manifest, checksum)
    return [item[1:] for item in latest.values()]


def run_bundled(ctx, args, spec, path):
    handler = importlib.import_module(f"fanuc_workbench.extensions.{path.name}.feature").handle
    contexts = ctx.bridge.extensions._context(spec, args)
    result = handler(spec["id"], args, contexts)
    validate_data(spec["output_schema"], result, maximum=4 * 1024 * 1024)
    return result


def run_core(ctx, args, spec):
    name, revision = spec["id"], spec["revision"]
    if spec["effect"] == "read":
        return ctx.bridge.extensions.run_read(name, args, revision)["result"]
    arguments = dict(args)
    if "reference_path" in arguments:
        ctx.plans.bind_native_reference(arguments["robot_id"], arguments["expected_sha256"],
                                        arguments.pop("reference_path"))
    return ctx.plans.save(ctx.bridge.extensions.prepare(name, arguments, revision))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def ordinary_file(path):
    if (not path.is_absolute() or (os.name == "nt" and len(path.drive) != 2)
            or path.resolve(strict=True) != path or not path.is_file()):
        raise ValueError("Use an absolute ordinary local file path")
    return path


def run(args, route):
    from fanuc_workbench.bridge.resource_context import configure_resource_registry, close_resource_context

    arguments = read_json(ordinary_file(args.request), 1024 * 1024)
    validate_data(route.descriptor["input_schema"], arguments)
    if args.revision is not None and args.revision != route.descriptor["revision"]:
        raise ValueError("Operation revision changed; describe the operation again")
    path = args.config or os.environ.get("FANUC_CLI_CONFIG")
    if not path:
        raise ValueError("Select an operator-owned JSON profile with --config")
    path = ordinary_file(Path(path))
    document, raw = read_json(path, with_bytes=True)
    config = BridgeConfig.from_document(document, path)
    policy = ordinary_file(args.native_policy) if args.native_policy else None
    registry = os.environ.get("FANUC_CLI_RESOURCE_DIR") or (config.state_dir / "cli-resources")
    configure_resource_registry(Path(registry))
    try:
        with RobotBridge(config, config_path=path) as bridge:
            ctx = Context(bridge, hashlib.sha256(os.fsencode(path) + b"\0" + raw).hexdigest(), policy)
            name = route.descriptor["id"]
            if name in NATIVE_TARGET_ROUTES | {"monitor.sample", "monitor.record"}:
                robot_id = arguments["robot_id"]
                access = bridge._access(robot_id)
                if name in NATIVE_TARGET_ROUTES or (access.native_provider == "frrobot" and access.sample_provider != "snpx"):
                    with ctx.plans.native_scope(robot_id):
                        return route.call(ctx, arguments)
            return route.call(ctx, arguments)
    finally:
        close_resource_context()


def main(argv=None):
    parser = Parser(prog="RunMiddleware.cmd", description=__doc__)
    parser.add_argument("--version", action="version", version=json.dumps({"version": VERSION}))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog", help="List engineering operations without opening a target")
    describe = commands.add_parser("describe", help="Show the exact JSON schema of one operation")
    invoke = commands.add_parser("run", help="Run one typed request from a JSON file")
    for command in (describe, invoke):
        command.add_argument("operation")
    invoke.add_argument("--request", required=True, type=Path)
    invoke.add_argument("--config", type=Path)
    invoke.add_argument("--native-policy", type=Path)
    invoke.add_argument("--revision")
    status = 0
    try:
        args = parser.parse_args(argv)
        with redirect_stdout(sys.stderr):
            routes = catalog()
            if args.command == "catalog":
                result = {"version": VERSION, "count": len(routes), "operations": [
                    {key: value for key, value in route.descriptor.items() if key in
                     {"id", "family", "effect", "description", "revision"}} for route in routes.values()]}
            else:
                route = routes.get(args.operation)
                if route is None:
                    raise ValueError("Unknown operation; run catalog")
                result = route.descriptor if args.command == "describe" else run(args, route)
        payload = {"ok": True, "result": serial(result)}
        output = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    except (ValueError, OSError, RuntimeError) as error:
        payload = {"ok": False, "error": {"code": type(error).__name__, "message": str(error)}}
        status = 65 if isinstance(error, ValueError) else 74
    except KeyboardInterrupt:
        payload = {"ok": False, "error": {"code": "cancelled", "message": "Inspect retained job or change receipts"}}
        status = 130
    except Exception as error:
        payload = {"ok": False, "error": {"code": type(error).__name__, "message": "Request failed; inspect its evidence"}}
        status = 70
    print(output if status == 0 else json.dumps(payload, ensure_ascii=True, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
