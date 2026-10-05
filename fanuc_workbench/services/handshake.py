from __future__ import annotations
import csv,json
from dataclasses import asdict,dataclass
from pathlib import Path
from fanuc_workbench.services.monitoring import TimelineEvent
@dataclass(slots=True)
class HandshakeSignal:
    name:str; robot_signal:str; plc_tag:str=''; direction:str=''; expected_peer:str=''

def load_handshake(path:Path)->list[HandshakeSignal]:
    if not Path(path).exists():return []
    return [HandshakeSignal(**x) for x in json.loads(Path(path).read_text(encoding='utf-8'))]
def save_handshake(path:Path,rows:list[HandshakeSignal])->Path:
    Path(path).write_text(json.dumps([asdict(x) for x in rows],indent=2),encoding='utf-8');return Path(path)
def analyze_handshake(events:list[TimelineEvent],rows:list[HandshakeSignal],window_s=2.0):
    out=[]
    by={r.robot_signal:r for r in rows}
    for e in events:
        r=by.get(e.signal)
        if not r or not r.expected_peer:continue
        peer=next((x for x in events if x.signal==r.expected_peer and x.timestamp>=e.timestamp and x.timestamp-e.timestamp<=window_s),None)
        out.append((r.name,e.iso_time,'OK' if peer else 'Timeout',peer.iso_time if peer else f'No {r.expected_peer} within {window_s:.1f}s'))
    return out
