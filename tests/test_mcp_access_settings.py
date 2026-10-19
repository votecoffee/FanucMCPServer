import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from fanuc_workbench.bridge.access_settings import load_access_settings, save_access_settings
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.ui.mcp_access_dialog import McpAccessDialog
from test_robot_bridge import MemoryRobot, ORIGINAL, PROPOSED, digest


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / 'robot_bridge.local.json'
    document = {'state_dir': 'state', 'source_dir': 'edits', 'allow_writes': False,
        'approval_mode': 'elicitation', 'max_file_bytes': 1024,
        'extension_metadata': {'keep': 'unchanged'}, 'robots': {'r1': {
            'profile': {'name': 'Test robot', 'host': '192.0.2.1', 'roots': ['md:', 'ud1:']},
            'password_env': 'FANUC_TEST_PASSWORD', 'writable_files': ['md:MAIN.LS'],
            'creatable_files': ['ud1:TEST.TXT'], 'comment_service_url': 'http://192.0.2.1',
            'comment_targets': ['R[1]'], 'native_provider': 'frrobot',
            'native_register_targets': ['R[1]'], 'native_programs': ['TEST']}}}
    path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    return path, document


@pytest.mark.parametrize('read_only', [True, False])
def test_access_setting_changes_only_write_switch(policy, read_only):
    path, original = policy
    saved = save_access_settings(load_access_settings(path), read_only=read_only)
    assert saved.read_only is read_only
    updated = json.loads(path.read_text(encoding='utf-8'))
    assert updated == {**original, 'allow_writes': not read_only}
    assert BridgeConfig.load(path).read_only is read_only


def test_unchanged_mode_preserves_original_file_bytes_and_default_is_read_only(policy):
    path, document = policy
    del document['allow_writes']
    path.write_bytes(json.dumps(document).encode('utf-8') + b'\r\n')
    original = path.read_bytes()
    settings = load_access_settings(path)
    assert settings.read_only
    save_access_settings(settings, read_only=True)
    assert path.read_bytes() == original


def test_stale_view_cannot_overwrite_other_operator_edits(policy):
    path, document = policy
    loaded = load_access_settings(path)
    document['robots']['r1']['profile']['host'] = '192.0.2.2'
    path.write_text(json.dumps(document), encoding='utf-8')
    changed = path.read_bytes()
    with pytest.raises(ValueError, match='changed since loading'):
        save_access_settings(loaded, read_only=False)
    assert path.read_bytes() == changed


@pytest.mark.parametrize('invalid', ['false', 0, None])
def test_access_setting_rejects_non_boolean_values(policy, invalid):
    path, _ = policy
    loaded = load_access_settings(path)
    original = path.read_bytes()
    with pytest.raises(ValueError, match='boolean'):
        save_access_settings(loaded, read_only=invalid)
    assert path.read_bytes() == original


def test_invalid_policy_cannot_be_loaded_for_editing(policy):
    path, document = policy
    document['allow_writes'] = 'false'
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='boolean'):
        load_access_settings(path)


def test_loaded_session_keeps_mode_and_new_session_retains_target_and_approval_checks(policy, monkeypatch):
    path, _ = policy
    monkeypatch.setenv('FANUC_TEST_PASSWORD', 'test-only-password')
    robot = MemoryRobot()
    config = BridgeConfig.load(path)
    config.source_dir.mkdir()
    (config.source_dir / 'MAIN.LS').write_bytes(PROPOSED)
    with RobotBridge.from_config(path, lambda _: robot) as original:
        assert original.operations.capabilities()['controller_access']['read_only']
        capture = original.read_file('r1', 'md:MAIN.LS')
        assert capture['sha256'] == digest(ORIGINAL)
        with pytest.raises(ValueError, match='disabled'):
            original.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', capture['sha256'])
        save_access_settings(load_access_settings(path), read_only=False)
        assert original.config.read_only  # An on-disk edit cannot elevate a running session.
        with RobotBridge.from_config(path, lambda _: robot) as updated:
            assert not updated.operations.capabilities()['controller_access']['read_only']
            with pytest.raises(ValueError, match='writable_files'):
                updated.prepare_upload('r1', 'md:OTHER.LS', 'MAIN.LS', capture['sha256'])
            plan = updated.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', capture['sha256'])
            with pytest.raises(ValueError, match='confirmation'):
                updated.commit_upload(plan['plan_id'], plan['proposed_sha256'])
    assert robot.uploads == 0


