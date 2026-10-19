import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fanuc_workbench.bridge.broker_server import service_manifest
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.policy_editor import BrokerRuntime, load_policy, review_policy, save_policy


@pytest.fixture
def broker_policy(tmp_path):
    pytest.importorskip("universal_tools.framework")
    state = tmp_path / "broker"
    subprocess.run([sys.executable, "-B", "-m", "universal_tools", "--state", str(state), "init", "--port", "18793"],
                   check=True, capture_output=True, timeout=30)
    config = tmp_path / "robot.local.json"
    config.write_text(json.dumps({"state_dir": "evidence", "source_dir": "edits", "robots": {}}))
    import asyncio
    with RobotBridge.from_config(config) as bridge:
        descriptor = asyncio.run(service_manifest(bridge, Path(sys.executable), config))
    path = state / "services/fanuc-mcp-server.json"; path.write_text(json.dumps(descriptor))
    pointer = tmp_path / "runtime.json"
    pointer.write_text(json.dumps({"python": sys.executable, "state": str(state)}))
    return path, BrokerRuntime.load(pointer), pointer


def test_broker_policy_permission_edits_preserve_transport_and_exact_backup(broker_policy):
    path, runtime, _ = broker_policy
    original = path.read_bytes()
    snapshot = load_policy(path, "broker", runtime)
    document = snapshot.document
    document.update(allow_writes=True, allowed_write_tools=["prepare_upload", "commit_upload"],
                    max_argument_bytes=2097152, timeout_seconds=80.0, lease_seconds=1800.0)
    saved, backup = save_policy(review_policy(snapshot, document))
    assert saved.document == document and backup.read_bytes() == original
    assert saved.document["transport"] == snapshot.document["transport"]
    assert not (runtime.state / "framework").exists()


def test_broker_policy_edit_refuses_running_daemon_and_recovery_barrier(broker_policy):
    from universal_tools.daemon import InstanceLock
    from universal_tools.models import ServiceManifest
    from universal_tools.ownership import OwnershipJournal
    path, runtime, _ = broker_policy
    snapshot = load_policy(path, "broker", runtime)
    document = snapshot.document; document["allow_writes"] = True
    reviewed = review_policy(snapshot, document)
    with InstanceLock(runtime.state / "broker.lock"):
        with pytest.raises(ValueError): save_policy(reviewed)
    assert path.read_bytes() == snapshot.raw and not (path.parent / ".fanuc-policy-history").exists()
    journal = OwnershipJournal(runtime.state / "ownership.sqlite3")
    journal.reserve(ServiceManifest.model_validate(snapshot.document), "fixture-owner", "a" * 48)
    before = journal.entries()
    with pytest.raises(ValueError, match="Reconcile retained"):
        save_policy(reviewed)
    assert path.read_bytes() == snapshot.raw and journal.entries() == before


def test_broker_global_controls_redact_tokens_and_preserve_original(broker_policy):
    _, runtime, _ = broker_policy
    path = runtime.state / "settings.json"
    snapshot = load_policy(path, "broker_limits", runtime)
    document = snapshot.document
    old_token = document["token"]; new_token = "z" * 48
    document.update(max_request_body_bytes=33554432, port=18794, token=new_token)
    reviewed = review_policy(snapshot, document)
    assert old_token not in reviewed.diff and new_token not in reviewed.diff
    assert "new authentication token" in reviewed.diff
    saved, backup = save_policy(reviewed)
    assert saved.document == document and backup.read_bytes() == snapshot.raw


@pytest.mark.parametrize("invalid", [{"max_argument_bytes": 67108865}, {"allowed_write_tools": ["bad name"]}])
def test_broker_runtime_validator_refuses_invalid_policy(broker_policy, invalid):
    path, runtime, _ = broker_policy
    snapshot = load_policy(path, "broker", runtime)
    with pytest.raises(ValueError): review_policy(snapshot, {**snapshot.document, **invalid})
    assert path.read_bytes() == snapshot.raw


def test_broker_gui_controls_and_runtime_changes_require_new_review(broker_policy):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from fanuc_workbench.ui.safeguards_dialog import SafeguardsDialog
    app = QApplication.instance() or QApplication([])
    path, runtime, pointer = broker_policy
    dialog = SafeguardsDialog(broker_runtime_path=str(pointer))
    assert dialog.broker_policy.snapshot is None  # Saved pointers do not execute a runtime automatically.
    dialog.load_broker()
    pane = dialog.broker_policy
    assert pane.snapshot is not None, pane.status.text()
    pane.widgets[("allow_writes",)].setChecked(True)
    pane.widgets[("allowed_write_tools",)].edit.setPlainText("prepare_upload\ncommit_upload")
    pane.review_changes(); assert pane.save_button.isEnabled(), pane.status.text()
    pane.save_changes()
    assert json.loads(path.read_text())["allowed_write_tools"] == ["prepare_upload", "commit_upload"]
    assert not dialog.broker_limits.preview.toPlainText()
    dialog.broker_limits.review_changes()
    assert json.loads((runtime.state / "settings.json").read_text())["token"] not in dialog.broker_limits.preview.toPlainText()
    dialog.broker_runtime_path.setText(str(pointer.with_name("another.json")))
    assert pane.snapshot is None and not pane.save_button.isEnabled()
    dialog.close()
