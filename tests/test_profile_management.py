from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.monitoring import Sample
from fanuc_workbench.services.profiles import ProfileCatalog
from fanuc_workbench.ui import main_window,connection_dialog,profiles_dialog,advanced_tabs
from test_desktop_preferences import desktop


def test_editor_keeps_custom_ports_timeout_and_rejects_duplicate_names(desktop,monkeypatch):
    original=RobotProfile('Original','192.0.2.1',port=2121,timeout=45.125)
    editor=connection_dialog.ConnectionDialog(original,existing_names=['Other'])
    editor.transport.setCurrentText('sftp')
    assert editor.port.value()==2121 and editor.profile().timeout==45.125
    warnings=[]
    monkeypatch.setattr(connection_dialog.QMessageBox,'warning',lambda *args:warnings.append(args[2]))
    editor.name.setText(' OTHER ');editor.accept()
    assert editor.result()==QDialog.Rejected and 'unique' in warnings[-1]
    editor.name.setText('New');editor.host.setText('ftp://192.0.2.1');editor.accept()
    assert editor.result()==QDialog.Rejected and 'hostname' in warnings[-1]
    editor.host.setText('192.0.2.1');editor.accept()
    assert editor.result()==QDialog.Accepted and original.transport=='ftp'
    standard=connection_dialog.ConnectionDialog()
    standard.transport.setCurrentText('sftp');assert standard.port.value()==22
    standard.close();editor.close()


def test_manager_stages_changes_and_cancel_keeps_original_profiles(desktop):
    _,store=desktop
    original=RobotProfile('Original','192.0.2.1',password='session-only')
    store.save_profiles([original]);before=store.path.read_bytes()
    calls=[]
    dialog=profiles_dialog.ProfilesDialog([original],original,lambda *args:calls.append(args) or True)
    dialog.duplicate()
    assert dialog.list.count()==2 and original.name=='Original'
    dialog.remove();dialog.reject()
    assert not calls and store.path.read_bytes()==before
    assert original.password=='session-only'


def test_manager_add_edit_and_retry_a_failed_save(desktop,monkeypatch):
    original=RobotProfile('Original','192.0.2.1')
    attempts=[]
    dialog=profiles_dialog.ProfilesDialog([original],original,lambda catalog,index:attempts.append((catalog.profiles,index)) or len(attempts)>1)
    def edit(editor):
        editor.name.setText('Added' if editor.host.text()=='' else 'Renamed')
        editor.host.setText('192.0.2.2')
        return QDialog.Accepted
    monkeypatch.setattr(profiles_dialog.ConnectionDialog,'exec',edit)
    dialog.add();assert dialog.list.count()==2
    dialog.edit();assert dialog.catalog.profiles[1].name=='Renamed'
    dialog.accept();assert dialog.result()==QDialog.Rejected
    dialog.accept();assert dialog.result()==QDialog.Accepted
    assert attempts[-1][1]==1 and attempts[-1][0][1].host=='192.0.2.2'


def test_rename_updates_combo_fleet_and_remembered_identity_without_connecting(desktop):
    _,store=desktop
    old=RobotProfile('Old name','192.0.2.1',password='session-only')
    store.save_profiles([old]);store.remember_profile(old,connected=True)
    window=main_window.MainWindow();catalog=ProfileCatalog(window.profiles)
    catalog.update(0,replace(window.profiles[0],name='New name',password='session-only'))
    assert window._save_connections(catalog,0)
    assert len(window.profiles)==1 and window.current_profile.name=='New name'
    assert 'New name' in window.profile_combo.currentText()
    assert window.fleet.table.item(0,1).text()=='New name'
    assert window.fleet.table.item(0,0).checkState()==Qt.Unchecked
    assert store.preferences()['last_connected_robot']['name']=='New name'
    assert 'session-only' not in store.path.read_text()+store.preferences_path.read_text()
    window.close();reopened=main_window.MainWindow()
    assert reopened.current_profile.name=='New name';reopened.close()


