import hashlib
import json
from pathlib import Path
import sys

import pytest

from fanuc_workbench.services import native_validation
from fanuc_workbench.services.program_packages import build_change_package
from test_program_logic import listing
from test_program_review import inventory


def setup_tools(root):
    maker,printer,config=root/'maketp.exe',root/'printtp.exe',root/'robot.ini'
    maker.write_bytes(b'Synthetic compiler');printer.write_bytes(b'Synthetic printer')
    config.write_text('[WinOLPC_Util]\nRobot=old\nVersion=V9.40-1\nPath=profile\nSupport=old\nOutput=old\n')
    context=root/'source_context';context.mkdir();(context/'support').mkdir();(context/'support/config.dat').write_bytes(b'synthetic context')
    return {'maketp':maker,'printtp':printer,'configuration':config,'robot_context':context,'version':'V9.40-1'}


def test_isolated_compile_readback_hash_review_preserves_sources_and_never_deploys(tmp_path,monkeypatch):
    path=listing(tmp_path,body='1: R[1]=1 ;')
    original=path.read_bytes();options=setup_tools(tmp_path);commands=[]
    def run(arguments,cwd,timeout):
        commands.append(arguments)
        if Path(arguments[0]).name=='maketp.exe': (cwd/'MAIN.TP').write_bytes(b'Synthetic compiled TP')
        else: (cwd/'readback.LS').write_bytes(original)
        return {'returncode':0,'timed_out':False,'output_truncated':False},b'Synthetic compiler log\n'
    monkeypatch.setattr(native_validation,'_run',run)
    destination=tmp_path/'validation'
    report=native_validation.validate_offline(path,destination,**options)
    assert report['status']=='offline_checks_passed' and not report['deployment_prepared']
    assert path.read_bytes()==original and (destination/'MAIN.LS').read_bytes()==original
    assert all('/p' not in arguments and '/ver' in arguments and '/config' in arguments for arguments in commands)
    checked=native_validation.review_validation(destination,'MAIN.LS','validation.json',compiled_path='MAIN.TP',readback_path='readback.LS')
    assert checked['preserved_checks_pass'] and not checked['compiler_execution_authenticated']
    (destination/'MAIN.TP').write_bytes(b'Changed')
    assert not native_validation.review_validation(destination,'MAIN.LS','validation.json',compiled_path='MAIN.TP',readback_path='readback.LS')['preserved_checks_pass']


@pytest.mark.parametrize('result,output',[(1,b'Error: synthetic compile diagnostic'),(0,b'Success without binary')])
def test_failed_or_missing_output_keeps_diagnostics_and_never_invokes_printer(tmp_path,monkeypatch,result,output):
    path=listing(tmp_path);options=setup_tools(tmp_path);calls=[]
    def run(arguments,cwd,timeout):
        calls.append(arguments)
        return {'returncode':result,'timed_out':False,'output_truncated':False},output
    monkeypatch.setattr(native_validation,'_run',run)
    destination=tmp_path/'failed'
    report=native_validation.validate_offline(path,destination,**options)
    assert report['status']=='compile_failed' and len(calls)==1
    assert (destination/'compile.log').read_bytes()==output
    assert report['compile_diagnostics'] and report['print_returncode'] is None


def test_mismatched_readback_does_not_pass_and_existing_destination_is_protected(tmp_path,monkeypatch):
    path=listing(tmp_path);original=path.read_bytes();options=setup_tools(tmp_path)
    def run(arguments,cwd,timeout):
        if Path(arguments[0]).name=='maketp.exe': (cwd/'MAIN.TP').write_bytes(b'compiled')
        else: (cwd/'readback.LS').write_bytes(original.replace(b'DI[1]',b'DI[2]'))
        return {'returncode':0,'timed_out':False,'output_truncated':False},b'log'
    monkeypatch.setattr(native_validation,'_run',run)
    destination=tmp_path/'different'
    assert native_validation.validate_offline(path,destination,**options)['status']=='compiled_without_matching_readback'
    with pytest.raises(FileExistsError): native_validation.validate_offline(path,destination,**options)


def test_process_timeout_and_output_bounds_are_enforced_without_shell(tmp_path):
    result,log=native_validation._run([sys.executable,'-c','import time; print("started",flush=True); time.sleep(10)'],tmp_path,.2)
    assert result['timed_out'] and result['returncode']!=0
    result,log=native_validation._run([sys.executable,'-c','import sys; sys.stdout.write("x"*1200000)'],tmp_path,5)
    assert result['output_truncated'] and len(log)==1024*1024


def test_change_package_is_transitive_hash_bound_and_cannot_overwrite_existing_files(tmp_path):
    listing(tmp_path,body='1: CALL CHILD ;\n2: R[1]=DI[1] ;')
    listing(tmp_path,'CHILD','1: END ;')
    inventory(tmp_path,['R[1]'],['DI[1]'])
    destination=tmp_path/'package'
    report=build_change_package(tmp_path,['MAIN.LS','CHILD.LS'],destination,entry_programs=['MAIN'],inventory_path='inventory.json')
    manifest=json.loads((destination/'package.json').read_text())
    assert report['status']=='dependency_review_complete' and report['files']==3
    for row in manifest['files']:
        assert hashlib.sha256((destination/row['path']).read_bytes()).hexdigest()==row['sha256']
    assert not manifest['deployment_authorized'] and manifest['native_validation_required']
    with pytest.raises(FileExistsError): build_change_package(tmp_path,['MAIN.LS','CHILD.LS'],destination,entry_programs=['MAIN'],inventory_path='inventory.json')


def test_incomplete_package_requires_explicit_choice_and_remains_review_only(tmp_path):
    listing(tmp_path,body='1: CALL ABSENT ;')
    destination=tmp_path/'package'
    with pytest.raises(ValueError,match='missing or unresolved'):
        build_change_package(tmp_path,['MAIN.LS'],destination,entry_programs=['MAIN'])
    assert not destination.exists()
    result=build_change_package(tmp_path,['MAIN.LS'],destination,entry_programs=['MAIN'],allow_incomplete=True)
    assert result['status']=='incomplete_review_only'
