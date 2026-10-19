import hashlib
import json

import pytest
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


@pytest.mark.parametrize('status',[
    'validation_error','compile_failed','compiled_without_matching_readback',
    'toolchain_unavailable','offline_checks_passed',
])
def test_matching_hashes_require_successful_validation_before_dispatch(native_case,tmp_path,monkeypatch,status):
    from fanuc_workbench.services.native_validation import review_validation
    bridge,_,api=native_case
    app=QApplication.instance() or QApplication([])
    class Owner:thread_pool=QThreadPool()
    tab=NativeControllerTab(Owner());tab.bridge=bridge;tab.robot.addItem('Synthetic','r1')
    source=tmp_path/'FWBTEST.LS';source.write_bytes(SOURCE)
    (tmp_path/'FWBTEST.TP').write_bytes(b'Synthetic TP')
    (tmp_path/'readback.LS').write_bytes(SOURCE)
    report=tmp_path/'validation.json'
    report.write_text(json.dumps({'format':'fanuc-native-offline-validation','schema_version':1,
        'status':status,'source_file':'FWBTEST.LS','compiled_file':'FWBTEST.TP','readback_file':'readback.LS',
        'source_sha256':hashlib.sha256(SOURCE).hexdigest(),'configuration_sha256':'1'*64,
        'compiled_sha256':hashlib.sha256(b'Synthetic TP').hexdigest(),
        'readback_sha256':hashlib.sha256(SOURCE).hexdigest(),'compile_returncode':0,'print_returncode':0}))
    assert review_validation(tmp_path,source.name,report.name,
        compiled_path='FWBTEST.TP',readback_path='readback.LS')['preserved_checks_pass']
    tab.source.setText(str(source));tab.offline_report.setText(str(report))
    dispatched=[]
    monkeypatch.setattr(tab,'run',lambda work,success:dispatched.append(work))
    before=list(api.calls)
    tab.prepare_program()
    assert bool(dispatched)==(status=='offline_checks_passed')
    assert api.calls==before and not api.writes and not api.loads
    if status!='offline_checks_passed':
        assert 'evidence failed' in tab.review.toPlainText()
    tab.shutdown();tab.close()
