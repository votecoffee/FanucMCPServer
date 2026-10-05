from __future__ import annotations
import hashlib,json,shutil
from dataclasses import asdict,dataclass,field
from datetime import datetime
from pathlib import Path
from fanuc_workbench.services.compare import compare_workspaces

@dataclass(slots=True)
class ChecklistItem:
    name:str; done:bool=False; note:str=''
DEFAULT_ITEMS=['Robot named correctly','IP address assigned','Backup taken','UFRAMEs documented','UTOOLs documented','Payloads configured','I/O comments complete','PLC handshake validated','DCS baseline captured','Mastering baseline captured','Final production backup']

def file_hashes(root:Path):
    out={}
    for p in root.rglob('*'):
        if p.is_file():out[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return out

def create_baseline(workspace:Path,destination:Path,name='commissioned')->Path:
    out=Path(destination)/name
    if out.exists():shutil.rmtree(out)
    shutil.copytree(workspace,out)
    (out/'baseline_manifest.json').write_text(json.dumps({'name':name,'created_at':datetime.now().isoformat(timespec='seconds'),'source':str(workspace),'sha256':file_hashes(out)},indent=2),encoding='utf-8')
    return out

def compare_baseline(baseline:Path,current:Path):return compare_workspaces(baseline,current)

def load_checklist(path:Path):
    if Path(path).exists():return [ChecklistItem(**x) for x in json.loads(Path(path).read_text(encoding='utf-8'))]
    return [ChecklistItem(x) for x in DEFAULT_ITEMS]
def save_checklist(path:Path,items:list[ChecklistItem])->Path:
    Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_text(json.dumps([asdict(x) for x in items],indent=2),encoding='utf-8');return Path(path)
