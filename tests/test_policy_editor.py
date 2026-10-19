import copy
from dataclasses import fields
import json
from pathlib import Path

import pytest

from fanuc_workbench.bridge.config import BridgeConfig, RobotAccess
from fanuc_workbench.bridge.policy_editor import load_policy, review_policy, save_policy, _BROKER_CODE
from fanuc_workbench.bridge.policy_fields import (SERVER_FIELDS, PROFILE_FIELDS, CONNECTION_FIELDS,
    WRITE_FIELDS, HTTP_FIELDS, SNPX_FIELDS)
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.snpx import SnpxReadPolicy


@pytest.fixture
def editor_policy(tmp_path):
    path = tmp_path / "robot.local.json"
    document = {"state_dir": "state", "source_dir": "edits", "allow_writes": False,
        "approval_mode": "elicitation", "extension_metadata": {"preserve": [1, 2]},
        "robots": {"r1": {"profile": {"host": "192.0.2.1", "roots": ["md:", "ud1:"]},
                         "writable_files": ["md:MAIN.LS"], "future_metadata": {"value": "keep"}},
                   "r2": {"profile": {"host": "192.0.2.2"}}}}
    path.write_bytes(json.dumps(document, indent=2).replace("\n", "\r\n").encode() + b"\r\n")
    return path, document


def test_controls_cover_every_runtime_policy_field():
    assert {item.key for item in SERVER_FIELDS} == {item.name for item in fields(BridgeConfig)} - {"robots"}
    assert {item.key for item in PROFILE_FIELDS} == {item.name for item in fields(RobotProfile)} - {"password", "sftp_trust_unknown_host"}
    assert {item.key for item in (*CONNECTION_FIELDS, *WRITE_FIELDS, *HTTP_FIELDS)} == {
        item.name for item in fields(RobotAccess)} - {"profile", "snpx_read"}
    assert {item.key for item in SNPX_FIELDS} == {item.name for item in fields(SnpxReadPolicy)}
    compile(_BROKER_CODE, "policy broker helper", "exec")


def test_review_save_preserves_unknown_settings_and_exact_original(editor_policy):
    path, document = editor_policy
    original = path.read_bytes()
    snapshot = load_policy(path)
    draft = copy.deepcopy(document)
    draft.update(allow_writes=True, plan_ttl_seconds=12, approval_mode="chat", max_backup_bytes=2147483648,
                 max_concurrent_requests=6, max_pending_requests=31, robot_lock_timeout_seconds=4.25)
    draft["robots"]["r1"].update(comment_max_bytes=64, comment_encoding="utf-8",
        native_provider="frrobot", native_register_targets=["R[1]"], native_programs=["TEST"],
        http_read_url="http://192.0.2.1", http_read_files=["md:ERRALL.LS"],
        snpx_read={"io_targets": ["DI[1]", "GI[1]"], "word_ranges": [{"address": 1, "count": 2}], "io_word_formats": {"GI[1]": "u16"}})
    reviewed = review_policy(snapshot, draft)
    assert path.read_bytes() == original and "+  \"allow_writes\": true" in reviewed.diff
    assert snapshot.document == document  # Mutable UI draft cannot corrupt the source snapshot.
    saved, backup = save_policy(reviewed)
    assert backup.read_bytes() == original and saved.document == draft
    loaded = BridgeConfig.load(path)
    assert loaded.state_dir == path.parent / "state" and not loaded.state_dir.exists()
    assert loaded.robots["r1"].snpx_read.io_targets == ("DI[1]", "GI[1]")
    assert saved.document["robots"]["r2"] == document["robots"]["r2"]
    assert saved.document["extension_metadata"] == document["extension_metadata"]


def test_no_change_retains_bytes_and_creates_no_history(editor_policy):
    path, document = editor_policy
    original = path.read_bytes()
    saved, backup = save_policy(review_policy(load_policy(path), document))
    assert saved.raw == original and path.read_bytes() == original and backup is None
    assert not (path.parent / ".fanuc-policy-history").exists()


@pytest.mark.parametrize("phase", ["review", "save"])
def test_stale_policy_cannot_overwrite_external_edits(editor_policy, phase):
    path, document = editor_policy
    snapshot = load_policy(path)
    proposal = {**document, "plan_ttl_seconds": 15}
    reviewed = review_policy(snapshot, proposal)
    path.write_text(json.dumps({**document, "plan_ttl_seconds": 20}))
    changed = path.read_bytes()
    with pytest.raises(ValueError, match="changed since loading"):
        review_policy(snapshot, proposal) if phase == "review" else save_policy(reviewed)
    assert path.read_bytes() == changed and not (path.parent / ".fanuc-policy-history").exists()


@pytest.mark.parametrize("change", [
    {"plan_ttl_seconds": 0}, {"max_backup_bytes": 2147483649}, {"max_concurrent_requests": 17},
    {"max_pending_requests": -1}, {"approval_mode": "none"}, {"allow_writes": "false"},
    {"max_file_bytes": float("nan")},
])
def test_invalid_policy_never_replaces_source(editor_policy, change):
    path, document = editor_policy
    original = path.read_bytes()
    with pytest.raises(ValueError): review_policy(load_policy(path), {**document, **change})
    assert path.read_bytes() == original
