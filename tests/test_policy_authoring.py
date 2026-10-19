import json
import os

import pytest

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.policy_authoring import create_policy, policy_document
from fanuc_workbench.core.models import RobotProfile


def profile():
    return RobotProfile('Synthetic','192.0.2.1',password='never-persist',roots=['md:','ud1:'])


def test_create_read_only_and_exact_reviewed_write_policies_without_credentials(tmp_path):
    read_only=policy_document(profile(),robot_id='r1')
    path=tmp_path/'robot.local.json'
    settings=create_policy(path,read_only)
    assert settings.read_only and BridgeConfig.load(path).robots['r1'].native_provider=='disabled'
    assert 'never-persist' not in path.read_text()
    document=policy_document(profile(),robot_id='r1',read_only=False,password_env='SYNTHETIC_PASSWORD',
        writable_files=['md:TEST.LS'],creatable_files=['ud1:TRACE.CSV'],native_provider='frrobot',
        native_register_targets=['R[1]','PR[1,1]'],native_programs=['TEST'],
        comment_service_url='http://192.0.2.1',comment_targets=['R[1]'])
    second=tmp_path/'reviewed.local.json'
    create_policy(second,document)
    parsed=BridgeConfig.load(second)
    assert not parsed.read_only and parsed.approval_mode=='elicitation'
    assert parsed.robots['r1'].writable_files==('md:TEST.LS',)
    assert parsed.robots['r1'].password_env=='SYNTHETIC_PASSWORD'
    assert not parsed.state_dir.exists() and not parsed.source_dir.exists()


@pytest.mark.parametrize('options', [
    {'robot_id':'bad id'},{'read_only':'false'},{'read_only':False},
    {'password_env':'secret!password'},{'writable_files':['md:*.LS']},
    {'writable_files':['md:MAIN.SV']},{'writable_files':['md:DCS.LS']},
    {'writable_files':['md:A.LS','MD:a.ls']},{'creatable_files':['md:NEW.TXT']},
    {'native_register_targets':['R[1]']},{'native_provider':'frrobot','native_register_targets':['R[*]']},
    {'comment_targets':['R[1]']},{'comment_service_url':'http://192.0.2.2','comment_targets':['R[1]']},
])
def test_invalid_or_broad_targets_cannot_be_authored(options):
    with pytest.raises((ValueError,TypeError)):
        policy_document(profile(),**{'robot_id':'r1',**options})


def test_existing_policies_are_never_overwritten_and_invalid_policy_leaves_no_file(tmp_path):
    path=tmp_path/'existing.local.json'
    original=b'{"private":"preserved"}\r\n'
    path.write_bytes(original)
    with pytest.raises(FileExistsError):
        create_policy(path,policy_document(profile(),robot_id='r1'))
    assert path.read_bytes()==original
    missing=tmp_path/'bad.local.json'
    with pytest.raises((KeyError,ValueError)):
        create_policy(missing,{'allow_writes':True})
    assert not missing.exists()


def test_dialog_requires_fresh_review_and_creates_selected_policy(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QDialogButtonBox,QDialog
    from fanuc_workbench.ui.policy_dialog import PolicyDialog
    app=QApplication.instance() or QApplication([])
    dialog=PolicyDialog(profile())
    assert dialog.read_only.isChecked()
    dialog.read_only.setChecked(False)
    dialog.fields['writable_files'].setPlainText('md:TEST.LS')
    dialog.review_policy()
    assert dialog.reviewed['approval_mode']=='elicitation' and dialog.reviewed['allow_writes'] is True
    dialog.host.setText('192.0.2.2')
    assert dialog.reviewed is None and not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
    dialog.review_policy()
    target=tmp_path/'robot.local.json'
    monkeypatch.setattr('fanuc_workbench.ui.policy_dialog.QFileDialog.getSaveFileName',lambda *_:(str(target),''))
    dialog.save_policy()
    assert dialog.result()==QDialog.Accepted and dialog.settings.path==target
    assert BridgeConfig.load(target).robots[dialog.robot_id.text()].profile.host=='192.0.2.2'
    dialog.close()


def test_native_policy_path_and_robot_are_restored_without_automatic_connection(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from fanuc_workbench.core.settings import SettingsStore,QStandardPaths
    from fanuc_workbench.ui import main_window
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths,'writableLocation',lambda _:str(tmp_path/'settings'))
    monkeypatch.setattr(main_window,'discover_plugins',lambda:[])
    monkeypatch.delenv('FANUC_MCP_SERVER_CONFIG',raising=False)
    monkeypatch.delenv('FANUC_WORKBENCH_CONFIG',raising=False)
    document=policy_document(profile(),robot_id='first')
    document['robots']['second']=dict(document['robots']['first'])
    path=tmp_path/'robots.local.json';create_policy(path,document)
    store=SettingsStore();store.update_preferences(robot_policy_path=str(path),native_robot_id='second')
    window=main_window.MainWindow()
    assert window.native_tab.config_path.text()==str(path) and window.native_tab.bridge is None
    window.native_tab.load_config()
    assert window.native_tab.robot.currentData()=='second'
    assert not window.native_tab.bridge.native.clients
    window.close()
