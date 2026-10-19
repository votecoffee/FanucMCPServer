from dataclasses import replace

from PySide6.QtCore import Qt

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.fleet import FleetManager,FleetResult
from fanuc_workbench.ui import main_window
from test_desktop_preferences import desktop


def make_window(store):
    store.save_profiles([RobotProfile('Robot A', '192.0.2.1'), RobotProfile('Robot B', '192.0.2.2')])
    return main_window.MainWindow()


def test_scheduler_respects_unchecked_rows_and_binds_jobs_to_displayed_targets(desktop, monkeypatch):
    _, store = desktop
    window = make_window(store); fleet = window.fleet
    fleet.table.item(1, 0).setCheckState(Qt.Unchecked)
    calls = []
    monkeypatch.setattr(fleet, '_run', lambda rows: calls.append(rows))
    fleet.run_due()
    assert len(calls[0]) == 1 and calls[0][0][1].profile.host == '192.0.2.1'
    assert not fleet._rows(False)[1][1].enabled
    original_job = fleet._rows()[0][1]
    window.profiles[0] = replace(window.profiles[0], host='192.0.2.99')
    assert fleet._rows() == []
    assert 'reload' in fleet.table.item(0, 7).text()
    assert original_job.profile.host == '192.0.2.1'
    window.close()


def test_profile_reload_preserves_matching_choices_and_starts_new_targets_unchecked(desktop):
    _, store = desktop
    window = make_window(store); fleet = window.fleet
    fleet.table.item(0, 4).setText('45')
    fleet.table.item(1, 0).setCheckState(Qt.Unchecked)
    window.profiles.append(RobotProfile('New robot', '192.0.2.3'))
    fleet.load_profiles()
    assert fleet.table.item(0, 0).checkState() == Qt.Checked
    assert fleet.table.item(0, 4).text() == '45'
    assert fleet.table.item(1, 0).checkState() == Qt.Unchecked
    assert fleet.table.item(2, 0).checkState() == Qt.Unchecked
    fleet.table.item(0, 4).setText('bad interval')
    assert fleet._rows() == []
    assert 'Interval' in fleet.table.item(0, 7).text()
    window.close()


def test_reloading_during_a_job_waits_for_its_rows_to_finish(desktop):
    _, store = desktop
    window = make_window(store); fleet = window.fleet
    fleet.running = True
    window.profiles = [RobotProfile('Replacement', '192.0.2.99')]
    fleet.load_profiles()
    assert fleet.table.rowCount() == 2 and fleet.table.item(0, 1).text() == 'Robot A'
    fleet._finished()
    assert fleet.table.rowCount() == 1 and fleet.table.item(0, 1).text() == 'Replacement'
    assert fleet.table.item(0, 0).checkState() == Qt.Unchecked
    window.close()


def test_job_updates_the_same_fleet_record_that_is_saved(desktop,tmp_path,monkeypatch):
    _,store=desktop
    window=make_window(store);fleet=window.fleet;fleet.dest.setText(str(tmp_path/'fleet'))
    class Immediate:
        def start(self,worker):worker.run()
    window.thread_pool=Immediate()
    def backup(manager,row,destination,**kwargs):
        assert any(robot is row for robot in manager.robots)
        row.last_backup=str(destination/'synthetic-backup');manager.save()
        return FleetResult(row.profile.name,'OK',row.last_backup)
    monkeypatch.setattr(FleetManager,'backup_one',backup)
    fleet.table.item(1,0).setCheckState(Qt.Unchecked)
    fleet.backup_checked()
    saved=FleetManager(tmp_path/'fleet'/'fleet.json').load()
    assert saved.robots[0].last_backup==str(tmp_path/'fleet'/'synthetic-backup')
    assert saved.robots[1].enabled is False and not saved.robots[1].last_backup
    assert not fleet.running
    window.close()


def test_setup_failure_leaves_fleet_available_to_retry(desktop,tmp_path,monkeypatch):
    _,store=desktop
    window=make_window(store);fleet=window.fleet;fleet.dest.setText(str(tmp_path/'fleet'))
    def fail(*_):raise OSError('Synthetic fleet state failure')
    monkeypatch.setattr(FleetManager,'save',fail)
    fleet.backup_checked()
    assert not fleet.running and 'could not start' in fleet.log.toPlainText()
    window.close()
