from __future__ import annotations

import json
import hashlib
import math
import os
import shutil
import time
import uuid
import sys
from pathlib import Path

from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.bridge.jobs import JobManager
from fanuc_workbench.bridge.storage import ArtifactStore, atomic_json, confined
from fanuc_workbench.services.workspace_api import WorkspaceService, ANALYSES, page, serial
from fanuc_workbench.services.monitoring import (WATCH_RE, Sample, WatchSpec, WorkspaceSampleProvider,
                                               TrendRecorder, TimelineRecorder, CycleRecorder, profile_cycle)
from fanuc_workbench.services.snapshots import capture_snapshot, compare_snapshots
from fanuc_workbench.services.backup import BackupService
from fanuc_workbench.version import release_metadata


class ScopedReadBackend(RobotBackend):
    """Let BackupService reuse bridge policy and a fresh connection per transfer."""

    def __init__(self, bridge, robot_id):
        self.bridge, self.robot_id = bridge, robot_id

    def connect(self): pass
    def close(self): pass

    def list_dir(self, remote):
        remote = self.bridge._path(self.robot_id, remote)
        return self.bridge._controller(self.robot_id).list_files(remote)

    def download(self, remote, local):
        self.download_bounded(remote, local, self.bridge.config.max_file_bytes)

    def download_bounded(self, remote, local, maximum):
        remote = self.bridge._path(self.robot_id, remote)
        temporary = local.with_name(local.name + "." + uuid.uuid4().hex + ".part")
        try:
            self.bridge._controller(self.robot_id).download_file(remote, temporary, maximum=maximum)
            os.replace(temporary, local)
        finally:
            temporary.unlink(missing_ok=True)

    def upload(self, local, remote):
        raise ValueError("Backup transport is read-only")

    def file_exists(self, remote):
        raise ValueError("Existence probes are not used by the backup capture service")


