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

from fanuc_workbench.extensions.programs_v1_1_0.validation import compare_native_listing,review_validation
from fanuc_workbench.services.native_programs import validate_source


def _read(path,maximum):
    with Path(path).open('rb') as stream: raw=stream.read(maximum+1)
    if len(raw)>maximum: raise ValueError('Native validation input exceeds its byte limit')
    return raw


def _run(arguments,cwd,timeout):
    """Drain merged output into a bounded buffer; no shell or interactive pause."""
    process=subprocess.Popen(arguments,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    output=bytearray();truncated=False
    def drain():
        nonlocal truncated
        while chunk:=process.stdout.read(4096):
            remaining=1024*1024-len(output)
            output.extend(chunk[:remaining])
            if len(chunk)>remaining:
                truncated=True;process.kill();break
    thread=threading.Thread(target=drain,daemon=True);thread.start()
    timed_out=False
    try: process.wait(timeout=timeout)
    except subprocess.TimeoutExpired: timed_out=True;process.kill();process.wait()
    thread.join(timeout=5)
    if thread.is_alive(): raise RuntimeError('Compiler output pipe did not close')
    process.stdout.close()
    return {'returncode':process.returncode,'timed_out':timed_out,'output_truncated':truncated},bytes(output)


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
    support=robot_context/'support'
    if not support.is_dir(): raise ValueError('Robot context needs its support directory')
    compiler_hash=hashlib.sha256(_read(maketp,64*1024*1024)).hexdigest()
    printer_hash=hashlib.sha256(_read(printtp,64*1024*1024)).hexdigest()
    destination=Path(destination).resolve()
    if destination.is_relative_to(robot_context): raise ValueError('Validation destination must be outside the original robot context')
    destination.mkdir(parents=False,exist_ok=False)
    context_manifest=_snapshot_context(robot_context,destination/'context')
    context=destination/'context'
    mapped='\\'+str(context.drive).rstrip(':').lower()+'\\'+str(context.relative_to(context.anchor)) if context.drive else str(context)
    settings['WinOLPC_Util']['Robot']=mapped
    settings['WinOLPC_Util']['Support']=str(context/'support')
    settings['WinOLPC_Util']['Output']=str(destination)
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
        'source_file':name+'.LS','compiled_file':name+'.TP','readback_file':'readback.LS',
        'compile_returncode':None,'print_returncode':None,'compiled_sha256':None,'readback_sha256':None,
        'status':'compile_failed','controller_contacted':False,'controller_written':False,'deployment_prepared':False}
    try:
        state,log=_run([str(maketp),str(destination/(name+'.LS')),str(destination/(name+'.TP')),'/config',str(destination/'robot.ini'),'/ver',version],destination,timeout)
        (destination/'compile.log').write_bytes(log);report['compile']=state;report['compile_returncode']=state['returncode']
        report['compile_diagnostics']=log.decode('utf-8',errors='replace').splitlines()[:200]
        compiled=destination/(name+'.TP')
        if state['returncode']==0 and not state['timed_out'] and not state['output_truncated'] and compiled.is_file():
            tp=_read(compiled,4*1024*1024)
            if not tp: raise ValueError('Compiler produced an empty TP file')
            report['compiled_sha256']=hashlib.sha256(tp).hexdigest();report['status']='compiled_without_matching_readback'
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
