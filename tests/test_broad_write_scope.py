"""Operator-selected broad scope preserves structural and transaction checks."""
from dataclasses import replace
import json

import pytest

from fanuc_workbench.bridge.config import BridgeConfig
from test_bridge_operations import bridge_case
from test_native_workflow import native_case, reg_plan, program_plan
from test_robot_bridge import ORIGINAL, PROPOSED, digest


def broad(bridge):
    robots = {key: replace(access, writable_files=(), creatable_files=(), native_register_targets=(),
                          native_programs=(), comment_targets=()) for key, access in bridge.config.robots.items()}
    bridge.config = replace(bridge.config, write_scope="all_supported", robots=robots)
    (bridge.config.source_dir / "MAIN.LS").write_bytes(PROPOSED)


@pytest.mark.parametrize("scope", [None, True, 1, [], {}, "*", "all"])
def test_invalid_scope_cannot_silently_enable_broad_access(tmp_path, scope):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"state_dir": "state", "source_dir": "edits", "robots": {}, "write_scope": scope}))
    with pytest.raises(ValueError, match="write_scope"):
        BridgeConfig.load(path)


def test_default_is_exact_and_broad_scope_is_reported_without_controller_contact(bridge_case):
    bridge, robot, _ = bridge_case
    assert bridge.config.write_scope == "allowlisted"
    with pytest.raises(ValueError, match="explicitly listed"):
        bridge._path("r1", "md:OTHER.LS", write=True)
    broad(bridge)
    assert bridge._path("r1", "md:OTHER.LS", write=True) == "md:OTHER.LS"
    assert bridge.list_robots()["write_scope"] == "all_supported"
    assert bridge.operations.capabilities()["native_access"]["write_scope"] == "all_supported"
    assert not robot.connects


@pytest.mark.parametrize("target", ["md:DCS.LS", "md:SAFETY.LS", "md:SYSVARS.SV", "md:MASTER.TP",
    "md:OTHER.SV", "md:../OTHER.LS", "md:*.LS", "ud1:OTHER.LS"])
def test_broad_scope_still_refuses_protected_unsupported_and_outside_root_paths(bridge_case, target):
    bridge, robot, _ = bridge_case
    broad(bridge)
    with pytest.raises(ValueError):
        bridge._path("r1", target, write=True)
    assert robot.uploads == robot.connects == 0


def test_broad_scope_never_enables_md_creation_or_program_creation_on_storage(bridge_case):
    bridge, _, _ = bridge_case
    broad(bridge)
    access = replace(bridge.config.robots["r1"], profile=replace(bridge.config.robots["r1"].profile, roots=["md:", "ud1:"]))
    bridge.config = replace(bridge.config, robots={"r1": access})
    assert bridge._path("r1", "ud1:TRACE.CSV", create=True) == "ud1:TRACE.CSV"
    for path in ("md:NEW.TXT", "ud1:NEW.LS"):
        with pytest.raises(ValueError): bridge._path("r1", path, create=True)


def test_broad_file_write_still_needs_exact_approval_readback_and_single_use(bridge_case):
    bridge, robot, _ = bridge_case
    broad(bridge)
    bridge.config = replace(bridge.config, approval_mode="elicitation")
    plan = bridge.prepare_upload("r1", "md:MAIN.LS", "MAIN.LS", digest(ORIGINAL))
    with pytest.raises(ValueError): bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    with pytest.raises(ValueError): bridge.commit_upload(plan["plan_id"], "0" * 64, client_approved=True)
    assert robot.uploads == 0
    result = bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"], client_approved=True)
    assert result["status"] == "verified" and robot.files["md:MAIN.LS"] == PROPOSED
    with pytest.raises(ValueError): bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"], client_approved=True)
    assert robot.uploads == 1


@pytest.mark.parametrize("revoke", ["read_only", "scope"])
def test_broad_file_write_rechecks_operator_policy_before_commit(bridge_case, revoke):
    bridge, robot, _ = bridge_case
    broad(bridge)
    plan = bridge.prepare_upload("r1", "md:MAIN.LS", "MAIN.LS", digest(ORIGINAL))
    bridge.config = replace(bridge.config, **({"allow_writes": False} if revoke == "read_only" else {"write_scope": "allowlisted"}))
    with pytest.raises(ValueError): bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"], client_approved=True)
    assert robot.uploads == 0


def test_broad_native_writes_preserve_confirmation_and_provider_opt_in(native_case):
    bridge, _, api = native_case
    broad(bridge)
    plan = reg_plan(bridge)
    with pytest.raises(ValueError): bridge.commit_change(plan["plan_id"], plan["proposed_sha256"])
    assert api.writes == 0
    assert bridge.commit_change(plan["plan_id"], plan["proposed_sha256"], client_approved=True)["status"] == "verified"
    assert api.writes == 1
    for target in ("DO[1]", "R[*]", "PR[9,1]"):
        with pytest.raises(ValueError): bridge.native._access("r1", target=target)
    access = replace(bridge.config.robots["r1"], native_provider="disabled")
    bridge.config = replace(bridge.config, robots={"r1": access})
    with pytest.raises(ValueError, match="disabled"): bridge.native._access("r1", target="R[999]")


def test_broad_program_deployment_keeps_native_preflight_and_readback(native_case):
    bridge, _, api = native_case
    broad(bridge)
    plan = program_plan(bridge)
    assert not api.loads
    result = bridge.commit_change(plan["plan_id"], plan["proposed_sha256"], client_approved=True)
    assert result["status"] == "verified" and api.loads == 1
    assert "program_load_preflight" in api.calls
    for name in ("DCS", "MASTER", "SAFETY", "../MAIN", "*"):
        with pytest.raises(ValueError): bridge.native._access("r1", program=name)


def test_broad_comments_still_require_valid_target_origin_review_and_readback(bridge_case, monkeypatch):
    bridge, robot, _ = bridge_case
    broad(bridge)
    with pytest.raises(ValueError): bridge.comments._policy("r1", "md:NUMREG.VA", "R[1]")
    access = replace(bridge.config.robots["r1"], comment_service_url="http://192.0.2.1")
    bridge.config = replace(bridge.config, robots={"r1": access})
    for target in ("R[*]", "SYSTEM[1]", "R[0]"):
        with pytest.raises(ValueError): bridge.comments._policy("r1", "md:NUMREG.VA", target)
    captured = bridge.read_file("r1", "md:NUMREG.VA")
    plan = bridge.comments.prepare("r1", "md:NUMREG.VA", "R[1]", "New count", captured["sha256"])
    def update(*args, **kwargs):
        robot.files["md:NUMREG.VA"] = robot.files["md:NUMREG.VA"].replace(b"'Count'", b"'New count'")
    monkeypatch.setattr("fanuc_workbench.bridge.comments.set_live_comment", update)
    assert bridge.comments.commit(plan["plan_id"], plan["proposed_sha256"])["status"] == "verified"