def test_connection_menu_edits_in_place_and_new_connection_adds_a_profile(desktop,monkeypatch):
    _,store=desktop
    store.save_profiles([RobotProfile('Original','192.0.2.1')])
    window=main_window.MainWindow()
    def edit(editor):
        editor.name.setText('Renamed' if editor.host.text() else 'Added')
        editor.host.setText('192.0.2.1' if editor.host.text() else '192.0.2.2')
        return QDialog.Accepted
    monkeypatch.setattr(main_window.ConnectionDialog,'exec',edit)
    window.edit_connection()
    assert [p.name for p in window.profiles]==['Renamed']
    window.new_connection()
    assert [p.name for p in window.profiles]==['Renamed','Added']
    assert window.current_profile.host=='192.0.2.2'
    window.close()


def test_selection_preference_failure_keeps_saved_profiles_and_gui_consistent(desktop,monkeypatch):
    _,store=desktop
    store.save_profiles([RobotProfile('Original','192.0.2.1')])
    window=main_window.MainWindow();catalog=ProfileCatalog(window.profiles)
    catalog.update(0,replace(window.profiles[0],host='192.0.2.2'))
    warnings=[]
    def fail(**_):raise OSError('Synthetic preference failure')
    monkeypatch.setattr(window.settings,'update_preferences',fail)
    monkeypatch.setattr(main_window.QMessageBox,'warning',lambda *args:warnings.append(args[2]))
    assert window._save_connections(catalog,0)
    assert window.current_profile.host==store.load_profiles()[0].host=='192.0.2.2'
    assert '192.0.2.2' in window.profile_combo.currentText() and 'Connections were saved' in warnings[0]
    window.close()


def test_failed_profile_save_keeps_memory_combo_and_original_file(desktop,monkeypatch):
    _,store=desktop
    store.save_profiles([RobotProfile('Original','192.0.2.1')]);before=store.path.read_bytes()
    window=main_window.MainWindow();catalog=ProfileCatalog(window.profiles)
    catalog.update(0,replace(window.profiles[0],host='192.0.2.2'))
    errors=[]
    monkeypatch.setattr(main_window.QMessageBox,'critical',lambda *args:errors.append(args[2]))
    def fail(*_):raise OSError('Synthetic settings failure')
    monkeypatch.setattr(window.settings,'save_profiles',fail)
    assert not window._save_connections(catalog,0)
    assert window.current_profile.host=='192.0.2.1' and '192.0.2.1' in window.profile_combo.currentText()
    assert store.path.read_bytes()==before and errors
    window.close()


def test_removing_last_profile_clears_selection_and_fleet(desktop):
    _,store=desktop
    robot=RobotProfile('Robot','192.0.2.1');store.save_profiles([robot]);store.remember_profile(robot,connected=True)
    window=main_window.MainWindow();catalog=ProfileCatalog(window.profiles);catalog.remove(0)
    assert window._save_connections(catalog,None)
    assert window.current_profile is None and window.profile_combo.count()==0
    assert window.fleet.table.rowCount()==0 and store.preferences()['last_connected_robot'] is None
    window.close();reopened=main_window.MainWindow();assert reopened.current_profile is None;reopened.close()


def test_connection_switch_pauses_file_polling_and_discards_previous_inflight_result(desktop,monkeypatch):
    _,store=desktop
    store.save_profiles([RobotProfile('First','192.0.2.1'),RobotProfile('Second','192.0.2.2')])
    window=main_window.MainWindow();live=window.live_tab
    pending=[]
    class Queue:
        def start(self,worker):pending.append(worker)
    class Provider:
        def __init__(self,*args):pass
        def sample(self,specs):return Sample(1,{'PartCount':5})
    window.thread_pool=Queue()
    monkeypatch.setattr(advanced_tabs,'create_backend',lambda _:None)
    monkeypatch.setattr(advanced_tabs,'ControllerFileSampleProvider',Provider)
    live.source.setCurrentIndex(1);live.timer.start(100000);live.start.setText('Stop');live.poll_once()
    assert live.inflight and len(pending)==1
    window.profile_combo.setCurrentIndex(1)
    assert not live.timer.isActive() and live.start.text()=='Start'
    assert 'Second' in live.target_label.text() and '192.0.2.2' in live.target_label.text()
    pending.pop().run()
    assert not live.inflight and live.last_sample is None and not live.trend.samples
    live.poll_once();pending.pop().run()
    assert live.last_sample.values['PartCount']==5
    assert store.preferences()['last_connected_robot']['host']=='192.0.2.2'
    window.close()
