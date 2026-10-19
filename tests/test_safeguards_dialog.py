import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.ui.safeguards_dialog import PolicyPane, SafeguardsDialog
from test_policy_editor import editor_policy


def test_gui_edits_limits_targets_and_snpx_with_review_and_backup(editor_policy):
    app = QApplication.instance() or QApplication([])
    path, original = editor_policy
    pane = PolicyPane("robot", str(path))
    assert pane.snapshot is not None and pane.draft == original
    pane.widgets[("max_backup_bytes",)].setValue(2147483648)
    pane.widgets[("plan_ttl_seconds",)].setValue(75)
    pane.widgets[("allow_writes",)].setChecked(True)
    pane.widgets[("approval_mode",)].setCurrentText("chat")
    prefix = ("robots", "r1")
    pane.widgets[prefix + ("native_provider",)].setCurrentText("frrobot")
    pane.widgets[prefix + ("native_register_targets",)].setPlainText("R[1]\nPR[1,1]")
    pane.snpx_enabled.setChecked(True)
    pane.widgets[prefix + ("snpx_read", "io_targets")].setPlainText("DI[1]\nGI[1]")
    pane.widgets[prefix + ("snpx_read", "io_word_formats")].setPlainText("GI[1]=u16")
    pane.widgets[prefix + ("snpx_read", "word_ranges")].setPlainText("1,2")
    assert not pane.save_button.isEnabled()
    pane.review_changes()
    assert pane.save_button.isEnabled(), pane.status.text()
    assert "Original SHA-256" in pane.preview.toPlainText() and "2147483648" in pane.preview.toPlainText()
    pane.save_changes()
    updated = BridgeConfig.load(path)
    assert not updated.read_only and updated.plan_ttl_seconds == 75 and updated.max_backup_bytes == 2147483648
    assert updated.robots["r1"].native_register_targets == ("R[1]", "PR[1,1]")
    assert updated.robots["r1"].snpx_read.word_ranges == ((1, 2),)
    assert json.loads(path.read_text())["robots"]["r2"] == original["robots"]["r2"]
    assert len(list((path.parent / ".fanuc-policy-history").glob("*.bak"))) == 1
    pane.close()


def test_changed_controls_invalidate_review_and_stale_save_is_refused(editor_policy):
    app = QApplication.instance() or QApplication([])
    path, original = editor_policy
    pane = PolicyPane("robot", str(path))
    pane.review_changes(); assert pane.save_button.isEnabled()
    pane.widgets[("max_pending_requests",)].setValue(3)
    assert pane.reviewed is None and not pane.save_button.isEnabled()
    pane.review_changes()
    path.write_text(json.dumps({**original, "plan_ttl_seconds": 25}))
    before = path.read_bytes(); pane.save_changes()
    assert path.read_bytes() == before and "changed since loading" in pane.status.text()
    pane.config_path.setText(str(path.with_name("missing.json")))
    assert pane.snapshot is None and not pane.tabs.isEnabled()
    pane.close()


def test_invalid_snpx_text_is_not_lost_when_switching_robots(editor_policy):
    app = QApplication.instance() or QApplication([])
    path, _ = editor_policy
    pane = PolicyPane("robot", str(path))
    pane.snpx_enabled.setChecked(True)
    key = ("robots", "r1", "snpx_read", "word_ranges")
    pane.widgets[key].setPlainText("invalid range")
    pane.robot.setCurrentText("r2")
    assert not pane.review_button.isEnabled() and key in pane.errors
    pane.robot.setCurrentText("r1")
    assert pane.widgets[key].toPlainText() == "invalid range"
    pane.snpx_enabled.setChecked(False)
    assert not pane.errors and pane.review_button.isEnabled()
    pane.close()


def test_closing_with_unsaved_changes_allows_cancel_or_discard(editor_policy, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path, _ = editor_policy
    dialog = SafeguardsDialog(str(path))
    dialog.robot_policy.widgets[("plan_ttl_seconds",)].setValue(11)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    dialog.reject(); assert dialog.result() == 0 and dialog.robot_policy.draft["plan_ttl_seconds"] == 11
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog.reject(); assert dialog.result() == QDialog.Rejected


def test_reloading_policy_does_not_reuse_another_files_snpx_scope(editor_policy):
    app = QApplication.instance() or QApplication([])
    path, original = editor_policy
    pane = PolicyPane("robot", str(path))
    pane.snpx_enabled.setChecked(True)
    pane.widgets[("robots", "r1", "snpx_read", "io_targets")].setPlainText("DI[1]")
    pane.snpx_enabled.setChecked(False)
    assert pane.snpx_cache["r1"]["io_targets"] == ["DI[1]"]
    second = path.with_name("second.local.json")
    second.write_text(json.dumps(original))
    pane.config_path.setText(str(second)); pane.load_document()
    pane.snpx_enabled.setChecked(True)
    assert pane.draft["robots"]["r1"]["snpx_read"] == {}
    pane.close()


def test_transport_defaults_and_legacy_numeric_fields_match_runtime_without_rewriting(editor_policy):
    app = QApplication.instance() or QApplication([])
    path, original = editor_policy
    original["robots"]["r1"]["profile"].update(transport="sftp", timeout="3.5")
    path.write_text(json.dumps(original))
    pane = PolicyPane("robot", str(path))
    prefix = ("robots", "r1", "profile")
    assert pane.snapshot is not None, pane.status.text()
    assert pane.widgets[prefix + ("port",)].value() == 22
    assert pane.widgets[prefix + ("timeout",)].value() == 3.5
    assert pane.draft == original  # Displaying effective defaults preserves the saved document.
    pane.widgets[prefix + ("transport",)].setCurrentText("ftp")
    assert pane.widgets[prefix + ("port",)].value() == 21
    assert "port" not in pane.draft["robots"]["r1"]["profile"]
    pane.widgets[prefix + ("port",)].setValue(2222)
    pane.widgets[prefix + ("transport",)].setCurrentText("sftp")
    assert pane.widgets[prefix + ("port",)].value() == 2222
    pane.close()


def test_main_window_opens_safeguards_and_remembers_saved_policy(editor_policy, tmp_path, monkeypatch):
    from fanuc_workbench.core.settings import QStandardPaths
    from fanuc_workbench.ui import main_window
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda _: str(tmp_path / "app-settings"))
    monkeypatch.setattr(main_window, "discover_plugins", lambda: [])
    monkeypatch.delenv("FANUC_MCP_SERVER_CONFIG", raising=False)
    monkeypatch.delenv("FANUC_WORKBENCH_CONFIG", raising=False)
    window = main_window.MainWindow()
    path, _ = editor_policy
    window.native_tab.config_path.setText(str(path))
    called = []
    class Dialog:
        saved_policy_path = str(path)
        loaded_broker_runtime_path = str(tmp_path / "runtime.json")
        def __init__(self, selected, broker, parent): called.append(selected)
        def exec(self): return QDialog.Rejected  # Saving a pane need not close the dialog.
    monkeypatch.setattr(main_window, "SafeguardsDialog", Dialog)
    action = next(action for action in window.settings_menu.actions() if action.text() == "Safeguards and policies…")
    action.trigger()
    assert called == [str(path)] and window.settings.preferences()["robot_policy_path"] == str(path)
    assert window.settings.preferences()["broker_runtime_path"].endswith("runtime.json")
    assert window.native_tab.bridge is None
    window.close()
