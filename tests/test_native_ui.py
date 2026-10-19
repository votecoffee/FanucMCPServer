import os
import time
from dataclasses import replace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from test_bridge_operations import bridge_case
from test_native_workflow import native_case
from fanuc_workbench.ui.native_tab import NativeControllerTab


def test_native_ui_displays_broad_scope_without_reporting_empty_lists_as_no_access(native_case):
    bridge, _, _ = native_case
    access = replace(bridge.config.robots['r1'], native_register_targets=(), native_programs=())
    bridge.config = replace(bridge.config, write_scope='all_supported', robots={'r1': access})
    app = QApplication.instance() or QApplication([])
    class Owner:
        thread_pool = QThreadPool()
    tab = NativeControllerTab(Owner())
    tab.bridge = bridge; tab.robot.addItem('Test', 'r1'); tab.update_policy()
    assert 'all supported R/PR targets' in tab.policy.text()
    assert 'all supported application programs' in tab.policy.text()
    tab.shutdown()


def test_native_ui_uses_shared_plans_and_edits_invalidate_approval(native_case):
    bridge, _, api = native_case
    app = QApplication.instance() or QApplication([])
    class Owner:
        thread_pool = QThreadPool()
    tab = NativeControllerTab(Owner())
    tab.bridge = bridge
    tab.robot.addItem('Test', 'r1')
    tab.target.setText('R[999]')
    tab.read_register()
    deadline = time.monotonic() + 10
    while tab.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert tab.capture is not None, tab.review.toPlainText()
    assert tab.capture['value']['value'] == 0
    assert '"value": 0' in tab.change.toPlainText()
    tab.change.setPlainText('{"type":"integer","value":1}')
    tab.prepare_register()
    while tab.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert tab.plan is not None and tab.approve.isEnabled() and not api.writes
    tab.change.setPlainText('{"type":"integer","value":2}')
    assert tab.plan is None and not tab.approve.isEnabled() and not bridge.native.plans
    tab.shutdown()
    Owner.thread_pool.waitForDone()
    tab.close()
