from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fanuc_workbench.backends.base import RobotBackend


@dataclass(slots=True)
class RestoreCandidate:
    path: Path
    relative_path: str
    category: str
    remote_path: str
    default_selected: bool
    warning: str = ""


def _category(path: Path) -> tuple[str,bool,str]:
    n=path.name.upper(); s=path.suffix.upper()
    if s == '.TP': return 'Programs',True,''
    if s == '.LS': return 'Programs (ASCII)',False,'Requires ASCII Program Loader/controller support to load LS.'
    if 'NUMREG' in n and s == '.VR': return 'Numerical Registers',True,''
    if 'POSREG' in n and s == '.VR': return 'Position Registers',True,''
    if ('FRAME' in n or 'FRM' in n) and s == '.VR': return 'Frames',True,''
    if s == '.VR': return 'Application Data',False,'Verify this variable file is intended for restore.'
    if s == '.SV': return 'System Data',False,'Advanced: system-variable restore may affect controller configuration.'
    if s in {'.VA','.DG'}: return 'Listings / Diagnostics',False,'ASCII/diagnostic listing is kept for inspection; not selected for restore.'
    return 'Other Files',False,'Not selected automatically.'


def _remote_map(root: Path) -> dict[str,str]:
    p=root/'backup_manifest.json'; out={}
    if not p.exists(): return out
    try:
        data=json.loads(p.read_text(encoding='utf-8'))
        for rec in data.get('records',[]):
            lp=rec.get('local_path',''); rp=rec.get('remote_path','')
            if lp and rp:
                try: rel=Path(lp).resolve().relative_to(root.resolve()).as_posix()
                except Exception: rel=Path(lp).name
                out[rel]=rp
                out[Path(lp).name]=rp
    except Exception: pass
    return out


def restore_candidates(root: Path, default_root: str='md:') -> list[RestoreCandidate]:
    rmap=_remote_map(root); rows=[]
    for p in root.rglob('*'):
        if not p.is_file() or p.name=='backup_manifest.json' or p.name.endswith('.before_comment_edit'): continue
        rel=p.relative_to(root).as_posix(); cat,sel,warn=_category(p)
        remote=rmap.get(rel) or rmap.get(p.name) or f"{default_root}{p.name}"
        rows.append(RestoreCandidate(p,rel,cat,remote,sel,warn))
    return sorted(rows,key=lambda r:(r.category,r.relative_path.upper()))


def restore_selected(backend: RobotBackend, rows: list[RestoreCandidate], progress=None) -> list[str]:
    progress=progress or (lambda _:None); done=[]
    backend.connect()
    try:
        for row in rows:
            if row.category == 'Listings / Diagnostics':
                raise ValueError(f'Refusing to restore diagnostic/listing file: {row.path.name}')
            progress({'message':f'Uploading {row.relative_path} -> {row.remote_path}'})
            backend.upload(row.path,row.remote_path); done.append(row.remote_path)
    finally: backend.close()
    return done
