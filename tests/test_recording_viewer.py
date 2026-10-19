import json

from PySide6.QtCore import Qt

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.live_recording import LiveRecording, recording_source
from fanuc_workbench.services.monitoring import Sample, WatchSpec
from fanuc_workbench.services.saved_recording import load_recording
from fanuc_workbench.ui import advanced_tabs, main_window, recording_viewer
from test_desktop_preferences import desktop


def saved_file(tmp_path, *, count=205):
    recording = LiveRecording()
    recording.ensure_context(recording_source(live=True, profile=RobotProfile('Saved robot', '192.0.2.1')),
                             [WatchSpec('R[1]', 'Count'), WatchSpec('PR[1]', 'Pose')])
    for index in range(count):
        if index == 3:
            recording.pause('Synthetic pause', timestamp=index+.5)
        recording.add(Sample(index+1, {'Count': index, 'Pose': {'J1': 1.25, 'J2': 2}},
                             {'Count': 'Unavailable'} if index == 1 else {}))
    recording.cycle.mark('START')
    recording.cycle_recording = True
    return recording.export_json(tmp_path/'saved.json')


def test_viewer_pages_data_preserves_errors_and_gaps_and_restores_its_own_layout(desktop, tmp_path):
    app, store = desktop
    store.update_preferences(layout={'sentinel': 'main layout'})
    path = saved_file(tmp_path)
    original = path.read_bytes()
    viewer = recording_viewer.RecordingViewer(load_recording(path), store)
    assert viewer.sample_rows.table.rowCount() == 200
    assert viewer.sample_rows.table.item(1, 2).text() == 'Unavailable'
    assert viewer.sample_rows.table.item(1, 3).text() == 'Unavailable'
    assert viewer.plot.break_before == [3] and 'open when exported' in viewer.statusBar().currentMessage()
    viewer.sample_rows.next.click()
    assert viewer.sample_rows.table.rowCount() == 5 and viewer.sample_rows.table.item(0, 0).text() == '200'
    viewer.signal.setCurrentText('Pose')
    assert viewer.sample_rows.table.item(0, 2).text() == 'J1=1.25   J2=2'
    assert viewer.plot.key == 'Pose'
    viewer.sample_rows.table.setColumnWidth(2, 620)
    viewer.timeline_rows.table.setColumnWidth(2, 410)
    viewer.tabs.setTabVisible(viewer.tabs.indexOf(viewer.cycle_rows), False)
    viewer.tabs.tabBar().moveTab(viewer.tabs.indexOf(viewer.pause_rows), 0)
    viewer.reject()
    assert store.preferences()['layout'] == {'sentinel': 'main layout'}
    reopened = recording_viewer.RecordingViewer(load_recording(path), store)
    assert reopened.sample_rows.table.columnWidth(2) == 620
    assert reopened.timeline_rows.table.columnWidth(2) == 410
    assert reopened.tabs.widget(0) is reopened.pause_rows
    assert not reopened.tabs.isTabVisible(reopened.tabs.indexOf(reopened.cycle_rows))
    assert reopened.sample_rows.table.horizontalHeader().sectionResizeMode(2).name == 'Interactive'
    assert not reopened.sample_rows.table.item(0, 2).flags() & Qt.ItemIsEditable
    metadata = json.loads(reopened.details.toPlainText())
    assert metadata['selected_file'] == str(path) and metadata['file_sha256'] == reopened.recording.sha256
    reopened.reject()
    assert path.read_bytes() == original


def test_opening_saved_capture_leaves_active_recording_profile_and_polling_unchanged(desktop, tmp_path, monkeypatch):
    _, store = desktop
    store.save_profiles([RobotProfile('Active robot', '192.0.2.2')])
    window = main_window.MainWindow()
    live = window.live_tab
    live.source.setCurrentIndex(1)
    live.sample_ready(Sample(1, {'PartCount': 42}))
    live.timer.start(60000)
    original_recording, original_profile = live.recording, window.current_profile
    original_document, original_preferences = live.recording.document(), store.preferences()
    path = saved_file(tmp_path, count=4)
    monkeypatch.setattr(advanced_tabs.QFileDialog, 'getOpenFileName', lambda *_: (str(path), ''))
    monkeypatch.setattr(advanced_tabs, 'create_backend', lambda *_: (_ for _ in ()).throw(AssertionError('Unexpected connection')))
    opened = []
    def inspect(viewer):
        opened.append(viewer)
        assert viewer.data['cycle_recording'] is True
        assert 'Saved robot' in viewer.summary.text()
        viewer.reject()
    monkeypatch.setattr(recording_viewer.RecordingViewer, 'exec', inspect)
    live.open_recording()
    assert len(opened) == 1
    assert live.recording is original_recording and live.recording.document() == original_document
    assert live.timer.isActive() and not live.cycle_recording
    assert window.current_profile is original_profile and store.load_profiles()[0].host == '192.0.2.2'
    preferences = store.preferences()
    preferences.pop('recording_viewer_layout', None)
    assert preferences == original_preferences
    assert live.watch.item(0, 0).text() == 'R[1]' and live.watch.item(0, 2).text() == '42'
    live.timer.stop()
    window.close()


def test_bad_file_and_cancel_do_not_open_a_viewer_or_discard_active_samples(desktop, tmp_path, monkeypatch):
    window = main_window.MainWindow()
    live = window.live_tab
    live.sample_ready(Sample(1, {'PartCount': 7}))
    before = live.recording.document()
    errors = []
    monkeypatch.setattr(recording_viewer.RecordingViewer, 'exec', lambda *_: (_ for _ in ()).throw(AssertionError('Unexpected viewer')))
    monkeypatch.setattr(advanced_tabs.QMessageBox, 'critical', lambda *args: errors.append(args[2]))
    monkeypatch.setattr(advanced_tabs.QFileDialog, 'getOpenFileName', lambda *_: ('', ''))
    live.open_recording()
    assert not errors
    path = tmp_path/'bad.json'
    path.write_text('{"format": "other"}')
    monkeypatch.setattr(advanced_tabs.QFileDialog, 'getOpenFileName', lambda *_: (str(path), ''))
    live.open_recording()
    assert errors and live.recording.document() == before
    window.close()


def test_empty_recording_viewer_handles_no_signals_or_history(desktop, tmp_path):
    _, store = desktop
    path = LiveRecording().export_json(tmp_path/'empty.json')
    viewer = recording_viewer.RecordingViewer(load_recording(path), store)
    assert viewer.signal.count() == 0 and viewer.sample_rows.table.rowCount() == 0
    assert viewer.sample_rows.range_label.text() == 'No rows'
    assert not viewer.sample_rows.previous.isEnabled() and not viewer.sample_rows.next.isEnabled()
    assert not viewer.plot.samples and not viewer.timeline_rows.table.rowCount()
    viewer.reject()
