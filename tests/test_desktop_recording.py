from PySide6.QtWidgets import QMessageBox

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.live_recording import LiveRecording
from fanuc_workbench.services.monitoring import Sample
from fanuc_workbench.ui import advanced_tabs,main_window
from test_desktop_preferences import desktop


def test_watch_drafts_survive_reopen_without_saving_sample_values(desktop):
    _,store=desktop
    window=main_window.MainWindow();live=window.live_tab
    live.watch.item(0,0).setText('R[21]');live.watch.item(0,1).setText('Good parts')
    live.add_watch();live.watch.item(6,0).setText('DI[21]');live.watch.item(6,1).setText('Ready')
    live.sample_ready(Sample(1,{'Good parts':123456}))
    window.close()
    assert '123456' not in store.preferences_path.read_text()
    reopened=main_window.MainWindow()
    assert reopened.live_tab.watch.rowCount()==7
    assert reopened.live_tab.watch.item(0,0).text()=='R[21]'
    assert reopened.live_tab.watch.item(6,1).text()=='Ready'
    assert reopened.live_tab.watch.item(0,2).text()==''
    reopened.close()


def test_new_recording_cancel_retains_data_and_confirm_resets_all_buffers(desktop,monkeypatch):
    window=main_window.MainWindow();live=window.live_tab
    live.sample_ready(Sample(1,{'PartCount':1}));live.sample_ready(Sample(2,{'PartCount':2}))
    old_id=live.recording.recording_id
    monkeypatch.setattr(advanced_tabs.QMessageBox,'question',lambda *args:QMessageBox.Cancel)
    live.new_recording();assert live.recording.recording_id==old_id and len(live.trend.samples)==2
    monkeypatch.setattr(advanced_tabs.QMessageBox,'question',lambda *args:QMessageBox.Yes)
    live.new_recording()
    assert live.recording.recording_id!=old_id and not live.trend.samples and not live.timeline.events
    assert live.last_sample is None and live.events.rowCount()==0 and not live.cycle_recording
    assert live.watch.item(0,0).text()=='R[1]'
    window.close()


def test_source_bound_recording_prevents_reads_from_a_different_robot(desktop,monkeypatch):
    _,store=desktop;store.save_profiles([RobotProfile('A','192.0.2.1'),RobotProfile('B','192.0.2.2')])
    window=main_window.MainWindow();live=window.live_tab;live.source.setCurrentIndex(1)
    live.sample_ready(Sample(1,{'PartCount':1}))
    reads=[];monkeypatch.setattr(advanced_tabs,'create_backend',lambda profile:reads.append(profile.host))
    window.profile_combo.setCurrentIndex(1);live.poll_once()
    assert not reads and len(live.trend.samples)==1
    assert 'New recording' in window.statusBar().currentMessage()
    assert 'A' in live.recording_label.text() and 'B' in live.target_label.text()
    assert not live.timeline_for_current_source()
    window.close()


def test_late_workspace_result_is_ignored_and_sample_limit_stops_before_another_read(desktop,monkeypatch,tmp_path):
    window=main_window.MainWindow();live=window.live_tab;pending=[]
    class Queue:
        def start(self,worker):pending.append(worker)
    class Provider:
        def __init__(self,path):pass
        def sample(self,specs):return Sample(1,{'PartCount':1})
    window.thread_pool=Queue();monkeypatch.setattr(advanced_tabs,'WorkspaceSampleProvider',Provider)
    window.workspace=tmp_path/'one';live.poll_once()
    window.workspace=tmp_path/'two';live.sync_workspace();pending.pop().run()
    assert live.last_sample is None and not live.inflight
    live.recording=LiveRecording(sample_limit=1);live.sample_ready(Sample(1,{'PartCount':2}))
    live.poll_once();assert not pending and len(live.trend.samples)==1 and not live.timer.isActive()
    window.close()
