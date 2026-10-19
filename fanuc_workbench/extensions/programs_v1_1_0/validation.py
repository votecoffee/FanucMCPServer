"""Read-only checking of preserved offline compiler evidence (MIT)."""
import hashlib
import re

from .diagnostics import read_json,selected_bytes
from .review import GENERATED,tokens


def normalized_listing(raw):
    text=raw.decode('ascii')
    rows=[]
    for line in text.splitlines():
        if GENERATED.match(line): continue
        row=tokens(re.sub(r'^\s*\d+\s*:\s*','',line))
        if row: rows.append(row)
    return rows


def compare_native_listing(source,readback):
    expected,actual=normalized_listing(source),normalized_listing(readback)
    return {'matches':expected==actual,'scope':'row/token equality excluding generated listing metadata; not runtime equivalence'}


def review_validation(root,source_path,report_path,*,compiled_path=None,readback_path=None):
    source=selected_bytes(root,source_path,1024*1024)
    report,report_hash=read_json(root,report_path)
    if (not isinstance(report,dict) or report.get('format')!='fanuc-native-offline-validation'
        or type(report.get('schema_version')) is not int or report['schema_version']!=1):
        raise ValueError('Expected version 1 native offline validation evidence')
    checks={'source_sha256_matches':report.get('source_sha256')==hashlib.sha256(source).hexdigest(),
            'reported_compile_success':type(report.get('compile_returncode')) is int and report['compile_returncode']==0,
            'reported_print_success':type(report.get('print_returncode')) is int and report['print_returncode']==0,
            'reported_configuration_hash_present':isinstance(report.get('configuration_sha256'),str) and re.fullmatch(r'[a-f0-9]{64}',report['configuration_sha256']) is not None,
            'compiled_sha256_matches':False,'readback_sha256_matches':False,'normalized_listing_matches':False}
    if compiled_path:
        compiled=selected_bytes(root,compiled_path,4*1024*1024)
        checks['compiled_sha256_matches']=bool(compiled) and report.get('compiled_sha256')==hashlib.sha256(compiled).hexdigest()
    if readback_path:
        readback=selected_bytes(root,readback_path,1024*1024)
        checks['readback_sha256_matches']=bool(readback) and report.get('readback_sha256')==hashlib.sha256(readback).hexdigest()
        checks['normalized_listing_matches']=compare_native_listing(source,readback)['matches']
    return {'checks':checks,'preserved_checks_pass':all(checks.values()),'reported_status':report.get('status'),
            'report_sha256':report_hash,'source_sha256':hashlib.sha256(source).hexdigest(),
            'readback_kind':'offline_printtp','compiler_execution_authenticated':False,
            'controller_readback_verified':False,'deployment_preflight_performed':False,
            'target_configuration_compatibility_verified':False,
            'controller_contacted':False,'controller_written':False,'runtime_behavior_verified':False}
