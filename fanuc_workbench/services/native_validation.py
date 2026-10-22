"""Isolated opt-in MakeTP/PrintTP pipeline. Never loads a controller program."""
from datetime import datetime,timezone
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import base64
import sys
import uuid

from fanuc_workbench.extensions.programs_v1_1_0.validation import compare_native_listing,review_validation
from fanuc_workbench.services.native_programs import validate_source


def _read(path,maximum):
    with Path(path).open('rb') as stream: raw=stream.read(maximum+1)
    if len(raw)>maximum: raise ValueError('Native validation input exceeds its byte limit')
    return raw


def _run(arguments,cwd,timeout):
    """Use the private, job-contained offline tool worker; never lose its owner."""
    if type(timeout) not in (int,float) or not 1<=timeout<=60: raise ValueError('Use compiler timeout 1..60 seconds')
    from fanuc_workbench.bridge.extension_process import RpcProcess
    bootstrap=Path(__file__).resolve().parents[1]/'bridge'/'native_tool_worker.py'
    worker=RpcProcess([sys.executable,'-B',str(bootstrap)], {'provider':'native-tools'},
                      operation_timeout=timeout+10)
    try:
        result=worker.call('run',{'arguments':arguments,'cwd':str(cwd),'timeout':timeout},{})
        raw=base64.b64decode(result['output_base64'],validate=True)
        if len(raw)>1024*1024: raise ValueError('Native tool output exceeds its byte bound')
        return result['state'],raw
    finally:
        worker.close()


