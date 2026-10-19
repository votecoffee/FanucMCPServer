import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QHeaderView, QTableView, QToolButton

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.core.settings import SettingsStore, QStandardPaths
from fanuc_workbench.services.display import watch_value
from fanuc_workbench.services.monitoring import Sample
from fanuc_workbench.ui import main_window


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda _: str(tmp_path / 'config'))
    monkeypatch.setattr(main_window, 'discover_plugins', lambda: [])
    monkeypatch.delenv('FANUC_MCP_SERVER_CONFIG', raising=False)
    monkeypatch.delenv('FANUC_WORKBENCH_CONFIG', raising=False)
    return app, SettingsStore()


def test_last_successful_robot_is_restored_without_persisting_passwords(desktop, monkeypatch):
    app, store = desktop
    first = RobotProfile('First', '192.0.2.1', password='never-save-this')
    second = RobotProfile('Second', '192.0.2.2', password='nor-this')
    store.save_profiles([first, second])
    store.remember_profile(first, connected=True)
    store.remember_profile(second)
    assert store.preferred_profile(store.load_profiles()).name == 'First'
    window = main_window.MainWindow()
    assert window.current_profile.name == 'First'
    window.profile_combo.setCurrentIndex(1)
    assert window.current_profile.name == 'Second'

    class Immediate:
        def start(self, worker):
            worker.run()
    class Controller:
        def __init__(self, profile):
            assert profile.host == '192.0.2.2'
        def test_connection(self):
            return {'md:': 2}
    window.thread_pool = Immediate()
    monkeypatch.setattr(main_window, 'ControllerService', Controller)
    monkeypatch.setattr(main_window.QMessageBox, 'information', lambda *_: None)
    window.test_connection()
    window.close()
    reopened = main_window.MainWindow()
    assert reopened.current_profile.name == 'Second'
    assert 'never-save-this' not in store.path.read_text() + store.preferences_path.read_text()
    assert 'nor-this' not in store.path.read_text() + store.preferences_path.read_text()
    reopened.close()


def test_column_widths_split_sizes_and_tab_visibility_order_survive_reopen(desktop):
    app, store = desktop
    window = main_window.MainWindow()
    window.show()
    window.tabs.setCurrentWidget(window.live_tab)
    app.processEvents()
    window.live_tab.watch.setColumnWidth(2, 590)
    window.live_tab.splitter.setSizes([550, 200])
    window.tabs.tabBar().moveTab(window.tabs.indexOf(window.live_tab), 0)
    window.tabs.setTabVisible(window.tabs.indexOf(window.backup_tab), False)
    window.data.pr.setColumnWidth(7, 420)
    app.processEvents()
    ratio = window.live_tab.splitter.sizes()[0] / sum(window.live_tab.splitter.sizes())
    window.close()
    reopened = main_window.MainWindow()
    reopened.show()
    app.processEvents()
    assert reopened.tabs.widget(0) is reopened.live_tab
    assert reopened.tabs.currentWidget() is reopened.live_tab
    assert not reopened.tabs.isTabVisible(reopened.tabs.indexOf(reopened.backup_tab))
    assert reopened.live_tab.watch.columnWidth(2) == 590
    assert reopened.data.pr.columnWidth(7) == 420
    sizes = reopened.live_tab.splitter.sizes()
    assert abs(sizes[0] / sum(sizes) - ratio) < .02
    for table, _ in reopened.desktop_layout.tables.values():
        header = table.horizontalHeader() if isinstance(table, QTableView) else table.header()
        assert all(header.sectionResizeMode(i) == QHeaderView.Interactive for i in range(header.count()))
    assert len(reopened.desktop_layout.splitters) >= 10
    reopened.close()


def test_tab_menu_can_recover_hidden_tabs_and_keeps_one_visible(desktop):
    app, _ = desktop
    window = main_window.MainWindow()
    tabs = window.tabs
    menu = tabs.cornerWidget().menu()
    menu.aboutToShow.emit()
    next(action for action in menu.actions() if action.text() == 'Files').setChecked(False)
    assert not tabs.isTabVisible(tabs.indexOf(window.files_tab))
    menu.aboutToShow.emit()
    menu.actions()[0].trigger()
    assert all(tabs.isTabVisible(i) for i in range(tabs.count()))
    for i in range(1, tabs.count()):
        tabs.setTabVisible(i, False)
    menu.aboutToShow.emit()
    remaining = next(action for action in menu.actions() if action.isCheckable() and action.isChecked())
    assert not remaining.isEnabled()
    window.close()


def test_corrupt_preferences_and_unknown_deleted_profile_use_defaults(desktop):
    _, store = desktop
    store.preferences_path.write_text('{bad')
    assert store.preferences() == {}
    store.update_preferences(last_connected_robot={'host': '192.0.2.99'}, layout='bad')
    store.save_profiles([RobotProfile('Available', '192.0.2.1')])
    window = main_window.MainWindow()
    assert window.current_profile.name == 'Available'
    window.close()
    assert isinstance(json.loads(store.preferences_path.read_text())['layout'], dict)


def test_position_register_display_wraps_and_preserves_recorded_values(desktop):
    app, _ = desktop
    value = {'J6': 90.0000001, 'J2': -45.25, 'J1': 12.5, 'J3': 0, 'J4': 1, 'J5': 2}
    before = dict(value)
    text, detail = watch_value(value)
    assert text.startswith('J1=12.5   J2=-45.25   J3=0\nJ4=1')
    assert json.loads(detail) == value and value == before
    window = main_window.MainWindow()
    sample = Sample(1, {'PickPR': value}, {'DI1': 'Unavailable fixture'})
    window.live_tab.sample_ready(sample)
    cell = window.live_tab.watch.item(1, 2)
    assert '\n' in cell.text() and json.loads(cell.toolTip()) == before
    assert window.live_tab.watch.item(2, 2).toolTip() == 'Unavailable fixture'
    assert window.live_tab.trend.samples[0].values['PickPR'] == before
    rows = window.live_tab.watch.rowCount()
    window.live_tab.add_watch()
    assert window.live_tab.watch.rowCount() == rows + 1
    window.live_tab.remove_watches()
    assert window.live_tab.watch.rowCount() == rows
    window.close()