class WorkbenchOperations:
    def __init__(self, bridge):
        self.bridge = bridge
        self.store = ArtifactStore(bridge.config.state_dir)
        self.jobs = JobManager(bridge.config.state_dir / "jobs")
        self._last_sample: dict[str, float] = {}

    def capabilities(self, robot_id: str | None = None) -> dict:
        if robot_id is not None:
            self.bridge._access(robot_id)
        return {"runtime": {"release": release_metadata(),
                            "gui_imported": any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules)},
                "concurrency": self.bridge.requests.status(),
                "resource_ownership": self.bridge.management_activity()["owned_resources"],
                "analyses": list(ANALYSES), "backups": ["full_roots", "selected_files", "ascii_companions"],
                "live_reads": ["R", "PR", "DI", "DO", "GI", "GO", "AI", "AO", "RI", "RO", "UI", "UO", "F"],
                "sampling_provider": "auto: native FRRobot if enabled, otherwise files; explicit snpx: digital/group/analog I/O",
                "snpx_reads": {"enabled": bool(self.bridge._access(robot_id).snpx_read) if robot_id else "per configured robot",
                               "operations": ["core.snpx.read_words"], "sample_provider": "explicit snpx setting",
                               "scope": "exact digital/group/analog I/O and raw %R spans; no assignment setup or variable decoding",
                               "word_io": {"formats": ["u16", "i16"], "selection": "explicit per-target io_word_formats",
                                           "scaling_applied": False, "units": "raw counts", "maximum_index": 1000},
                               "controller_acceptance": "pending", "writes": "unsupported"},
                "http_reads": {"policy": "optional same-host origin and exact MD text-file allowlist",
                               "scope": "read_file, collect_logs and file-based samples; no listing, backup or write fallback",
                               "redirects": "refused", "environment_proxies": "disabled"},
                "native_access": {"operations": ["core.controller.status", "core.controller.sample", "core.controller.programs",
                    "core.registers.read", "core.registers.update", "core.programs.inspect", "core.programs.deploy", "core.controller.reconnect"],
                    "provider": self.bridge._access(robot_id).native_provider if robot_id else "per configured robot",
                    "policy": "disabled by default; writes enabled plus allowlisted or all_supported target scope",
                    "write_scope": self.bridge.config.write_scope,
                    "program_source": "ASCII LS; native loader plus TP/LS readback; never selects or runs a program",
                    "confirmation": "core prepared plans and configured client confirmation",
                    "pr_verification": "axis absolute tolerance 0.0001 mm/degrees; metadata exact"},
                "offline_edits": ["comment", "numeric_register", "position_register"],
                "file_exchange": {"read_captured_file_max_range": 65536,
                                  "stage_file_max_bytes": min(1048576, self.bridge.config.max_file_bytes),
                                  "staging": "base64 plus exact SHA-256; generated source_dir-relative path; no controller contact"},
                "connection_refresh": {"available": self.bridge.config_path is not None,
                                       "source": "bound local configuration file only",
                                       "policy_changes": "refused", "pending_plans": "invalidated on endpoint changes",
                                       "active_operations_and_jobs": "refused; finish first"},
                "feature_extensions": {**self.bridge.extensions.status(),
                                       "catalog": "list_operations and describe_operation",
                                       "reload": "reload_extensions; fixed MCP tool schemas",
                                       "code_trust": "trusted local releases; process isolation is not a sandbox"},
                "uploads": "existing exact-allowlisted application files; new exact-allowlisted TXT/CSV storage files outside MD:",
                "storage_file_creation": {"extensions": [".TXT", ".CSV"], "md_device": "refused",
                                          "atomic_no_clobber": False, "approval": "prepared change and configured client confirmation"},
                "live_comments": "ComSet with configured service origin, exact comment targets and listing readback; disabled by default",
                "approval_mode": self.bridge.config.approval_mode,
                "controller_access": {"read_only": self.bridge.config.read_only,
                                      "allow_writes": self.bridge.config.allow_writes,
                                      "scope": "controller writes; local backups, analysis and staged edits remain available",
                                      "apply_setting": "reconnect this MCP server after editing its local policy"},
                "native_controller_operations": {**{name: "unsupported" for name in (
                    "program_start_stop", "motion",
                    "frame_write", "io_forcing", "controller_mode_interlock", "dcs_write", "mastering_write")},
                    **{name: "requires configured native provider and exact target policy" for name in (
                        "program_file_creation", "load_compile", "register_value_write")}},
                "controller_identity": "configured profile; authenticate physical identity separately",
                "cross_process_lock_scope": "bridge clients sharing state_dir and the same configured host string",
                "robot_id": robot_id, "controller_contacted": False}

    def probe(self, robot_id: str) -> dict:
        self.bridge._access(robot_id)
        start = time.monotonic()
        with self.bridge._robot_lock(robot_id):
            self.bridge._audit("probe_started", robot_id=robot_id)
            try:
                roots = self.bridge._controller(robot_id).test_connection()
                result = {"reachable": True, "entries_by_root": roots}
            except Exception as exc:
                result = {"reachable": False, "error_type": type(exc).__name__}
            self.bridge._audit("probe_complete", robot_id=robot_id, **result)
        return {"robot_id": robot_id, **result, "elapsed_seconds": time.monotonic() - start,
                "running_state": "unknown", "physical_identity": "not_verified"}

    def start_fleet_backup(self, robot_ids: list[str], attempt_ascii=True) -> dict:
        if not 1 <= len(robot_ids) <= 8 or len(set(robot_ids)) != len(robot_ids):
            raise ValueError("Select 1-8 distinct configured robots")
        for identifier in robot_ids:
            self.bridge._access(identifier)
        jobs, unavailable = [], []
        for identifier in robot_ids:
            try:
                jobs.append(self.start_backup(identifier, attempt_ascii=attempt_ascii))
            except Exception as exc:
                unavailable.append({"robot_id": identifier, "error_type": type(exc).__name__})
        return {"jobs": jobs, "not_started": unavailable}

    def read_metadata(self, artifact_id: str, kind: str) -> dict:
        from fanuc_workbench.services.workspace_metadata import WorkspaceMetadataService
        return WorkspaceMetadataService.read(self.store.verify(artifact_id), kind)

    def write_metadata(self, artifact_id: str, kind: str, data) -> dict:
        from fanuc_workbench.services.workspace_metadata import WorkspaceMetadataService
        source = self.store.verify(artifact_id)
        destination = self.bridge._folder() / "workspace"
        shutil.copytree(source, destination)
        result = WorkspaceMetadataService.write(destination, kind, data)
        item = self.store.register(destination, "workspace", note=f"{kind} metadata derived from {artifact_id}")
        self.bridge._audit("metadata_saved", source_artifact=artifact_id, artifact_id=item["artifact_id"], kind=kind)
        return {**result, "artifact_id": item["artifact_id"], "source_artifact": artifact_id}

    def project(self, action: str, *, project_id: str = "", name: str = "",
                robot_id: str = "", workspace_id: str = "", metadata: dict | None = None) -> dict:
        from fanuc_workbench.services.project import RobotProject, ProjectRobot
        if action == "read":
            if self.store.get(project_id)["kind"] != "project":
                raise ValueError("Supply a project artifact ID")
            project = RobotProject.load(self.store.verify(project_id))
            return {"project_id": project_id, "name": project.name, "robots": serial(project.robots)}
        destination = self.bridge._folder() / "project"
        if action == "create":
            if not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
                raise ValueError("Project name requires 1-100 printable characters")
            project = RobotProject(name, destination)
            project.save()
        elif action == "add_workspace":
            if self.store.get(project_id)["kind"] != "project":
                raise ValueError("Supply a project artifact ID")
            access = self.bridge._access(robot_id)
            source = self.store.verify(workspace_id)
            shutil.copytree(self.store.verify(project_id), destination)
            project = RobotProject.load(destination)
            fields = metadata or {}
            if set(fields) - {"controller", "model", "software", "notes"} or any(
                not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 for c in value)
                for value in fields.values()
            ):
                raise ValueError("Project metadata supports bounded controller, model, software and notes text")
            robot = next((item for item in project.robots if item.name.casefold() == robot_id.casefold()), None)
            if robot is None:
                robot = ProjectRobot(robot_id, host=access.profile.host)
            for field, value in fields.items():
                setattr(robot, field, value)
            project.upsert_robot(robot)
            project.add_backup(robot_id, source, backup_name=workspace_id)
        else:
            raise ValueError("Project action must be create, read or add_workspace")
        item = self.store.register(destination, "project", note="Local project metadata and backup copies")
        self.bridge._audit("project_saved", artifact_id=item["artifact_id"], project_action=action)
        return {"project_id": item["artifact_id"], "name": project.name, "robots": serial(project.robots),
                "controller_written": False}

    def import_workspace(self, source_folder: str, note: str = "") -> dict:
        config = self.bridge.config
        source = confined(config.source_dir, source_folder)
        if not source.is_dir() or source.is_relative_to(config.state_dir.resolve()):
            raise ValueError("Import a source_dir-relative backup folder outside state_dir")
        paths, total = [], 0
        for path in source.rglob("*"):
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("Imported workspaces cannot contain links")
            if path.resolve().is_relative_to(config.state_dir.resolve()):
                raise ValueError("Import cannot include bridge state")
            if path.is_file():
                size = path.stat().st_size
                if size > config.max_file_bytes:
                    raise ValueError("Imported file exceeds max_file_bytes")
                total += size
                paths.append(path)
                if total > config.max_backup_bytes or len(paths) > config.max_backup_files:
                    raise ValueError("Imported workspace exceeds capture limits")
        if not paths:
            raise ValueError("Workspace contains no files")
        root = self.bridge._folder() / "workspace"
        root.mkdir()
        copied = 0
        for path in paths:
            destination = root / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as src, destination.open("xb") as dst:
                remaining = min(config.max_file_bytes, config.max_backup_bytes - copied)
                while chunk := src.read(min(65536, remaining + 1)):
                    if len(chunk) > remaining:
                        raise ValueError("Source grew during import; import aborted")
                    dst.write(chunk)
                    remaining -= len(chunk)
                    copied += len(chunk)
        item = self.store.register(root, "workspace", note=note)
        self.bridge._audit("workspace_imported", artifact_id=item["artifact_id"], files=len(paths))
        return {key: value for key, value in item.items() if key != "files"} | {"file_count": len(paths)}

    def start_backup(self, robot_id: str, paths: list[str] | None = None, max_depth: int = 8,
                     attempt_ascii: bool = True, *, job_id=None, task_binding=None) -> dict:
        with self.bridge._lock:
            return self._start_backup(robot_id, paths, max_depth, attempt_ascii, job_id=job_id, task_binding=task_binding)

    def _start_backup(self, robot_id: str, paths: list[str] | None, max_depth: int,
                      attempt_ascii: bool, *, job_id=None, task_binding=None) -> dict:
        access = self.bridge._access(robot_id)
        if not 0 <= max_depth <= 16:
            raise ValueError("max_depth must be between 0 and 16")
        if paths is not None:
            if not 1 <= len(paths) <= self.bridge.config.max_backup_files:
                raise ValueError("Selected path list is empty or exceeds the file limit")
            paths = [self.bridge._path(robot_id, path) for path in paths]

        def work(context):
            folder = self.bridge._folder()
            self.bridge._audit("backup_started", job_id=context.identifier, robot_id=robot_id)
            with self.bridge._robot_lock(robot_id, timeout=self.bridge.config.robot_lock_timeout_seconds,
                                         cancel_event=context.cancel):
                context.check()
                result = None
                try:
                    root = BackupService(ScopedReadBackend(self.bridge, robot_id), access.connection_profile()).run(
                        folder, max_depth=max_depth, attempt_ascii=attempt_ascii, selected_paths=paths,
                        maximum_file_bytes=self.bridge.config.max_file_bytes,
                        maximum_total_bytes=self.bridge.config.max_backup_bytes,
                        maximum_files=self.bridge.config.max_backup_files,
                        checkpoint=context.check, validate_path=lambda p: self.bridge._path(robot_id, p),
                        redact_errors=True, progress=lambda info: context.progress(info.get("message", "Capturing"),
                            completed=info.get("completed_files"), total=info.get("total_files"), stage=info.get("phase")))
                    manifest = json.loads((root / "backup_manifest.json").read_text(encoding="utf-8"))
                    result = {"complete": not manifest["warnings"] and not any(row.get("error") for row in manifest["records"]),
                              "warnings": manifest["warnings"], "records": len(manifest["records"])}
                finally:
                    candidates = list(folder.glob("*/backup_manifest.json"))
                    if candidates:
                        item = self.store.register(candidates[0].parent, "workspace", robot_id=robot_id,
                                                   note="Full/selected robot capture; inspect backup manifest for completeness")
                        self.jobs.update(context.identifier, artifact_id=item["artifact_id"])
                        if result is not None:
                            result["artifact_id"] = item["artifact_id"]
            self.bridge._audit("backup_finished", job_id=context.identifier, robot_id=robot_id, **result)
            return result

        return self.jobs.start("backup", robot_id, work, job_id=job_id, task_binding=task_binding)

    def analyze(self, artifact_id: str, kind: str, offset=0, limit=100, query="") -> dict:
        return self.bridge.extensions.legacy_analysis(artifact_id, kind, offset, limit, query)

    def compare(self, before: str, after: str, offset=0, limit=100) -> dict:
        return WorkspaceService.compare(self.store.verify(before), self.store.verify(after), offset, limit)

    def edit(self, artifact_id: str, relative: str, kind: str, target: str, value: dict) -> dict:
        source = self.store.file(artifact_id, relative)
        name = uuid.uuid4().hex + "/" + source.name
        destination = confined(self.bridge.config.source_dir, name)
        result = WorkspaceService.edit(source, destination, kind, target, value)
        self.bridge._audit("offline_edit", artifact_id=artifact_id, source_file=name, kind=kind, target=target)
        return {**result, "source_file": name, "original_artifact_id": artifact_id,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}

    @staticmethod
    def _watch_specs(expressions):
        if not 1 <= len(expressions) <= 100 or any(not WATCH_RE.fullmatch(item) for item in expressions):
            raise ValueError("Supply 1-100 supported watch expressions such as R[1], PR[1] or DI[1]")
        for item in expressions:
            match = WATCH_RE.fullmatch(item)
            if int(match.group(2)) < 1 or (match.group(3) and (match.group(1).upper() != "PR" or int(match.group(3)) < 1)):
                raise ValueError("Watch indices must be positive; only PR supports group,index")
        return [WatchSpec(item) for item in expressions]

    def _sample_clock(self, robot_id):
        host = self.bridge._access(robot_id).profile.host.casefold()
        key = hashlib.sha256(host.encode()).hexdigest()
        return self.bridge.config.state_dir / "sample-clocks" / (key + ".json")

    def _last_sample_time(self, robot_id):
        path = self._sample_clock(robot_id)
        if not path.exists():
            return 0.0
        try:
            value = float(json.loads(path.read_text(encoding="utf-8"))["monotonic"])
            if not math.isfinite(value):
                raise ValueError()
            return value if value <= time.monotonic() else 0.0
        except (ValueError, KeyError):
            raise ValueError("Sampling clock is unreadable; inspect bridge state") from None

    def sample(self, robot_id: str, expressions: list[str], *, checkpoint=None) -> dict:
        specs = self._watch_specs(expressions)
        access = self.bridge._access(robot_id)
        needed = set()
        for spec in specs:
            typ = WATCH_RE.fullmatch(spec.expression).group(1).upper()
            needed.add("NUMREG.VA" if typ == "R" else "POSREG.VA" if typ == "PR" else "IOSTATE.DG")
        with self.bridge._robot_lock(robot_id):
            elapsed = time.monotonic() - self._last_sample_time(robot_id)
            if elapsed < 2:
                raise ValueError("Live samples must be at least two seconds apart")
            self._last_sample[robot_id] = time.monotonic()
            atomic_json(self._sample_clock(robot_id), {"monotonic": self._last_sample[robot_id]})
            if access.sample_provider == "snpx":
                return self.bridge.snpx.sample(robot_id, [spec.expression for spec in specs], checkpoint=checkpoint)
            if access.native_provider == "frrobot":
                if checkpoint:
                    checkpoint()
                data = self.bridge.native.sample(robot_id, [spec.expression for spec in specs])
                values = {name: (value.get("values") if value.get("kind") == "PR" else value.get("value"))
                          for name, value in data["values"].items()}
                return {**data, "values": values, "unavailable_files": [], "source": "native FRRobot TCP API"}
            folder = self.bridge._folder()
            unavailable, transports = [], {}
            for name in sorted(needed):
                http_files = [value for value in access.http_read_files
                              if value.rsplit(':', 1)[-1].casefold() == name.casefold()]
                remotes = http_files if access.http_read_url and http_files else [
                    root + name if root.endswith(':') else root.rstrip('/') + '/' + name
                    for root in access.profile.roots]
                for remote in remotes:
                    if checkpoint:
                        checkpoint()
                    try:
                        transports[name] = self.bridge._download_read(
                            robot_id, self.bridge._path(robot_id, remote), folder / name)
                        break
                    except Exception:
                        continue
                else:
                    unavailable.append(name)
            sample = WorkspaceSampleProvider(folder).sample(specs)
            atomic_json(folder / "sample.json", {**serial(sample), "file_transports": transports})
            artifact = self.store.register(folder, "sample", robot_id=robot_id)
            self.bridge._audit("sample_captured", robot_id=robot_id, artifact_id=artifact["artifact_id"])
            return {**serial(sample), "artifact_id": artifact["artifact_id"], "unavailable_files": unavailable,
                    "capture_is_atomic": False, "source": "controller-generated file evidence",
                    "file_transports": transports}

    def start_watch(self, robot_id: str, expressions: list[str], samples: int = 10,
                    interval_seconds: float = 2.0, cycle_signal: str = "", *, job_id=None, task_binding=None) -> dict:
        with self.bridge._lock:
            return self._start_watch(robot_id, expressions, samples, interval_seconds, cycle_signal,
                                     job_id=job_id, task_binding=task_binding)

    def _start_watch(self, robot_id: str, expressions: list[str], samples: int,
                     interval_seconds: float, cycle_signal: str, *, job_id=None, task_binding=None) -> dict:
        self.bridge._access(robot_id)
        self._watch_specs(expressions)
        if not 1 <= samples <= 300 or not math.isfinite(interval_seconds) or not 2 <= interval_seconds <= 60:
            raise ValueError("Use 1-300 samples and a finite interval between 2 and 60 seconds")
        if cycle_signal and cycle_signal not in expressions:
            raise ValueError("cycle_signal must be one of the watch expressions")

        def work(context):
            trend, timeline, cycle = TrendRecorder(), TimelineRecorder(), CycleRecorder()
            folder = self.bridge._folder()
            sample_ids = []
            try:
                with self.bridge._robot_lock(robot_id, timeout=self.bridge.config.robot_lock_timeout_seconds,
                                             cancel_event=context.cancel):
                    for index in range(samples):
                        context.progress(f"Reading sample {index + 1}/{samples}", stage="sampling")
                        remaining = max(0, interval_seconds - (time.monotonic() - self._last_sample_time(robot_id)))
                        if context.cancel.wait(remaining):
                            context.check()
                        data = self.sample(robot_id, expressions, checkpoint=context.check)
                        sample_ids.append(data["artifact_id"])
                        context.progress(f"Captured sample {index + 1}/{samples}", completed=index + 1,
                                         total=samples, stage="samples_captured")
                        sample = Sample(data["timestamp"], data["values"], data["errors"])
                        trend.add(sample)
                        timeline.add(sample)
                        if cycle_signal:
                            cycle.add_sample_state(sample, cycle_signal)
            finally:
                trend.export_csv(folder / "trend.csv")
                timeline.export_csv(folder / "timeline.csv")
                cycle.export_json(folder / "cycle.json")
                atomic_json(folder / "recording.json", {"samples": serial(trend.samples), "events": serial(timeline.events),
                                                        "cycle": serial(cycle.events), "sample_artifacts": sample_ids,
                                                        "profile_seconds": profile_cycle(cycle.events)})
                artifact = self.store.register(folder, "recording", robot_id=robot_id)
                self.jobs.update(context.identifier, artifact_id=artifact["artifact_id"])
            return {"artifact_id": artifact["artifact_id"], "samples": len(trend.samples), "profile_seconds": profile_cycle(cycle.events)}

        return self.jobs.start("watch", robot_id, work, job_id=job_id, task_binding=task_binding)

    def snapshot(self, workspace_id: str, note: str = "") -> dict:
        root = self.store.verify(workspace_id)
        destination = self.bridge._folder()
        path = capture_snapshot(root, destination, note=note[:1000])
        item = self.store.register(destination, "snapshot", note=note)
        return {"artifact_id": item["artifact_id"], "file": path.name}

    def compare_snapshots(self, before: str, after: str) -> dict:
        def selected(identifier):
            item = self.store.get(identifier)
            if item["kind"] != "snapshot" or len(item["files"]) != 1:
                raise ValueError("Supply snapshot artifact IDs")
            return self.store.file(identifier, item["files"][0]["path"])
        return page(compare_snapshots(selected(before), selected(after)))

    def prepare_restore(self, robot_id: str, artifact_id: str, relative: str,
                        remote: str, expected_sha256: str) -> dict:
        self.bridge._path(robot_id, remote, write=True)
        source = self.store.file(artifact_id, relative)
        staged_name = uuid.uuid4().hex + "/" + source.name
        destination = confined(self.bridge.config.source_dir, staged_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        result = self.bridge.prepare_upload(robot_id, remote, staged_name, expected_sha256)
        return {**result, "restore_source_artifact": artifact_id, "restore_source_file": relative}

    def audit(self, offset=0, limit=100) -> dict:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Use offset >= 0 and limit between 1 and 500")
        path = self.bridge.config.state_dir / "audit.jsonl"
        rows = []
        total = 0
        if path.exists():
            with self.bridge._audit_lock, self.bridge.audit_locks.acquire("journal", timeout=15), path.open(encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    total = index + 1
                    if offset <= index < offset + limit:
                        rows.append(json.loads(line))
        return {"items": rows, "total": total, "offset": offset}

    def inspect_program(self, artifact_id: str, relative: str) -> dict:
        from fanuc_workbench.services.xref import validate_ls, program_flow
        root = self.store.verify(artifact_id)
        path = self.store.file(artifact_id, relative)
        if path.suffix.upper() != ".LS":
            raise ValueError("Program inspection requires an LS listing")
        return {"validation": validate_ls(path, root), "flow": program_flow(path)[:500],
                "compiler_acceptance": "not_verified"}

    def make_package(self, artifact_id: str, kind: str, files: list[str] | None = None) -> dict:
        root = self.store.verify(artifact_id)
        destination = self.bridge._folder()
        if kind == "documentation":
            from fanuc_workbench.services.documentation import export_all
            export_all(root, destination)
        elif kind == "roboguide":
            from fanuc_workbench.services.roboguide import stage_backup_for_roboguide
            stage_backup_for_roboguide(root, destination)
        elif kind == "baseline":
            from fanuc_workbench.services.commissioning import create_baseline
            create_baseline(root, destination)
        elif kind == "deployment":
            if not files or len(files) > self.bridge.config.max_backup_files:
                raise ValueError("Deployment packages require an explicit bounded file list")
            manifest = []
            for relative in files:
                source = self.store.file(artifact_id, relative)
                target = confined(destination, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise ValueError("Duplicate deployment file")
                shutil.copyfile(source, target)
                manifest.append({"file": relative, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            atomic_json(destination / "deployment_manifest.json", {"source_artifact": artifact_id, "files": manifest,
                                                                  "controller_acceptance": "not_verified"})
        else:
            raise ValueError("Package kind must be documentation, roboguide, baseline or deployment")
        item = self.store.register(destination, kind, note="Offline package; no controller acceptance implied")
        self.bridge._audit("package_created", artifact_id=item["artifact_id"], kind=kind, source_artifact=artifact_id)
        return {"artifact_id": item["artifact_id"], "file_count": len(item["files"]), "controller_written": False}

    def analyze_recording(self, artifact_id: str, kind: str = "cycle_profile", workspace_id: str = "",
                          mapping: list[dict] | None = None, window_seconds: float = 2.0) -> dict:
        from fanuc_workbench.services.monitoring import TimelineEvent, CycleEvent
        from fanuc_workbench.services.handshake import HandshakeSignal, analyze_handshake
        from fanuc_workbench.services.diagnostics import alarm_intelligence
        if self.store.get(artifact_id)["kind"] != "recording":
            raise ValueError("Supply a recording artifact ID")
        if not math.isfinite(window_seconds) or not 0 < window_seconds <= 120:
            raise ValueError("window_seconds must be finite and in (0, 120]")
        data = json.loads(self.store.file(artifact_id, "recording.json").read_text(encoding="utf-8"))
        events = [TimelineEvent(**item) for item in data["events"]]
        if kind == "cycle_profile":
            return {"profile_seconds": profile_cycle([CycleEvent(**item) for item in data["cycle"]])}
        if kind == "handshake":
            if not mapping or len(mapping) > 100:
                raise ValueError("Supply 1-100 handshake mappings")
            rows = [HandshakeSignal(**item) for item in mapping]
            return page(analyze_handshake(events, rows, window_seconds))
        if kind == "alarm_correlation":
            return page(alarm_intelligence(self.store.verify(workspace_id), events, window_seconds))
        raise ValueError("Recording analysis must be cycle_profile, handshake or alarm_correlation")

    def compare_recordings(self, before: str, after: str) -> dict:
        from fanuc_workbench.services.monitoring import compare_cycle_profiles
        a = self.analyze_recording(before)["profile_seconds"]
        b = self.analyze_recording(after)["profile_seconds"]
        return page(compare_cycle_profiles(a, b))

    def bundle_recording(self, artifact_id: str, sample_offset: int = 0, sample_limit: int = 20) -> dict:
        from fanuc_workbench.bridge.recording_bundle import bundle_recording
        return bundle_recording(self.bridge, artifact_id, sample_offset, sample_limit)

    @staticmethod
    def transform_pose(pose: list[float], old_frame: list[float], new_frame: list[float]) -> dict:
        from fanuc_workbench.services.transforms import transform_pose_between_frames
        for values in (pose, old_frame, new_frame):
            if len(values) != 6 or any(not math.isfinite(value) for value in values):
                raise ValueError("Poses require six finite X,Y,Z,W,P,R values")
        return {"pose": transform_pose_between_frames(pose, old_frame, new_frame),
                "convention": "Rz(R) Ry(P) Rx(W)", "controller_written": False, "physical_validation": "pending"}