def _run_process(arguments,cwd,timeout,*,contained=False):
    """Only the activated owned tool worker calls this compiler launch path."""
    if contained is not True: raise RuntimeError('Use the owned offline tool worker')
    if (not isinstance(arguments,list) or not 1<=len(arguments)<=32
            or any(not isinstance(value,str) or '\0' in value or len(value)>32768 for value in arguments)
            or not Path(arguments[0]).is_absolute() or not Path(arguments[0]).is_file()
            or Path(arguments[0]).name.lower() not in {'maketp.exe','printtp.exe'}):
        raise ValueError('Use an explicit MakeTP/PrintTP executable and bounded argument list')
    if type(timeout) not in (int,float) or not 1<=timeout<=60: raise ValueError('Use compiler timeout 1..60 seconds')
    folder=Path(cwd).resolve(strict=True)
    if not folder.is_dir(): raise ValueError('Use an existing isolated compiler directory')
    log_path=folder/('native-tool-'+uuid.uuid4().hex+'.log')
    from fanuc_workbench.bridge.resource_context import resource_context
    from fanuc_workbench.bridge.process_identity import creation_time
    claim=resource_context().begin('compiler-process')
    process=None;output=bytearray();truncated=False;drain_errors=[]
    def drain():
        nonlocal truncated
        try:
            with log_path.open('xb') as log:
                while chunk:=process.stdout.read(4096):
                    remaining=1024*1024-len(output)
                    retained=chunk[:remaining]
                    output.extend(retained);log.write(retained);log.flush()
                    if len(chunk)>remaining:
                        truncated=True;process.kill();break
                log.flush();os.fsync(log.fileno())
        except Exception as exc:
            drain_errors.append(type(exc).__name__)
    try:
        # The activated tool worker is already inside its parent's private job;
        # native descendants inherit containment before they execute.
        process=subprocess.Popen(arguments,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                 close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        claim.bind(process.pid,creation_time(process._handle))
        thread=threading.Thread(target=drain,daemon=True);thread.start()
        timed_out=False
        try: process.wait(timeout=timeout)
        except subprocess.TimeoutExpired: timed_out=True;process.kill();process.wait(timeout=5)
        thread.join(timeout=5)
        if thread.is_alive() or drain_errors: raise RuntimeError('Compiler output capture did not finish cleanly')
        process.stdout.close()
        claim.exited('Observed compiler root exit; containing tool-worker job owns remaining descendants')
        return {'returncode':process.returncode,'timed_out':timed_out,'output_truncated':truncated,
                'output_log':log_path.name},bytes(output)
    except BaseException:
        claim.uncertain('Offline compiler launch/capture is unverified; inspect preserved validation inputs and logs')
        # The caller closes the containing owned worker job; no PID-tree scans.
        raise


def _snapshot_context(source,destination):
    paths=[];directories=[];total=0;manifest=[]
    for path in source.rglob('*'):
        if path.resolve()!=path.absolute(): raise ValueError('Robot context contains redirected paths')
        if path.is_dir():
            directories.append(path)
            if len(directories)>10000: raise ValueError('Robot context exceeds directory bound')
            continue
        if not path.is_file(): raise ValueError('Unsupported robot context entry')
        total+=path.stat().st_size;paths.append(path)
        if len(paths)>10000 or total>512*1024*1024: raise ValueError('Robot context exceeds 10000 files / 512 MiB')
    destination.mkdir()
    for path in sorted(directories): (destination/path.relative_to(source)).mkdir(parents=True,exist_ok=True)
    copied=0
    for path in sorted(paths):
        relative=path.relative_to(source);target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256();size=0
        with path.open('rb') as reader,target.open('xb') as writer:
            while chunk:=reader.read(1024*1024):
                size+=len(chunk);copied+=len(chunk)
                if copied>512*1024*1024: raise ValueError('Robot context grew beyond its byte bound')
                digest.update(chunk);writer.write(chunk)
        manifest.append({'path':relative.as_posix(),'bytes':size,'sha256':digest.hexdigest()})
    return manifest


def _inspect_toolchain(profile):
    components=[]
    for name in ('asc2bint.dll','bin2asct.dll'):
        path=profile/name
        present=path.is_file()
        components.append({'path':str(path),'present':present,
            'sha256':hashlib.sha256(_read(path,64*1024*1024)).hexdigest() if present else None})
    return {'profile_path':str(profile),'components':components,
            'missing_components':[row['path'] for row in components if not row['present']]}


def validate_offline(source,destination,*,maketp,printtp,configuration,robot_context,version,timeout=30):
    if not isinstance(version,str) or not re.fullmatch(r'V\d{1,2}\.\d{2}-\d{1,3}',version): raise ValueError('Select an explicit installed WinOLPC core version')
    if type(timeout) not in (int,float) or not 1<=timeout<=60: raise ValueError('Use compiler timeout 1..60 seconds')
    source,maketp,printtp,configuration,robot_context=map(Path,(source,maketp,printtp,configuration,robot_context))
    robot_context=robot_context.resolve()
    if not robot_context.is_dir(): raise ValueError('Select an existing offline robot context directory')
    if maketp.name.lower()!='maketp.exe' or printtp.name.lower()!='printtp.exe': raise ValueError('Select explicit MakeTP and PrintTP executable paths')
    if not maketp.is_absolute() or not printtp.is_absolute() or not maketp.is_file() or not printtp.is_file(): raise ValueError('Compiler executables must exist at absolute paths')
    raw=_read(source,1024*1024);identity=validate_source(raw)
    config=_read(configuration,1024*1024)
    if not config: raise ValueError('Robot configuration is empty')
    settings=configparser.ConfigParser(interpolation=None)
    settings.optionxform=str
    settings.read_string(config.decode('utf-8-sig'))
    if settings.sections()!=['WinOLPC_Util'] or set(settings['WinOLPC_Util'])!={'Robot','Version','Path','Support','Output'}:
        raise ValueError('Use an explicit WinOLPC_Util Robot/Version/Path/Support/Output configuration')
    if settings['WinOLPC_Util']['Version'].upper()!=version.upper(): raise ValueError('Selected core and configuration versions differ')
    profile_text=settings['WinOLPC_Util']['Path'].strip()
    if not profile_text: raise ValueError('Select an explicit WinOLPC profile directory in the configuration Path')
    profile=Path(profile_text)
    profile=(profile if profile.is_absolute() else configuration.resolve().parent/profile).resolve()
    toolchain=_inspect_toolchain(profile)
    support=robot_context/'support'
    if not support.is_dir(): raise ValueError('Robot context needs its support directory')
    compiler_hash=hashlib.sha256(_read(maketp,64*1024*1024)).hexdigest()
    printer_hash=hashlib.sha256(_read(printtp,64*1024*1024)).hexdigest()
    destination=Path(destination).resolve()
    if destination.is_relative_to(robot_context): raise ValueError('Validation destination must be outside the original robot context')
    destination.mkdir(parents=False,exist_ok=False)
    context_manifest=_snapshot_context(robot_context,destination/'context')
    context=destination/'context'
    staging=destination/'compiler-output'
    staging.mkdir()
    mapped='\\'+str(context.drive).rstrip(':').lower()+'\\'+str(context.relative_to(context.anchor)) if context.drive else str(context)
    settings['WinOLPC_Util']['Robot']=mapped
    settings['WinOLPC_Util']['Path']=str(profile)
    settings['WinOLPC_Util']['Support']=str(context/'support')
    settings['WinOLPC_Util']['Output']=str(staging)
    output=io.StringIO();settings.write(output);effective_config=output.getvalue().encode('utf-8')
    name=identity['program']
    inputs={name+'.LS':raw,'robot.ini':effective_config,'source_robot.ini':config}
    for filename,data in inputs.items():
        with (destination/filename).open('xb') as stream: stream.write(data)
    report={'format':'fanuc-native-offline-validation','schema_version':1,'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':identity['source_sha256'],'configuration_sha256':hashlib.sha256(effective_config).hexdigest(),
        'original_configuration_sha256':hashlib.sha256(config).hexdigest(),
        'context_manifest_sha256':hashlib.sha256(json.dumps(context_manifest,sort_keys=True).encode()).hexdigest(),
        'context_files':len(context_manifest),'context_capture_is_atomic':False,
        'compiler_sha256':compiler_hash,'printer_sha256':printer_hash,'core_version':version,
        'toolchain':toolchain,'compiler_invoked':False,'printer_invoked':False,
        'source_file':name+'.LS','compiled_file':name+'.TP','readback_file':'readback.LS',
        'compile_returncode':None,'print_returncode':None,'compiled_sha256':None,'readback_sha256':None,
        'status':'compile_failed','controller_contacted':False,'controller_written':False,'deployment_prepared':False}
    try:
        if toolchain['missing_components']:
            report['status']='toolchain_unavailable'
            report['compile_diagnostics']=['Selected WinOLPC profile is missing native translator components:',
                *toolchain['missing_components'],'Install or select a compatible complete WinOLPC toolchain; no tools were invoked.']
            (destination/'compile.log').write_text('\n'.join(report['compile_diagnostics'])+'\n',encoding='utf-8')
        else:
            report['compiler_invoked']=True
            state,log=_run([str(maketp),str(destination/(name+'.LS')),str(destination/(name+'.TP')),'/config',str(destination/'robot.ini'),'/ver',version],destination,timeout)
            (destination/'compile.log').write_bytes(log);report['compile']=state;report['compile_returncode']=state['returncode']
            report['compile_diagnostics']=log.decode('utf-8',errors='replace').splitlines()[:200]
            compiled=destination/(name+'.TP')
            if state['returncode']==0 and not state['timed_out'] and not state['output_truncated'] and compiled.is_file():
                tp=_read(compiled,4*1024*1024)
                if not tp: raise ValueError('Compiler produced an empty TP file')
                report['compiled_sha256']=hashlib.sha256(tp).hexdigest();report['status']='compiled_without_matching_readback'
                report['printer_invoked']=True
                state,log=_run([str(printtp),str(destination/(name+'.TP')),str(destination/'readback.LS'),'/config',str(destination/'robot.ini'),'/ver',version],destination,timeout)
                (destination/'print.log').write_bytes(log);report['print']=state;report['print_returncode']=state['returncode']
                report['print_diagnostics']=log.decode('utf-8',errors='replace').splitlines()[:200]
                readback=destination/'readback.LS'
                if state['returncode']==0 and not state['timed_out'] and not state['output_truncated'] and readback.is_file():
                    actual=_read(readback,1024*1024)
                    report['readback_sha256']=hashlib.sha256(actual).hexdigest()
                    report['listing_comparison']=compare_native_listing(raw,actual)
                    if actual and report['listing_comparison']['matches']: report['status']='offline_checks_passed'
        if _read(source,1024*1024)!=raw or _read(configuration,1024*1024)!=config:
            raise ValueError('Original source or configuration changed during validation')
        if (destination/(name+'.LS')).read_bytes()!=raw or (destination/'robot.ini').read_bytes()!=effective_config:
            raise ValueError('Validation inputs changed during compiler execution')
        for item in context_manifest:
            with (robot_context/item['path']).open('rb') as reader:
                if hashlib.file_digest(reader,'sha256').hexdigest()!=item['sha256']:
                    raise ValueError('Original robot context changed during offline validation')
    except Exception as exc:
        report['status']='validation_error';report['error_type']=type(exc).__name__
        raise
    finally:
        with (destination/'context_manifest.json').open('x',encoding='utf-8') as stream: json.dump(context_manifest,stream,indent=2)
        with (destination/'validation.json').open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2,allow_nan=False)
    return {'path':str(destination),**report,'scope':'Offline MakeTP/PrintTP evidence; controller readback and deployment require separate preflight'}


__all__=['validate_offline','review_validation']
