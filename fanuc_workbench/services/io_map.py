from __future__ import annotations
import csv, json
from pathlib import Path


def load_plc_map(root: Path) -> dict[str,str]:
    p=root/'.fanuc_workbench_io_map.json'
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {}


def save_plc_map(root: Path,mapping: dict[str,str]) -> None:
    (root/'.fanuc_workbench_io_map.json').write_text(json.dumps(mapping,indent=2),encoding='utf-8')


def export_io_csv(path: Path, rows: list[tuple[str,str,str,str,str,str,str]]) -> Path:
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(['Robot I/O','Description','Value','Rack','Slot','Start','PLC Tag']); w.writerows(rows)
    return path
