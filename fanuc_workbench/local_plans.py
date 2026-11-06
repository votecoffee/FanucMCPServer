"""Durable one-shot review state for the existing typed workbench handlers."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from fanuc_workbench.bridge.comments import CommentPlan
from fanuc_workbench.bridge.extension_release import read_json
from fanuc_workbench.bridge.native import NativePlan
from fanuc_workbench.bridge.service import UploadPlan
from fanuc_workbench.bridge.storage import ArtifactStore, RobotLocks, atomic_json
from fanuc_workbench.services.local_native import document_hash
from fanuc_workbench.services.native_values import canonical


def exact_bytes(path: Path, limit: int, checksum: str) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit or hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("Preserved bytes differ from the reviewed SHA-256")
    return data


class LocalPlans:
    def __init__(self, bridge, config_sha256):
        self.bridge = bridge
        self.root = bridge.config.state_dir / "cli-plans"
        self.config_sha256 = config_sha256
        self.locks = RobotLocks(self.root / "locks")

    def _file(self, identifier, filename):
        return self.root / ArtifactStore.check_id(identifier) / filename

    def _evidence(self, value):
        path = Path(value).resolve(strict=True)
        if not path.is_relative_to(self.bridge.config.state_dir.resolve()):
            raise ValueError("Evidence must remain inside this profile's state directory")
        return path

    def bind_native_reference(self, robot_id, checksum, reference_path):
        path = self._evidence(reference_path)
        if path.name != "native_capture.json":
            raise ValueError("Supply the native_capture.json path returned by the read")
        raw = exact_bytes(path, 1024 * 1024, checksum)
        value = json.loads(raw)
        if value["robot_id"] != robot_id or value["kind"] not in {"register", "program"}:
            raise ValueError("Native evidence belongs to a different target")
        self.bridge.native.receipts[(robot_id, value["kind"], value["target"], checksum)] = (path, raw)

    def save(self, result, native_validation=None):
        identifier = result["plan_id"]
        bridge = self.bridge
        if native_validation is not None:
            kind = "compile"
            plan, proposal, expires = native_validation.plans[identifier]
            fields = {"plan": plan}
        elif identifier in bridge._plans:
            kind, plan = "upload", bridge._plans[identifier]
            proposal, expires = plan.proposed_sha256, plan.expires
            fields = {name: getattr(plan, name) for name in ("robot_id", "remote", "original_sha256")}
            fields.update(folder=str(plan.folder), proposed_path=result["proposed_path"])
        elif identifier in bridge.comments.plans:
            kind, plan = "comment", bridge.comments.plans[identifier]
            fields = asdict(plan)
            proposal, expires = fields.pop("proposed_sha256"), fields.pop("expires")
        else:
            kind, plan = "native", bridge.native.plans[identifier]
            proposal, expires = plan.sha256, plan.expires
            fields = {"proposal": json.loads(plan.payload), "folder": str(plan.folder)}
        record = {"version": 1, "plan_id": identifier, "kind": kind,
                  "config_sha256": self.config_sha256, "approved_sha256": proposal,
                  "expires_unix": time.time() + max(0, expires - time.monotonic()),
                  "fields": fields, "review": result}
        checksum = document_hash(record)
        self._file(identifier, "plan.json").parent.mkdir(parents=True, exist_ok=False)
        atomic_json(self._file(identifier, "plan.json"), record)
        self._receipt(identifier, "prepared")
        return {**result, "review_sha256": checksum, "expires_unix": record["expires_unix"]}

    def _receipt(self, identifier, status, **fields):
        receipt = {"plan_id": identifier, "status": status, **fields}
        atomic_json(self._file(identifier, "receipt.json"), receipt)
        return receipt

    def status(self, plan_id):
        record = read_json(self._file(plan_id, "plan.json"), 16 * 1024 * 1024)
        receipt = read_json(self._file(plan_id, "receipt.json"), 16 * 1024 * 1024)
        return {**receipt, "review_sha256": document_hash(record), "review": record["review"],
                "expires_unix": record["expires_unix"],
                "current_policy": record["config_sha256"] == self.config_sha256,
                "requires_inspection": receipt["status"] in {"dispatching", "outcome_unverified"}}

    def _load(self, identifier, review_sha256):
        record = read_json(self._file(identifier, "plan.json"), 16 * 1024 * 1024)
        if record["plan_id"] != identifier or document_hash(record) != review_sha256:
            raise ValueError("The full review SHA-256 does not match the retained plan")
        if record["config_sha256"] != self.config_sha256:
            raise ValueError("The operator's configuration changed; prepare a new plan")
        if read_json(self._file(identifier, "receipt.json"))["status"] != "prepared":
            raise ValueError("This plan is consumed; inspect change.status before any new proposal")
        return record

    def cancel(self, plan_id, review_sha256):
        with self.locks.acquire("plan:" + ArtifactStore.check_id(plan_id)):
            record = self._load(plan_id, review_sha256)
            if record["kind"] == "compile":
                atomic_json(self.bridge.config.state_dir / "native-validation" / plan_id / "receipt.json",
                            {"status": "discarded", "plan_sha256": record["approved_sha256"]})
            return self._receipt(plan_id, "cancelled")

    def _restore(self, record):
        bridge = self.bridge
        identifier, kind, checksum = record["plan_id"], record["kind"], record["approved_sha256"]
        fields = dict(record["fields"])
        remaining = record["expires_unix"] - time.time()
        if not 0 < remaining <= bridge.config.plan_ttl_seconds + 1:
            raise ValueError("Plan expired or the system clock changed; prepare again")
        expires = time.monotonic() + remaining
        if kind == "comment":
            bridge.comments.plans[identifier] = CommentPlan(**fields, proposed_sha256=checksum, expires=expires)
            return fields["robot_id"]
        if kind == "upload":
            payload = exact_bytes(self._evidence(fields.pop("proposed_path")), bridge.config.max_file_bytes, checksum)
            fields["folder"] = self._evidence(fields["folder"])
            bridge._plans[identifier] = UploadPlan(**fields, proposed_sha256=checksum, payload=payload, expires=expires)
            return fields["robot_id"]
        proposal = fields["proposal"]
        payload = canonical(proposal)
        if hashlib.sha256(payload).hexdigest() != checksum:
            raise ValueError("Retained native proposal SHA-256 differs")
        folder = self._evidence(fields["folder"])
        source = None
        if proposal["kind"] == "program":
            source = exact_bytes(self._evidence(folder / ("proposed_" + proposal["target"] + ".LS")),
                                 1024 * 1024, proposal["proposed"]["source_sha256"])
        bridge.native.plans[identifier] = NativePlan(payload, checksum, expires, folder, source)
        return proposal["robot_id"]

    def _hold(self, robot_id):
        host = self.bridge._access(robot_id).profile.host.casefold()
        return self.root / "holds" / (hashlib.sha256(host.encode()).hexdigest() + ".json")

    @contextmanager
    def native_scope(self, robot_id):
        import os
        from fanuc_workbench.bridge.process_identity import current_creation_time

        host = self.bridge._access(robot_id).profile.host.casefold()
        key = hashlib.sha256(host.encode()).hexdigest()
        receipt = self.root / "native-targets" / (key + ".json")
        with self.locks.acquire("native:" + key):
            if receipt.exists():
                raise ValueError(f"Native target cleanup requires inspection of {receipt}")
            atomic_json(receipt, {"robot_id": robot_id, "host": host, "status": "exit_unverified",
                                  "owner_pid": os.getpid(), "owner_created": current_creation_time()})
            try:
                yield
            finally:
                from fanuc_workbench.bridge.owned_workers import workers
                self.bridge.native.close()
                if any(row["kind"] == "native-worker" for row in workers.snapshot()["resources"]):
                    raise RuntimeError("Native target worker exit remains unverified; inspect the retained ownership")
                receipt.unlink()

    def _check_hold(self, robot_id):
        hold = self._hold(robot_id)
        if hold.exists():
            prior = read_json(hold)
            raise ValueError(f"Controller has an unresolved outcome for plan {prior['plan_id']}; inspect its receipt")
        return hold

    def reconcile(self, plan_id, review_sha256, evidence_note, confirm):
        with self.locks.acquire("plan:" + ArtifactStore.check_id(plan_id)):
            record = read_json(self._file(plan_id, "plan.json"), 16 * 1024 * 1024)
            if document_hash(record) != review_sha256 or record["config_sha256"] != self.config_sha256:
                raise ValueError("Use the original profile and exact retained review SHA-256")
            if not 10 <= len(evidence_note.strip()) <= 1000:
                raise ValueError("Record an operator inspection finding or evidence locator")
            receipt = read_json(self._file(plan_id, "receipt.json"), 16 * 1024 * 1024)
            robot = receipt.get("robot_id") or record["fields"].get("robot_id")
            if not robot:
                robot = record["fields"].get("proposal", {}).get("robot_id")
            if not robot:
                raise ValueError("This plan has no controller hold")
            hold = self._hold(robot)
            native_hold = self.root / "native-targets" / hold.name
            with self.bridge._robot_lock(robot):
                if not hold.is_file() or read_json(hold)["plan_id"] != plan_id:
                    raise ValueError("This plan has no matching controller hold")
                if native_hold.exists():
                    raise ValueError("Resolve the retained native worker ownership before controller reconciliation")
                confirm(plan_id, evidence_note)
                reconciled = self._receipt(plan_id, "operator_reconciled", previous=receipt,
                                           robot_id=robot, evidence_note=evidence_note, never_replay=True)
                hold.unlink()
                return reconciled

    def commit(self, plan_id, review_sha256, approved_sha256, confirm):
        bridge = self.bridge
        with self.locks.acquire("plan:" + ArtifactStore.check_id(plan_id)):
            record = self._load(plan_id, review_sha256)
            if record["kind"] == "compile" or approved_sha256 != record["approved_sha256"]:
                raise ValueError("Use the correct commit operation and exact proposal SHA-256")
            if bridge.config.approval_mode == "elicitation":
                confirm(record)
            robot = self._restore(record)
            scope = self.native_scope(robot) if record["kind"] == "native" else nullcontext()
            with scope, bridge._robot_lock(robot):
                hold = self._check_hold(robot)
                started = False
                prior_audit = bridge._audit

                def audit(action, **fields):
                    nonlocal started
                    prior_audit(action, **fields)
                    if action in {"upload_started", "comment_started", "native_commit_started"} and fields.get("plan_id") == plan_id:
                        atomic_json(hold, {"plan_id": plan_id, "robot_id": robot})
                        started = True
                        self._receipt(plan_id, "dispatching", robot_id=robot, never_replay=True)

                self._receipt(plan_id, "checking_preflight", robot_id=robot, never_replay=True)
                bridge._audit = audit
                try:
                    result = bridge.commit_change(plan_id, approved_sha256, client_approved=True)
                    bridge.native.close()
                    self._receipt(plan_id, "verified", result=result)
                    hold.unlink(missing_ok=True)
                    return result
                except BaseException as exc:
                    self._receipt(plan_id, "outcome_unverified" if started else "refused",
                                  robot_id=robot, error_type=type(exc).__name__, never_replay=True)
                    raise
                finally:
                    bridge._audit = prior_audit

    def run_compile(self, validator, plan_id, review_sha256, expected_plan_sha256):
        with self.locks.acquire("plan:" + ArtifactStore.check_id(plan_id)):
            record = self._load(plan_id, review_sha256)
            if record["kind"] != "compile" or expected_plan_sha256 != record["approved_sha256"]:
                raise ValueError("Supply the exact reviewed compilation plan hashes")
            remaining = record["expires_unix"] - time.time()
            if not 0 < remaining <= self.bridge.config.plan_ttl_seconds + 1:
                raise ValueError("Compilation review expired or system clock changed")
            self._receipt(plan_id, "dispatching", never_replay=True)
            validator.plans[plan_id] = (record["fields"]["plan"], expected_plan_sha256, time.monotonic() + remaining)
            try:
                result = validator.run(plan_id, expected_plan_sha256)
            except BaseException as exc:
                self._receipt(plan_id, "outcome_unverified", error_type=type(exc).__name__, never_replay=True)
                raise
            self._receipt(plan_id, "completed", result=result)
            return result
