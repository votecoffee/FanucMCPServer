from __future__ import annotations
import json,os,shutil
from pathlib import Path

DEFAULT_CANDIDATES=[
    r'C:\Program Files (x86)\FANUC\WinOLPC\bin\ROBOGUIDE.exe',
    r'C:\Program Files\FANUC\WinOLPC\bin\ROBOGUIDE.exe',
]
def discover_roboguide()->Path|None:
    env=os.environ.get('FANUC_ROBOGUIDE_EXE')
    for raw in ([env] if env else [])+DEFAULT_CANDIDATES:
        p=Path(raw)
        if p.is_file():return p
    return None

def stage_backup_for_roboguide(backup:Path,destination:Path)->Path:
    out=Path(destination)/f'{Path(backup).name}_ROBOGUIDE';
    shutil.copytree(backup,out)
    (out/'roboguide_handoff.json').write_text(json.dumps({'source_backup':str(backup),'instructions':'Create/open a virtual controller in ROBOGUIDE, then restore the selected FANUC backup files from this staged folder. FanucMCPServer does not automate unverified ROBOGUIDE UI/COM interfaces.'},indent=2),encoding='utf-8')
    return out

def launch_roboguide(executable:Path|None=None,workcell:Path|None=None):
    raise RuntimeError('Automatic ROBOGUIDE launch is unavailable until application ownership and dirty-workcell recovery are validated. Open ROBOGUIDE separately and use the staged backup; that session remains under your control.')
