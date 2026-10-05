from __future__ import annotations
import json,shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from fanuc_workbench.services.diagnostics import health_summary,scan_alarms,mastering_evidence,dcs_evidence
from fanuc_workbench.services.workspace import scan_comments,scan_positions,scan_variables
from fanuc_workbench.services.monitoring import Sample

def capture_snapshot(workspace:Path,destination:Path,live_sample:Sample|None=None,note='')->Path:
    out=Path(destination)/f'snapshot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    data={'created_at':datetime.now().isoformat(timespec='seconds'),'workspace':str(workspace),'note':note,'health':health_summary(workspace),
          'alarms':[asdict(x)|{'source':str(x.source)} for x in scan_alarms(workspace)][-100:],
          'comments':[{'kind':x.kind,'id':x.identifier,'comment':x.comment,'file':str(x.path)} for x in scan_comments(workspace)],
          'positions':[{'program':x.program,'index':x.index,'group':x.group,'uf':x.user_frame,'ut':x.user_tool,'values':x.values} for x in scan_positions(workspace)],
          'mastering_evidence':[(str(p),n,l) for p,n,l in mastering_evidence(workspace)],'dcs_evidence':[(str(p),n,l) for p,n,l in dcs_evidence(workspace)]}
    if live_sample:data['live_sample']={'timestamp':live_sample.timestamp,'values':live_sample.values,'errors':live_sample.errors}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(data,indent=2,default=str),encoding='utf-8');return out

def compare_snapshots(a:Path,b:Path):
    x=json.loads(Path(a).read_text(encoding='utf-8'));y=json.loads(Path(b).read_text(encoding='utf-8'));changes=[]
    for section in ('health','live_sample'):
        if x.get(section)!=y.get(section):changes.append((section,x.get(section),y.get(section)))
    xa={(r['kind'],r['id']):r.get('comment','') for r in x.get('comments',[])};ya={(r['kind'],r['id']):r.get('comment','') for r in y.get('comments',[])}
    for k in sorted(set(xa)|set(ya)):
        if xa.get(k)!=ya.get(k):changes.append((f'{k[0]} {k[1]} comment',xa.get(k),ya.get(k)))
    return changes
