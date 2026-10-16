"""Core-owned native captures, frozen plans, approval, preflight and readback."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fanuc_workbench.bridge.storage import atomic_json, confined
from fanuc_workbench.services.native_controller import NativeControllerService
from fanuc_workbench.services.native_programs import validate_source, verify_listing
from fanuc_workbench.services.native_values import (IO, canonical, fingerprint, program_name,
                                                    propose_register, register_matches, register_target)


@dataclass(frozen=True)
class NativePlan:
    payload: bytes
    sha256: str
    expires: float
    folder: Path
    source: bytes | None = None


class NativeChanges:
    def __init__(self, bridge, factory=NativeControllerService):
        self.bridge, self.factory = bridge, factory
        self.clients, self.plans, self.receipts = {}, {}, {}
        self.client_lock = threading.RLock()

    def _access(self, robot_id, target=None, program=None):
        access = self.bridge._access(robot_id)
        if access.native_provider != "frrobot":
            raise ValueError("Native access is disabled; the operator must configure the frrobot provider")
        if target is not None:
            register_target(target)
            if not self.bridge.config.allow_writes or target not in access.native_register_targets:
                raise ValueError("Native register writes require writes enabled and an exact native_register_targets entry")
        if program is not None:
            program_name(program)
            self.bridge._path(robot_id, "md:" + program + ".TP")
            if not self.bridge.config.allow_writes or program not in access.native_programs:
                raise ValueError("Native deployment requires writes enabled and an exact native_programs entry")
        return access

    def _client(self, robot_id):
        access = self._access(robot_id)
        with self.client_lock:
            if robot_id not in self.clients:
                self.clients[robot_id] = self.factory(access.profile)
            return self.clients[robot_id]

    def _identity(self, robot_id):
        access = self._access(robot_id)
        return {"robot_id": robot_id, "host": access.profile.host, "provider": access.native_provider,
                "file_transport": access.profile.transport, "file_port": access.profile.port}

    def _capture(self, robot_id, kind, target, value, *, folder=None):
        snapshot = {**self._identity(robot_id), "kind": kind, "target": target, "value": value}
        data = canonical(snapshot)
        checksum = hashlib.sha256(data).hexdigest()
        folder = folder or self.bridge._folder()
        path = folder / "native_capture.json"
        path.write_bytes(data)
        self.receipts[(robot_id, kind, target, checksum)] = (path, data)
        self.bridge._audit("native_read", robot_id=robot_id, kind=kind, target=target, sha256=checksum)
        return {**snapshot, "sha256": checksum, "local_path": str(path)}

    def _reference(self, robot_id, kind, target, checksum):
        receipt = self.receipts.get((robot_id, kind, target, checksum))
        if receipt is None:
            raise ValueError("Retrieve this native target first and supply its exact returned SHA-256")
        path, data = receipt
        with path.open("rb") as stream:
            actual = stream.read(len(data) + 1)
        value = json.loads(data)
        if actual != data or any(value.get(key) != item for key, item in self._identity(robot_id).items()):
            raise ValueError("Native capture changed or belongs to an earlier endpoint")
        return value

    def read_register(self, robot_id, target):
        register_target(target)
        self._access(robot_id)
        with self.bridge._robot_lock(robot_id):
            value = self._client(robot_id).call("read_register", target=target)
            return self._capture(robot_id, "register", target, value)

    def status(self, robot_id, group=1, alarm_limit=20):
        if type(group) is not int or not 1 <= group <= 8 or type(alarm_limit) is not int or not 0 <= alarm_limit <= 100:
            raise ValueError("Use group 1-8 and alarm_limit 0-100")
        self._access(robot_id)
        with self.bridge._robot_lock(robot_id):
            result = self._client(robot_id).call("status", group=group, alarm_limit=alarm_limit)
            folder = self.bridge._folder()
            atomic_json(folder / "native_status.json", result)
            item = self.bridge.operations.store.register(folder, "native_status", robot_id=robot_id)
            self.bridge._audit("native_status", robot_id=robot_id, artifact_id=item["artifact_id"])
            return {**result, "robot_id": robot_id, "artifact_id": item["artifact_id"], "capture_is_atomic": False}

    def sample(self, robot_id, expressions):
        if not isinstance(expressions, list) or not 1 <= len(expressions) <= 100 or len(set(expressions)) != len(expressions):
            raise ValueError("Select 1-100 distinct native watch expressions")
        for expression in expressions:
            if not isinstance(expression, str):
                raise ValueError("Native watch expressions must be explicit strings")
            if not IO.fullmatch(expression):
                register_target(expression)
        self._access(robot_id)
        with self.bridge._robot_lock(robot_id):
            result = self._client(robot_id).call("sample", expressions=expressions)
            folder = self.bridge._folder()
            atomic_json(folder / "native_sample.json", result)
            item = self.bridge.operations.store.register(folder, "sample", robot_id=robot_id)
            self.bridge._audit("native_sample", robot_id=robot_id, artifact_id=item["artifact_id"])
            return {**result, "robot_id": robot_id, "artifact_id": item["artifact_id"], "capture_is_atomic": False}

    def programs(self, robot_id, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Use offset >= 0 and limit 1-500")
        self._access(robot_id)
        with self.bridge._robot_lock(robot_id):
            rows = self._client(robot_id).call("programs")
            rows.sort(key=lambda row: row["name"])
            return {"robot_id": robot_id, "items": rows[offset:offset+limit], "total": len(rows),
                    "offset": offset, "next_offset": offset+limit if offset+limit < len(rows) else None}

    def _program_state(self, robot_id, name, folder):
        rows = self._client(robot_id).call("programs")
        found = [row for row in rows if row["name"].upper() == name]
        if len(found) > 1:
            raise ValueError("Native program name is ambiguous")
        state = {"exists": bool(found), "program": found[0] if found else None}
        atomic_json(folder / "native_program_catalog.json", rows)
        # A successful complete listing distinguishes absence from a failed RETR.
        entries = self.bridge._controller(robot_id).list_files(self.bridge._path(robot_id, "md:"))
        if len(entries) > self.bridge.config.max_backup_files:
            raise ValueError("Program directory exceeds the configured capture limit")
        atomic_json(folder / "program_directory.json", {"device": "md:", "entries": [
            {"name": row.name, "is_dir": row.is_dir, "size": row.size} for row in entries]})
        present = any(row.name.upper() == name + ".TP" and not row.is_dir for row in entries)
        if bool(found) != present:
            raise ValueError("Native program catalog and compiled TP file disagree; inspect this target")
        if found:
            for extension in ["TP", "LS"]:
                path = folder / (name + "." + extension)
                self.bridge._controller(robot_id).download_file(self.bridge._path(robot_id, "md:" + path.name),
                    path, maximum=self.bridge.config.max_file_bytes)
                if not path.stat().st_size:
                    raise ValueError("Native program capture is empty")
                state[extension.lower() + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return state

    def read_program(self, robot_id, program):
        program_name(program)
        self._access(robot_id)
        with self.bridge._robot_lock(robot_id):
            folder = self.bridge._folder()
            state = self._program_state(robot_id, program, folder)
            return self._capture(robot_id, "program", program, state, folder=folder)

    def _freeze(self, robot_id, kind, target, before, proposed, folder, source=None):
        self.plans = {key: plan for key, plan in self.plans.items() if plan.expires > time.monotonic()}
        if len(self.plans) >= 64:
            raise ValueError("Too many pending native plans")
        payload = canonical({**self._identity(robot_id), "kind": kind, "target": target, "before": before,
                             "proposed": proposed})
        checksum = hashlib.sha256(payload).hexdigest()
        identifier = uuid.uuid4().hex
        plan = NativePlan(payload, checksum, time.monotonic()+self.bridge.config.plan_ttl_seconds, folder, source)
        atomic_json(folder / "native_plan.json", {"plan_id": identifier, "proposal": json.loads(payload), "sha256": checksum})
        self.bridge._audit("native_prepared", robot_id=robot_id, kind=kind, target=target, plan_id=identifier, sha256=checksum)
        self.plans[identifier] = plan
        return {"plan_id": identifier, "proposed_sha256": checksum, "proposal": json.loads(payload),
                "status": "prepared_only", "requires_user_approval": True, "controller_written": False,
                "review_path": str(folder / "native_plan.json")}

    def prepare_register(self, robot_id, target, change, expected_sha256):
        with self.bridge._lock, self.bridge._robot_lock(robot_id):
            self._access(robot_id, target=target)
            reference = self._reference(robot_id, "register", target, expected_sha256)
            before = self._client(robot_id).call("read_register", target=target)
            if before != reference["value"]:
                raise ValueError("Native register changed; retrieve and review it again")
            proposed = propose_register(before, change)
            folder = self.bridge._folder()
            self._capture(robot_id, "register", target, before, folder=folder)
            review = {"value": proposed, "verification": "exact" if before["kind"] == "R" else
                      "axis absolute tolerance 0.0001 mm/degrees; frame/tool/configuration/external axes exact"}
            return self._freeze(robot_id, "register", target, before, review, folder)

    def prepare_program(self, robot_id, source_file, expected_sha256):
        source = confined(self.bridge.config.source_dir, source_file)
        if source.suffix.upper() != ".LS":
            raise ValueError("Native program deployment currently accepts LS source only")
        with source.open("rb") as stream:
            data = stream.read(min(1024*1024, self.bridge.config.max_file_bytes)+1)
        proposal = validate_source(data)
        name = proposal["program"]
        with self.bridge._lock, self.bridge._robot_lock(robot_id):
            self._access(robot_id, program=name)
            reference = self._reference(robot_id, "program", name, expected_sha256)
            folder = self.bridge._folder()
            before = self._program_state(robot_id, name, folder)
            if before != reference["value"]:
                raise ValueError("Native program changed; retrieve and review it again")
            if before["exists"] and before["program"]["protection"] != 0:
                raise ValueError("Native program is protected; Workbench will not change protection")
            (folder / ("proposed_" + name + ".LS")).write_bytes(data)
            proposal.update(source_text=data.decode("ascii")[:32768], source_preview_truncated=len(data)>32768,
                operation="replace" if before["exists"] else "create", executed=False,
                verification="native loader acceptance, compiled TP capture and normalized LS readback comparison")
            return self._freeze(robot_id, "program", name, before, proposal, folder, data)

    def review(self, plan_id, approved_sha256):
        plan = self.plans.get(plan_id)
        if plan is None or plan.expires <= time.monotonic() or plan.sha256 != approved_sha256:
            raise ValueError("Unknown, expired or mismatched native plan")
        return {"plan_id": plan_id, "proposed_sha256": plan.sha256, "proposal": json.loads(plan.payload)}

    def cancel(self, plan_id):
        with self.bridge._lock:
            if plan_id not in self.plans:
                raise ValueError("Unknown or consumed native plan")
            self.bridge._audit("native_cancelled", plan_id=plan_id)
            self.plans.pop(plan_id)
            return {"plan_id": plan_id, "status": "cancelled"}

    def commit(self, plan_id, approved_sha256, *, client_approved=False):
        with self.bridge._lock:
            self.review(plan_id, approved_sha256)
            if self.bridge.config.approval_mode == "elicitation" and not client_approved:
                raise ValueError("This native change requires confirmation through the client's elicitation interface")
            plan = self.plans[plan_id]
            value = json.loads(plan.payload)
            robot_id, target = value["robot_id"], value["target"]
            if any(value.get(key) != item for key, item in self._identity(robot_id).items()):
                raise ValueError("Native plan belongs to an earlier endpoint")
            self._access(robot_id, **({"target": target} if value["kind"] == "register" else {"program": target}))
            with self.bridge._robot_lock(robot_id):
                folder = self.bridge._folder()
                if value["kind"] == "register":
                    before = self._client(robot_id).call("read_register", target=target)
                else:
                    before = self._program_state(robot_id, target, folder)
                if before != value["before"]:
                    raise ValueError("Native target changed after approval; retrieve and prepare again")
                atomic_json(folder / "preflight.json", {**self._identity(robot_id), "target": target, "value": before})
                # Audit failure prevents a vendor write and leaves the plan available.
                self.bridge._audit("native_commit_started", robot_id=robot_id, target=target,
                                   plan_id=plan_id, sha256=plan.sha256)
                self.plans.pop(plan_id)
                try:
                    if value["kind"] == "register":
                        result = self._client(robot_id).call("write_register", target=target,
                            expected=before, proposed=value["proposed"]["value"])
                        if not register_matches(result["value"], value["proposed"]["value"]):
                            raise RuntimeError("Native register readback does not match the approved proposal")
                        result["capture"] = self._capture(robot_id, "register", target, result["value"], folder=folder)
                    else:
                        (folder / "load").mkdir()
                        path = folder / "load" / (target + ".LS")
                        with path.open("xb") as stream:
                            stream.write(plan.source)
                            stream.flush()
                            os.fsync(stream.fileno())
                        if hashlib.sha256(path.read_bytes()).hexdigest() != value["proposed"]["source_sha256"]:
                            raise ValueError("Frozen program source changed")
                        loaded = self._client(robot_id).call("load_program", source=str(path), name=target,
                                                            expected_programs=before["program"])
                        after_folder = self.bridge._folder()
                        after = self._program_state(robot_id, target, after_folder)
                        if not after["exists"]:
                            raise RuntimeError("Native loader did not produce a compiled program")
                        comparison = verify_listing(plan.source, (after_folder / (target + ".LS")).read_bytes())
                        if not comparison["matches"]:
                            atomic_json(after_folder / "listing_verification.json", comparison)
                            raise RuntimeError("Program loaded but LS readback differs; inspect before further changes")
                        result = {"status": "verified", "native_loader": loaded, "listing_verification": comparison,
                                  "capture": self._capture(robot_id, "program", target, after, folder=after_folder), "executed": False}
                    atomic_json(folder / "readback.json", result)
                    self.bridge._audit("native_commit_verified", robot_id=robot_id, target=target, plan_id=plan_id)
                    return {**result, "plan_id": plan_id, "robot_id": robot_id, "target": target,
                            "proposed_sha256": plan.sha256, "evidence_path": str(folder)}
                except Exception as exc:
                    atomic_json(folder / "failure.json", {"error_type": type(exc).__name__, "state": "inspect_before_retry"})
                    self.bridge._audit("native_commit_failed", robot_id=robot_id, target=target, plan_id=plan_id,
                                       error_type=type(exc).__name__)
                    raise RuntimeError("Native change failed or verification is incomplete; plan consumed. Inspect preserved evidence and the controller before another change") from None

    def reconnect(self, robot_id):
        with self.bridge._lock, self.bridge._robot_lock(robot_id), self.client_lock:
            if self.bridge.operations.jobs.events:
                raise ValueError("Finish read jobs before reconnecting a native provider")
            self.bridge._audit("native_reconnected", robot_id=robot_id, invalidated_native_plans=len(self.plans))
            self.plans.clear()
            client = self.clients.pop(robot_id, None)
            if client:
                client.close()
            return self.status(robot_id)

    def close(self):
        with self.client_lock:
            clients, self.clients = list(self.clients.values()), {}
        for client in clients:
            client.close()
