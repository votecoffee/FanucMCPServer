from __future__ import annotations
import csv, json, math, re, tempfile, time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.parsers.data import parse_numeric_registers,parse_position_registers,parse_io

WATCH_RE=re.compile(r'^\s*(R|PR|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|F)\s*\[\s*(\d+)(?:\s*,\s*(\d+))?\s*\]\s*$',re.I)
@dataclass(slots=True)
class WatchSpec:
    expression:str
    alias:str=''
@dataclass(slots=True)
class Sample:
    timestamp:float
    values:dict[str,object]
    errors:dict[str,str]=field(default_factory=dict)
    @property
    def iso_time(self):return datetime.fromtimestamp(self.timestamp).isoformat(timespec='milliseconds')

class WorkspaceSampleProvider:
    def __init__(self,root:Path):self.root=Path(root)
    def _find(self,name):return next((p for p in self.root.rglob(name) if p.is_file()),None)
    def sample(self,specs:Iterable[WatchSpec])->Sample:
        nr={r.index:r for r in parse_numeric_registers(self._find('NUMREG.VA'))} if self._find('NUMREG.VA') else {}
        prs={(r.group,r.index):r for r in parse_position_registers(self._find('POSREG.VA'))} if self._find('POSREG.VA') else {}
        ios={}
        p=self._find('IOSTATE.DG')
        if p:
            for r in parse_io(p):
                typ=r.io_type.replace('DIN','DI').replace('DOUT','DO').replace('GIN','GI').replace('GOUT','GO').replace('AIN','AI').replace('AOUT','AO').replace('FLG','F')
                ios[(typ,r.index)]=r
        vals={};errs={}
        for s in specs:
            k=s.alias or s.expression; m=WATCH_RE.match(s.expression)
            if not m:errs[k]='Unsupported watch expression';continue
            typ=m.group(1).upper();idx=int(m.group(2));group=int(m.group(3) or 1)
            if typ == 'PR' and m.group(3):group,idx=int(m.group(2)),int(m.group(3))
            elif m.group(3):errs[k]='Only PR supports a group,index pair';continue
            if typ=='R':
                r=nr.get(idx); vals[k]=r.value if r else None
                if not r:errs[k]='R not found'
                else:
                    try: available=math.isfinite(float(r.value))
                    except (ValueError, OverflowError): available=False
                    if not available:vals[k]=None;errs[k]='R value is unavailable or nonfinite'
            elif typ=='PR':
                r=prs.get((group,idx)); vals[k]=dict(r.values) if r else None
                if not r:errs[k]='PR not found'
                elif r.unavailable_axes or not r.values:
                    vals[k]=None;errs[k]='PR contains unavailable axes or is uninitialized'
            else:
                r=ios.get((typ,idx));vals[k]=r.value if r else None
                if not r:errs[k]=f'{typ} not found'
        return Sample(time.time(),vals,errs)

class ControllerFileSampleProvider:
    """Bounded polling of controller-generated ASCII/diagnostic files.

    This intentionally avoids undocumented CGI scraping. Controllers differ in which
    files they expose; missing files become per-signal errors rather than writes.
    """
    def __init__(self,backend:RobotBackend,profile:RobotProfile,minimum_interval:float=2.0):
        self.backend=backend;self.profile=profile;self.minimum_interval=max(2.0,float(minimum_interval));self._last=0.0
    def sample(self,specs:Iterable[WatchSpec])->Sample:
        now=time.monotonic()
        if self._last and now-self._last<self.minimum_interval:time.sleep(self.minimum_interval-(now-self._last))
        self._last=time.monotonic()
        with tempfile.TemporaryDirectory(prefix='fanuc_watch_') as td:
            root=Path(td);wanted={'NUMREG.VA','POSREG.VA','IOSTATE.DG'}
            with self.backend:
                for name in wanted:
                    for dev in self.profile.roots:
                        remote=(dev+name) if dev.endswith(':') else dev.rstrip('/')+'/'+name
                        try:
                            if self.backend.file_exists(remote):self.backend.download(remote,root/name);break
                        except Exception:continue
            return WorkspaceSampleProvider(root).sample(specs)

class TrendRecorder:
    def __init__(self):self.samples:list[Sample]=[]
    def add(self,sample:Sample):self.samples.append(sample)
    def clear(self):self.samples.clear()
    def export_csv(self,path:Path):
        keys=sorted({k for s in self.samples for k in s.values})
        with Path(path).open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['timestamp',*keys])
            for s in self.samples:w.writerow([s.iso_time,*[json.dumps(s.values.get(k),sort_keys=True) if isinstance(s.values.get(k),(dict,list)) else s.values.get(k) for k in keys]])

@dataclass(slots=True)
class TimelineEvent:
    timestamp:float; signal:str; old:object; new:object
    @property
    def iso_time(self):return datetime.fromtimestamp(self.timestamp).isoformat(timespec='milliseconds')
class TimelineRecorder:
    def __init__(self):self.events:list[TimelineEvent]=[];self._last={}
    def add(self,sample:Sample):
        for k,v in sample.values.items():
            if k in sample.errors or v is None:continue
            if k in self._last and self._last[k]!=v:self.events.append(TimelineEvent(sample.timestamp,k,self._last[k],v))
            self._last[k]=v
    def export_csv(self,path:Path):
        with Path(path).open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['timestamp','signal','old','new'])
            for e in self.events:w.writerow([e.iso_time,e.signal,e.old,e.new])

@dataclass(slots=True)
class CycleEvent:
    timestamp:float; state:str; detail:str=''
class CycleRecorder:
    def __init__(self):self.events:list[CycleEvent]=[]
    def mark(self,state:str,detail:str=''):self.events.append(CycleEvent(time.time(),state,detail))
    def add_sample_state(self,sample:Sample,state_key:str):
        v=sample.values.get(state_key)
        if v is not None and (not self.events or self.events[-1].state!=str(v)):self.events.append(CycleEvent(sample.timestamp,str(v),state_key))
    def export_json(self,path:Path)->Path:
        Path(path).write_text(json.dumps([asdict(e) for e in self.events],indent=2),encoding='utf-8');return Path(path)

def profile_cycle(events:list[CycleEvent])->dict[str,float]:
    out={}
    for a,b in zip(events,events[1:]):out[a.state]=out.get(a.state,0.0)+max(0.0,b.timestamp-a.timestamp)
    return out

def compare_cycle_profiles(a:dict[str,float],b:dict[str,float])->list[tuple[str,float,float,float]]:
    return [(k,a.get(k,0.0),b.get(k,0.0),b.get(k,0.0)-a.get(k,0.0)) for k in sorted(set(a)|set(b))]
