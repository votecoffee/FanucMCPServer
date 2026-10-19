"""Local source-preserving packages from reviewed dependency requirements."""
import hashlib
import json
from pathlib import Path

from fanuc_workbench.extensions.programs_v1_1_0.diagnostics import selected_bytes
from fanuc_workbench.services.program_analysis import dependency_review


def build_change_package(root,paths,destination,*,entry_programs,inventory_path=None,allow_incomplete=False):
    if type(allow_incomplete) is not bool: raise ValueError('Explicit boolean incomplete-package choice required')
    report=dependency_review(root,paths,entry_programs=entry_programs,inventory_path=inventory_path)
    if not report['explicit_dependencies_accounted_for'] and not allow_incomplete:
        raise ValueError('Dependency review has missing or unresolved requirements; inspect missing/issues first')
    sources=[]
    for file in report['program_files']:
        raw=selected_bytes(root,file['path'],1024*1024)
        if hashlib.sha256(raw).hexdigest()!=file['source_sha256']: raise ValueError('Program revision changed during package review')
        sources.append(('programs/'+file['path'],raw))
    if inventory_path:
        raw=selected_bytes(root,inventory_path)
        if hashlib.sha256(raw).hexdigest()!=report['inventory_sha256']: raise ValueError('Dependency inventory changed during review')
        sources.append(('evidence/inventory.json',raw))
    # Freeze every requirement, including rows beyond the first report page.
    rows=[];offset=0
    while True:
        page=dependency_review(root,paths,entry_programs=entry_programs,inventory_path=inventory_path,offset=offset)
        if page['program_files']!=report['program_files'] or page['inventory_sha256']!=report['inventory_sha256']:
            raise ValueError('Dependency inputs changed during package review')
        rows.extend(page['items'])
        if page['next_offset'] is None: break
        offset=page['next_offset']
    destination=Path(destination)
    destination.mkdir(parents=False,exist_ok=False)
    files=[]
    for relative,raw in sources:
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(raw)
        if target.read_bytes()!=raw: raise OSError('Package copy readback differs')
        files.append({'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    manifest={'format':'fanuc-reviewed-change-package','schema_version':1,
              'status':'dependency_review_complete' if report['explicit_dependencies_accounted_for'] else 'incomplete_review_only',
              'files':files,'review':{**report,'items':rows},'deployment_authorized':False,'native_validation_required':True}
    with (destination/'package.json').open('x',encoding='utf-8') as stream: json.dump(manifest,stream,indent=2,allow_nan=False)
    return {'path':str(destination),'files':len(files),'status':manifest['status'],'controller_contacted':False}
