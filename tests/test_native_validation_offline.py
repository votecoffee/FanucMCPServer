import configparser
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
    profile=root/'profile';profile.mkdir()
    for name in ('asc2bint.dll','bin2asct.dll'):
        (profile/name).write_bytes(b'Synthetic offline translator component')
    context=root/'source_context';context.mkdir();(context/'support').mkdir();(context/'support/config.dat').write_bytes(b'synthetic context')
    return {'maketp':maker,'printtp':printer,'configuration':config,'robot_context':context,'version':'V9.40-1'}


def test_isolated_compile_readback_hash_review_preserves_sources_and_never_deploys(tmp_path,monkeypatch):
    path=listing(tmp_path,body='1: R[1]=1 ;')
    original=path.read_bytes();options=setup_tools(tmp_path);commands=[]
    def run(arguments,cwd,timeout):
        commands.append(arguments)
        settings=configparser.ConfigParser(interpolation=None)
        settings.read(cwd/'robot.ini')
        staging=Path(settings['WinOLPC_Util']['Output'])
        assert staging.is_dir() and staging.is_relative_to(cwd)
        assert staging!=Path(arguments[1]).parent and staging!=Path(arguments[2]).parent
        assert Path(settings['WinOLPC_Util']['Path'])==tmp_path/'profile'
        if Path(arguments[0]).name=='maketp.exe': (cwd/'MAIN.TP').write_bytes(b'Synthetic compiled TP')
        else: (cwd/'readback.LS').write_bytes(original)
        return {'returncode':0,'timed_out':False,'output_truncated':False},b'Synthetic compiler log\n'
    monkeypatch.setattr(native_validation,'_run',run)
    destination=tmp_path/'validation'
    report=native_validation.validate_offline(path,destination,**options)
    assert report['status']=='offline_checks_passed' and not report['deployment_prepared']
    assert report['compiler_invoked'] and report['printer_invoked']
    assert all(row['sha256'] for row in report['toolchain']['components'])
    assert path.read_bytes()==original and (destination/'MAIN.LS').read_bytes()==original
    assert all('/p' not in arguments and '/ver' in arguments and '/config' in arguments for arguments in commands)
    checked=native_validation.review_validation(destination,'MAIN.LS','validation.json',compiled_path='MAIN.TP',readback_path='readback.LS')
    assert checked['preserved_checks_pass'] and not checked['compiler_execution_authenticated']
    (destination/'MAIN.TP').write_bytes(b'Changed')
    assert not native_validation.review_validation(destination,'MAIN.LS','validation.json',compiled_path='MAIN.TP',readback_path='readback.LS')['preserved_checks_pass']


@pytest.mark.parametrize('missing',['asc2bint.dll','bin2asct.dll'])
def test_missing_native_translator_keeps_actionable_report_without_invoking_tools(tmp_path,monkeypatch,missing):
    path=listing(tmp_path);options=setup_tools(tmp_path)
    (tmp_path/'profile'/missing).unlink()
    def unexpected_run(*args,**kwargs):
        pytest.fail('An incomplete native toolchain must not be invoked')
    monkeypatch.setattr(native_validation,'_run',unexpected_run)
    destination=tmp_path/'unavailable'
    report=native_validation.validate_offline(path,destination,**options)
    assert report['status']=='toolchain_unavailable'
    assert not report['compiler_invoked'] and not report['printer_invoked']
    assert report['compile_returncode'] is None and report['print_returncode'] is None
    assert report['toolchain']['missing_components']==[str(tmp_path/'profile'/missing)]
    assert missing in (destination/'compile.log').read_text()
    assert json.loads((destination/'validation.json').read_text())['status']=='toolchain_unavailable'
    assert path.read_bytes()==(destination/'MAIN.LS').read_bytes()
    assert not report['controller_contacted'] and not report['controller_written']


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


def test_native_tool_entry_does_not_accept_arbitrary_python_commands(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('Native compiler validation must not launch arbitrary executables')
    monkeypatch.setattr(native_validation.subprocess,'Popen',forbidden)
    with pytest.raises(ValueError,match='explicit MakeTP/PrintTP'):
        native_validation._run_process([sys.executable,'-c','print("inert")'],tmp_path,5,contained=True)


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


def test_package_retains_all_requirements_beyond_a_report_page(tmp_path):
    listing(tmp_path,body='\n'.join(f'{index}: R[1]={index} ;' for index in range(1,121)))
    inventory(tmp_path,['R[1]'])
    build_change_package(tmp_path,['MAIN.LS'],tmp_path/'package',entry_programs=['MAIN'],inventory_path='inventory.json')
    report=json.loads((tmp_path/'package/package.json').read_text())['review']
    assert len(report['items'])==120 and report['next_offset'] is None
