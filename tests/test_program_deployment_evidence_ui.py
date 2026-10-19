from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from fanuc_workbench.ui.native_tab import NativeControllerTab
from test_bridge_operations import bridge_case
from test_native_workflow import native_case,SOURCE


def test_bad_offline_report_blocks_preparation_before_native_reads(native_case,tmp_path):
    bridge,_,api=native_case
    app=QApplication.instance() or QApplication([])
    class Owner:thread_pool=QThreadPool()
    tab=NativeControllerTab(Owner());tab.bridge=bridge;tab.robot.addItem('Synthetic','r1')
    source=tmp_path/'FWBTEST.LS';source.write_bytes(SOURCE)
    report=tmp_path/'validation.json';report.write_text('{"format":"fanuc-native-offline-validation","schema_version":1,"status":"compile_failed","source_file":"FWBTEST.LS"}')
    tab.source.setText(str(source));tab.offline_report.setText(str(report))
    before=list(api.calls)
    tab.prepare_program()
    assert tab.plan is None and api.calls==before and not api.writes and not api.loads
    assert 'evidence failed' in tab.review.toPlainText()
    tab.shutdown();tab.close()
