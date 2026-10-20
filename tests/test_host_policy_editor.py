import json
import os

import pytest

from fanuc_workbench.bridge.policy_editor import HostRuntime, load_policy, review_policy, save_policy
from test_framework_host import app_publication


@pytest.fixture
def host_policy(app_publication):
    state, _, publication, _ = app_publication
    pointer = state / "runtime.json"
    return publication, HostRuntime.load(pointer), pointer


def test_host_settings_redact_tokens_and_preserve_original(host_policy):
    _, runtime, _ = host_policy
    path = runtime.state / "settings.json"
    snapshot = load_policy(path, "host_settings", runtime)
    document = snapshot.document
    old_token = document["token"]; new_token = "z" * 48
    document.update(max_request_body_bytes=33554432, port=18794, token=new_token)
    reviewed = review_policy(snapshot, document)
    assert old_token not in reviewed.diff and new_token not in reviewed.diff
    assert "new authentication token" in reviewed.diff
    saved, backup = save_policy(reviewed)
    assert saved.document == document and backup.read_bytes() == snapshot.raw


def test_host_settings_refuse_running_host_and_recovery_barrier(host_policy):
    from universal_tools.daemon import InstanceLock
    from universal_tools.models import ServiceManifest
    from universal_tools.ownership import OwnershipJournal
    publication, runtime, _ = host_policy
    snapshot = load_policy(runtime.state / "settings.json", "host_settings", runtime)
    document = snapshot.document; document["port"] = 18794
    reviewed = review_policy(snapshot, document)
    with InstanceLock(runtime.state / "broker.lock"):
        with pytest.raises(ValueError): save_policy(reviewed)
    assert snapshot.path.read_bytes() == snapshot.raw
    journal = OwnershipJournal(runtime.state / "ownership.sqlite3")
    service = json.loads(publication.read_bytes())["backends"][0]["service"]
    journal.reserve(ServiceManifest.model_validate(service), "fixture-owner", "a" * 48)
    before = journal.entries()
    with pytest.raises(ValueError, match="Reconcile retained"):
        save_policy(reviewed)
    assert snapshot.path.read_bytes() == snapshot.raw and journal.entries() == before


@pytest.mark.parametrize("invalid", [{"max_request_body_bytes": 0}, {"token": "short"}])
def test_host_settings_validator_refuses_invalid_controls(host_policy, invalid):
    _, runtime, _ = host_policy
    snapshot = load_policy(runtime.state / "settings.json", "host_settings", runtime)
    with pytest.raises(ValueError): review_policy(snapshot, {**snapshot.document, **invalid})
    assert snapshot.path.read_bytes() == snapshot.raw


def test_host_gui_has_no_broker_pane_and_runtime_changes_invalidate_review(host_policy):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTabWidget
    from fanuc_workbench.ui.safeguards_dialog import SafeguardsDialog
    app = QApplication.instance() or QApplication([])
    _, runtime, pointer = host_policy
    dialog = SafeguardsDialog(host_runtime_path=str(pointer))
    assert dialog.host_settings.snapshot is None
    tabs = dialog.findChild(QTabWidget)
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Robot policy", "App framework", "Host settings", "Built-in checks"]
    dialog.load_host()
    pane = dialog.host_settings
    assert pane.snapshot is not None, pane.status.text()
    pane.widgets[("port",)].setValue(18794)
    pane.review_changes(); assert pane.save_button.isEnabled(), pane.status.text()
    assert json.loads((runtime.state / "settings.json").read_bytes())["token"] not in pane.preview.toPlainText()
    pane.save_changes()
    assert json.loads((runtime.state / "settings.json").read_bytes())["port"] == 18794
    dialog.host_runtime_path.setText(str(pointer.with_name("another.json")))
    assert pane.snapshot is None and not pane.save_button.isEnabled()
    dialog.close()


def test_host_runtime_refuses_generic_registry_state(tmp_path):
    import sys
    (tmp_path / "settings.json").write_text("{}")
    pointer = tmp_path / "runtime.json"
    pointer.write_text(json.dumps({"python": sys.executable, "state": str(tmp_path)}))
    with pytest.raises((OSError, ValueError)):
        HostRuntime.load(pointer)
