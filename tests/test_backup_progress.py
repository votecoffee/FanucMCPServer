import json
from pathlib import Path

import pytest

from fanuc_workbench.core.models import RemoteEntry, RobotProfile
from fanuc_workbench.services.backup import BackupLimitError, BackupService


class Files:
    def __init__(self, values, listing, fail_limit=False):
        self.values, self.listing, self.fail_limit = values, listing, fail_limit
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def list_dir(self, path):
        self.calls.append(('list', path))
        return self.listing[path]
    def download(self, path, local):
        self.calls.append(('download', path))
        if self.fail_limit:
            raise BackupLimitError('Synthetic limit')
        if path not in self.values:
            raise FileNotFoundError(path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.values[path])


def test_discovery_precedes_percentage_and_completion_requires_saved_manifest(tmp_path):
    backend = Files({'md:A.TP': b'tp', 'md:sub/B.TXT': b'b'}, {'md:': [
        RemoteEntry('A.TP','md:A.TP',False), RemoteEntry('bad.txt','md:bad.txt',False),
        RemoteEntry('sub','md:sub',True)], 'md:sub': [RemoteEntry('B.TXT','md:sub/B.TXT',False)]})
    events = []
    def progress(event):
        if event.get('percent') == 100:
            assert len(list(tmp_path.glob('*/backup_manifest.json'))) == 1
        events.append(event)
    folder = BackupService(backend, RobotProfile('Synthetic', '192.0.2.1')).run(tmp_path, progress=progress)
    assert [event['percent'] for event in events if event.get('phase') == 'discovering'] == [None, None]
    percentages = [event['percent'] for event in events if event.get('percent') is not None]
    assert percentages == sorted(percentages) and percentages[-1] == 100 and percentages[-2] == 99
    assert events[-1]['total_files'] == events[-1]['completed_files'] == 3
    assert events[-1]['failed_files'] == events[-1]['warning_count'] == 1
    manifest = json.loads((folder/'backup_manifest.json').read_text())
    assert len(manifest['records']) == 3 and manifest['warnings']
    assert max(i for i, call in enumerate(backend.calls) if call[0] == 'list') < min(i for i, call in enumerate(backend.calls) if call[0] == 'download')


def test_interrupted_capture_retains_manifest_and_never_reports_100(tmp_path):
    backend = Files({}, {'md:': [RemoteEntry('A.TXT','md:A.TXT',False)]}, fail_limit=True)
    events = []
    with pytest.raises(BackupLimitError):
        BackupService(backend, RobotProfile('Synthetic','192.0.2.1')).run(tmp_path, progress=events.append)
    assert all(event.get('percent') != 100 for event in events)
    assert events[-1]['phase'] == 'failed'
    manifest = json.loads(next(tmp_path.glob('*/backup_manifest.json')).read_text())
    assert manifest['warnings'] == ['Capture interrupted: BackupLimitError']


def test_selected_paths_have_fixed_deduplicated_count_and_ascii_is_not_redownloaded(tmp_path):
    backend = Files({'md:A.TP':b'tp','md:A.LS':b'ls'}, {})
    events=[]
    BackupService(backend, RobotProfile('Synthetic','192.0.2.1')).run(tmp_path,
        selected_paths=['md:A.TP','md:A.LS','md:A.TP'], progress=events.append)
    assert events[-1]['total_files'] == events[-1]['completed_files'] == 2
    assert backend.calls == [('download','md:A.TP'),('download','md:A.LS')]


def test_empty_capture_reports_completion_after_manifest(tmp_path):
    backend=Files({}, {'md:':[]})
    events=[]
    BackupService(backend, RobotProfile('Synthetic','192.0.2.1')).run(tmp_path, progress=events.append)
    assert events[-1]['percent'] == 100 and events[-1]['total_files'] == 0


def test_gui_discovery_transfer_and_failure_progress(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from fanuc_workbench.core.settings import QStandardPaths
    from fanuc_workbench.ui import main_window
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths,'writableLocation',lambda _:str(tmp_path/'settings'))
    monkeypatch.setattr(main_window,'discover_plugins',lambda:[])
    monkeypatch.setattr(main_window.QMessageBox,'critical',lambda *_:None)
    window=main_window.MainWindow()
    window.backup_progress({'phase':'discovering','percent':None,'message':'Listing'})
    assert window.backup_progress_bar.maximum()==0
    window.backup_progress({'phase':'downloading','percent':50,'completed_files':1,'total_files':2,'failed_files':1,'message':'Processed'})
    assert window.backup_progress_bar.value()==50 and '1 failed' in window.backup_progress_label.text()
    window.backup_failed('Synthetic interruption')
    assert window.backup_progress_bar.value()==50 and 'Interrupted' in window.backup_progress_bar.format()
    window.close()