def test_read_only_blocks_all_controller_change_classes_before_transport(policy):
    path, _ = policy
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        bridge.config.source_dir.mkdir(exist_ok=True)
        (bridge.config.source_dir / 'TEST.LS').write_bytes(
            b'/PROG TEST\r\n/ATTR\r\nCOMMENT = "Test fixture";\r\n/MN\r\n   1: ! Fixture ;\r\n/POS\r\n/END\r\n')
        calls = [lambda: bridge.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', '0' * 64),
                 lambda: bridge.prepare_file_create('r1', 'ud1:TEST.TXT', 'TEST.TXT'),
                 lambda: bridge.comments.prepare('r1', 'md:NUMREG.VA', 'R[1]', 'New', '0' * 64),
                 lambda: bridge.native.prepare_register('r1', 'R[1]', {'type': 'integer', 'value': 1}, '0' * 64),
                 lambda: bridge.native.prepare_program('r1', 'TEST.LS', '0' * 64)]
        for call in calls:
            with pytest.raises(ValueError, match='disabled|writes'):
                call()
        staged = bridge.stage_file('TEST.TXT', 'dGVzdA==', digest(b'test'))
        assert (bridge.config.source_dir / staged['source_file']).read_bytes() == b'test'
        assert not bridge.native.clients
    assert robot.connects == robot.uploads == 0


def test_dialog_load_save_and_path_changes_require_a_fresh_policy(policy):
    app = QApplication.instance() or QApplication([])
    path, _ = policy
    dialog = McpAccessDialog(str(path))
    assert dialog.read_only.isChecked() and dialog.read_only.isEnabled()
    dialog.read_only.setChecked(False)
    dialog.save_policy()
    assert dialog.result() == QDialog.Accepted
    assert not BridgeConfig.load(path).read_only
    dialog.close()
    changed = McpAccessDialog(str(path))
    changed.config_path.setText(str(path.with_name('another.json')))
    assert changed.read_only.isChecked() and not changed.read_only.isEnabled()
    assert not changed.buttons.button(QDialogButtonBox.Save).isEnabled()
    changed.close()


def test_dialog_refuses_stale_and_invalid_policies_without_writes(policy):
    app = QApplication.instance() or QApplication([])
    path, document = policy
    dialog = McpAccessDialog(str(path))
    document['robots']['r1']['native_programs'] = ['OTHER']
    path.write_text(json.dumps(document), encoding='utf-8')
    dialog.read_only.setChecked(False)
    dialog.save_policy()
    assert dialog.result() == QDialog.Rejected
    assert json.loads(path.read_text()) == document
    assert not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
    path.write_text('{invalid', encoding='utf-8')
    dialog.load_policy()
    assert dialog.read_only.isChecked() and not dialog.read_only.isEnabled()
    dialog.close()


def test_main_window_settings_menu_uses_selected_policy(policy, monkeypatch, tmp_path):
    from fanuc_workbench.core.settings import QStandardPaths
    from fanuc_workbench.ui import main_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda _: str(tmp_path / 'app-config'))
    monkeypatch.setattr(main_window, 'discover_plugins', lambda: [])
    monkeypatch.delenv('FANUC_MCP_SERVER_CONFIG', raising=False)
    monkeypatch.delenv('FANUC_WORKBENCH_CONFIG', raising=False)
    window = main_window.MainWindow()
    path, _ = policy
    window.native_tab.config_path.setText(str(path))
    calls = []

    class Dialog:
        def __init__(self, selected, parent):
            calls.append(selected)
            self.settings = load_access_settings(path)

        def exec(self):
            return True

    monkeypatch.setattr(main_window, 'McpAccessDialog', Dialog)
    assert window.settings_menu.title() == 'Settings'
    action = next(action for action in window.settings_menu.actions() if action.text() == 'MCP access…')
    action.trigger()
    window.native_tab.config_path.clear()
    action.trigger()
    assert calls == [str(path), str(path.resolve())]
    assert 'Reconnect MCP servers' in window.statusBar().currentMessage()
    window.close()
