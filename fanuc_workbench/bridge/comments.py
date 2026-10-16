from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fanuc_workbench.parsers.comments import parse_comment_file
from fanuc_workbench.services.live_comments import target_for_record, set_live_comment


@dataclass(frozen=True)
class CommentPlan:
    robot_id: str
    remote: str
    target: str
    comment: str
    old_comment: str
    original_sha256: str
    proposed_sha256: str
    expires: float


class CommentChanges:
    def __init__(self, bridge):
        self.bridge = bridge
        self.plans: dict[str, CommentPlan] = {}

    def _policy(self, robot_id, remote, target):
        bridge = self.bridge
        access = bridge._access(robot_id)
        bridge._path(robot_id, remote)
        if not bridge.config.allow_writes or not access.comment_service_url or target not in access.comment_targets:
            raise ValueError("Live comments require writes enabled, a configured comment service URL and an exact comment target")
        name = remote.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if name.upper() not in {"NUMREG.VA", "POSREG.VA", "IOSTATE.DG"}:
            raise ValueError("Live comment changes require a supported controller-generated listing")
        return access, name

    @staticmethod
    def _record(capture, name, target):
        original = Path(capture["local_path"])
        named = original.parent / name.upper()
        shutil.copyfile(original, named)
        rows = [row for row in parse_comment_file(named) if row.identifier == target]
        if len(rows) != 1:
            raise ValueError("Comment target must identify exactly one record in the retrieved listing")
        target_for_record(rows[0])  # Enforce the existing supported ComSet contract.
        return rows[0]

    def prepare(self, robot_id, remote, target, comment, expected_sha256):
        bridge = self.bridge
        with bridge._lock, bridge._robot_lock(robot_id):
            _, name = self._policy(robot_id, remote, target)
            if not isinstance(comment, str) or len(comment) > 256 or any(ord(c) < 32 for c in comment):
                raise ValueError("Comment must contain at most 256 printable characters")
            capture = bridge.read_file(robot_id, remote)
            if capture["sha256"] != expected_sha256:
                raise ValueError("Controller listing changed; retrieve and review it again")
            record = self._record(capture, name, target)
            if comment == record.comment:
                raise ValueError("Comment is unchanged")
            change = {"robot_id": robot_id, "remote": remote, "target": target, "comment": comment,
                      "old_comment": record.comment, "original_sha256": expected_sha256}
            digest = hashlib.sha256(json.dumps(change, sort_keys=True).encode()).hexdigest()
            self.plans = {key: plan for key, plan in self.plans.items() if plan.expires > time.monotonic()}
            if len(self.plans) >= 64:
                raise ValueError("Too many pending comment changes")
            identifier = uuid.uuid4().hex
            self.plans[identifier] = CommentPlan(**change, proposed_sha256=digest,
                                                expires=time.monotonic() + bridge.config.plan_ttl_seconds)
            bridge._audit("comment_prepared", plan_id=identifier, robot_id=robot_id, target=target,
                          sha256=digest, backup_path=capture["local_path"])
            return {"plan_id": identifier, **change, "proposed_sha256": digest,
                    "backup_path": capture["local_path"], "status": "prepared_only", "requires_user_approval": True}

    def review(self, plan_id, approved_sha256):
        plan = self.plans.get(plan_id)
        if not plan or plan.expires <= time.monotonic() or plan.proposed_sha256 != approved_sha256:
            raise ValueError("Unknown, expired or mismatched comment plan")
        return {"robot_id": plan.robot_id, "target": plan.target, "old_comment": plan.old_comment,
                "new_comment": plan.comment, "sha256": plan.proposed_sha256}

    def commit(self, plan_id, approved_sha256, *, client_approved=False):
        bridge = self.bridge
        with bridge._lock:
            self.review(plan_id, approved_sha256)
            plan = self.plans[plan_id]
            access, name = self._policy(plan.robot_id, plan.remote, plan.target)
            if bridge.config.approval_mode == "elicitation" and not client_approved:
                raise ValueError("This bridge requires confirmation through the MCP client's elicitation interface")
            with bridge._robot_lock(plan.robot_id):
                self.plans.pop(plan_id)
                started = False
                try:
                    before = bridge.read_file(plan.robot_id, plan.remote)
                    if before["sha256"] != plan.original_sha256:
                        raise ValueError("Listing changed after review")
                    record = self._record(before, name, plan.target)
                    bridge._audit("comment_started", plan_id=plan_id, robot_id=plan.robot_id, target=plan.target)
                    started = True
                    set_live_comment(access.comment_service_url, record, plan.comment, timeout=access.profile.timeout)
                    after = bridge.read_file(plan.robot_id, plan.remote)
                    if self._record(after, name, plan.target).comment != plan.comment:
                        raise ValueError("Updated comment was not confirmed by controller listing readback")
                    bridge._audit("comment_verified", plan_id=plan_id, readback_path=after["local_path"])
                    return {"status": "verified", "plan_id": plan_id, "target": plan.target,
                            "backup_path": before["local_path"], "readback_path": after["local_path"],
                            "controller_value_written": False}
                except Exception as exc:
                    state = "uncertain" if started else "not_attempted"
                    bridge._audit("comment_failed", plan_id=plan_id, controller_state=state, error_type=type(exc).__name__)
                    raise RuntimeError(f"Comment change failed; controller write state: {state}. Inspect current comments; plan consumed.") from None
