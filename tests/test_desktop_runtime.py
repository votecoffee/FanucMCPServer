import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import tomllib

import pytest

from fanuc_workbench import __version__, app, desktop_runtime


def test_exception_log_is_utf8_rotates_and_restores_process_hook(tmp_path):
    notifications = []
    original = sys.excepthook
    with desktop_runtime.DesktopSession(tmp_path, lambda path, **kw: notifications.append((path, kw))) as session:
        session.handler.maxBytes = 256
        for _ in range(8):
            session.logger.info('Synthetic startup detail ' * 8)
        try:
            raise ValueError('Synthetic failure: J1=−29.5')
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert notifications == [(tmp_path / 'desktop.log', {'startup': False})]
        assert 'ValueError: Synthetic failure: J1=−29.5' in session.log_path.read_text(encoding='utf-8')
    assert sys.excepthook is original
    assert sorted(p.name for p in tmp_path.iterdir()) == ['desktop.log', 'desktop.log.1', 'desktop.log.2', 'desktop.log.3']
    # The closed handler must not keep Windows files locked.
    for path in tmp_path.iterdir():
        path.unlink()


def test_unwritable_log_directory_does_not_block_startup(tmp_path):
    blocked = tmp_path / 'a file'
    blocked.touch()
    notifications = []
    with desktop_runtime.DesktopSession(blocked / 'logs', lambda path, **kw: notifications.append(path)) as session:
        assert session.log_path is None
        session.report(RuntimeError, RuntimeError('fixture'), None, startup=True)
    assert notifications == [None]


def test_missing_qt_is_reported_before_desktop_import(tmp_path, monkeypatch):
    notifications = []
    session = desktop_runtime.DesktopSession(tmp_path, lambda path, **kw: notifications.append((path, kw)))
    monkeypatch.setattr(app, 'DesktopSession', lambda: session)
    original_import = builtins.__import__

    def missing_qt(name, *args, **kwargs):
        if name.startswith('PySide6'):
            raise ModuleNotFoundError('Synthetic missing Qt dependency')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', missing_qt)
    assert app.main() == 1
    assert notifications == [(tmp_path / 'desktop.log', {'startup': True})]
    assert 'Synthetic missing Qt dependency' in session.log_path.read_text(encoding='utf-8')


def test_dialog_failure_does_not_recurse_or_change_exception_hook(tmp_path):
    calls = []

    def failed_dialog(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('Fixture dialog unavailable')

    original = sys.excepthook
    with desktop_runtime.DesktopSession(tmp_path, failed_dialog) as session:
        session.exception_hook(ValueError, ValueError('Original fixture error'), None)
        assert not session.reporting
    assert calls == [1] and sys.excepthook is original
    assert 'Original fixture error' in (tmp_path / 'desktop.log').read_text()


def test_native_error_dialog_is_available_when_qt_cannot_import(monkeypatch):
    if sys.platform != 'win32':
        pytest.skip('Windows startup fallback')
    import ctypes

    original_import = builtins.__import__

    def missing_qt(name, *args, **kwargs):
        if name.startswith('PySide6'):
            raise ImportError('Fixture missing Qt')
        return original_import(name, *args, **kwargs)

    calls = []
    monkeypatch.setattr(builtins, '__import__', missing_qt)
    monkeypatch.setattr(ctypes.windll.user32, 'MessageBoxW', lambda *args: calls.append(args))
    desktop_runtime.notify_desktop_error(Path('fixture-desktop.log'), startup=True)
    assert 'could not start' in calls[0][1] and 'fixture-desktop.log' in calls[0][1]


def test_log_locations_are_user_local_and_module_version_matches_package(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local User Data'))
    assert desktop_runtime.desktop_log_directory() == tmp_path / 'Local User Data' / 'FanucMCPServer' / 'logs'
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    assert desktop_runtime.desktop_log_directory() == tmp_path / 'state' / 'FanucMCPServer' / 'logs'
    metadata = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())
    assert __version__ == metadata['project']['version']


@pytest.mark.parametrize('mode', ['startup', 'callback'])
def test_real_qt_startup_and_callback_failures_have_local_evidence(tmp_path, mode):
    code = textwrap.dedent('''
        from pathlib import Path
        import json,sys
        from PySide6.QtCore import QStandardPaths,QTimer,Qt
        from fanuc_workbench import app,desktop_runtime
        from fanuc_workbench.ui import main_window
        root=Path(sys.argv[1]);mode=sys.argv[2]
        QStandardPaths.writableLocation=lambda *_:str(root/'settings')
        main_window.discover_plugins=lambda:[]
        notified=[]
        session=desktop_runtime.DesktopSession(root/'logs',lambda path,**kw:notified.append((str(path),kw)))
        app.DesktopSession=lambda:session
        if mode=='startup':
            def failed_window():raise ValueError('Synthetic startup failure')
            main_window.MainWindow=failed_window
        else:
            original=main_window.MainWindow
            class SmokeWindow(original):
                def __init__(self):
                    super().__init__()
                    self.setAttribute(Qt.WA_DontShowOnScreen,True)
                    QTimer.singleShot(20,self.fail)
                    QTimer.singleShot(120,self.close)
                def fail(self):raise RuntimeError('Synthetic callback failure')
            main_window.MainWindow=SmokeWindow
        original_hook=sys.excepthook
        result=app.main()
        assert sys.excepthook is original_hook
        text=(root/'logs'/'desktop.log').read_text(encoding='utf-8')
        assert ('Synthetic startup failure' if mode=='startup' else 'Synthetic callback failure') in text
        assert 'Desktop stopped' in text
        print(json.dumps({'exit_code':result,'notified':notified,'hook_restored':True}))
    ''')
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'QT_QPA_PLATFORM': 'offscreen',
                   'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    for name in ('FANUC_MCP_SERVER_CONFIG', 'FANUC_WORKBENCH_CONFIG'):
        environment.pop(name, None)
    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code, str(tmp_path), mode],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['exit_code'] == (1 if mode == 'startup' else 0)
    assert report['notified'] == [[str(tmp_path / 'logs' / 'desktop.log'), {'startup': mode == 'startup'}]]
