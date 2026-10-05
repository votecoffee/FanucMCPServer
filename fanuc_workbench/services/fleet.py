from __future__ import annotations
import json
from dataclasses import asdict,dataclass,field
from datetime import datetime,timedelta
from pathlib import Path
from fanuc_workbench.backends.factory import create_backend
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.backup import BackupService
from fanuc_workbench.services.compare import compare_workspaces

@dataclass(slots=True)
class FleetRobot:
    profile:RobotProfile; enabled:bool=True; interval_minutes:int=1440; last_backup:str=''; last_error:str=''
@dataclass(slots=True)
class FleetResult:
    robot:str; status:str; backup:str=''; changed_files:int=0; message:str=''

class FleetManager:
    def __init__(self,path:Path):self.path=Path(path);self.robots:list[FleetRobot]=[]
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text(json.dumps({'robots':[{'profile':r.profile.to_dict(),'enabled':r.enabled,'interval_minutes':r.interval_minutes,'last_backup':r.last_backup,'last_error':r.last_error} for r in self.robots]},indent=2),encoding='utf-8')
    def load(self):
        if not self.path.exists():return self
        d=json.loads(self.path.read_text(encoding='utf-8'));self.robots=[FleetRobot(RobotProfile.from_dict(x['profile']),x.get('enabled',True),x.get('interval_minutes',1440),x.get('last_backup',''),x.get('last_error','')) for x in d.get('robots',[])];return self
    @staticmethod
    def previous_backup(root:Path,robot_name:str,current:Path|None=None):
        rows=[p for p in Path(root).iterdir() if p.is_dir() and p.name.startswith(robot_name+'_') and p!=current] if Path(root).is_dir() else []
        return max(rows,key=lambda p:p.stat().st_mtime) if rows else None
    def backup_one(self,row:FleetRobot,destination:Path,*,attempt_ascii=True,max_depth=8,progress=None)->FleetResult:
        prev=self.previous_backup(destination,row.profile.name)
        try:
            out=BackupService(create_backend(row.profile),row.profile).run(destination,attempt_ascii=attempt_ascii,max_depth=max_depth,progress=progress)
            changed=len(compare_workspaces(prev,out)) if prev else 0;row.last_backup=str(out);row.last_error='';self.save();return FleetResult(row.profile.name,'OK',str(out),changed,'Changed since previous' if changed else '')
        except Exception as exc:row.last_error=str(exc);self.save();return FleetResult(row.profile.name,'ERROR',message=str(exc))
    def due(self,row:FleetRobot,now:datetime|None=None):
        if not row.enabled:return False
        now=now or datetime.now()
        if not row.last_backup:return True
        try:t=datetime.fromtimestamp(Path(row.last_backup).stat().st_mtime)
        except Exception:return True
        return now-t>=timedelta(minutes=max(1,row.interval_minutes))
