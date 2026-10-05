from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from fanuc_workbench.services.restore import restore_candidates


@dataclass(slots=True)
class ProjectRobot:
    name: str
    host: str = ""
    controller: str = ""
    model: str = ""
    software: str = ""
    notes: str = ""
    backups: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RobotProject:
    name: str
    path: Path
    robots: list[ProjectRobot] = field(default_factory=list)

    @property
    def config_path(self) -> Path: return self.path/'fanuc_project.json'

    def save(self) -> None:
        self.path.mkdir(parents=True,exist_ok=True)
        self.config_path.write_text(json.dumps({'name':self.name,'robots':[asdict(r) for r in self.robots]},indent=2),encoding='utf-8')

    @classmethod
    def load(cls,path: Path) -> 'RobotProject':
        p=path/'fanuc_project.json'; data=json.loads(p.read_text(encoding='utf-8'))
        return cls(data.get('name',path.name),path,[ProjectRobot(**r) for r in data.get('robots',[])])

    def upsert_robot(self, robot: ProjectRobot) -> None:
        for i,r in enumerate(self.robots):
            if r.name.lower()==robot.name.lower(): self.robots[i]=robot; self.save(); return
        self.robots.append(robot); self.save()

    def add_backup(self, robot_name: str, backup: Path) -> Path:
        dest=self.path/'Robots'/robot_name/'Backups'/backup.name
        if backup.resolve()!=dest.resolve():
            if dest.exists(): shutil.rmtree(dest)
            shutil.copytree(backup,dest)
        rob=next((r for r in self.robots if r.name.lower()==robot_name.lower()),None)
        if rob is None: rob=ProjectRobot(robot_name); self.robots.append(rob)
        rel=dest.relative_to(self.path).as_posix()
        if rel not in rob.backups: rob.backups.append(rel)
        self.save(); return dest

    def deployment_package(self, source_backup: Path, destination: Path, categories: set[str] | None=None) -> Path:
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S'); out=destination/f'deployment_{source_backup.name}_{stamp}'; out.mkdir(parents=True)
        categories=categories or {'Programs','Numerical Registers','Position Registers','Frames','Application Data'}
        manifest=[]
        for row in restore_candidates(source_backup):
            if row.category not in categories or not row.default_selected: continue
            target=out/row.path.name; shutil.copy2(row.path,target)
            manifest.append({'file':target.name,'category':row.category,'remote_path':row.remote_path})
        (out/'deployment_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        return out
