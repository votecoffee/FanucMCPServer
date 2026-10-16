from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fanuc_workbench.parsers.ls import read_text
from fanuc_workbench.services.compare import compare_workspaces
from fanuc_workbench.services.workspace import load_manifest


@dataclass(slots=True)
class AlarmRecord:
    source: Path
    line_number: int
    timestamp: str
    code: str
    severity: str
    message: str

_ALARM = re.compile(r"(?:(?P<date>\d{1,4}[/-]\d{1,2}[/-]\d{1,4})[ T]+(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s+)?(?P<code>[A-Z]{2,10}-\d{3,5})(?:\s*\((?P<sev>[^)]+)\))?\s*[: -]?\s*(?P<msg>.*)",re.I)


def scan_alarm_file(path: Path) -> list[AlarmRecord]:
    """Parse an explicitly selected text log, independent of its local filename."""
    out = []
    for number, line in enumerate(read_text(path).splitlines(), 1):
        match = _ALARM.search(line)
        if match:
            stamp = ' '.join(x for x in (match.group('date'), match.group('time')) if x)
            out.append(AlarmRecord(path, number, stamp, match.group('code').upper(),
                                   match.group('sev') or '', match.group('msg').strip()))
    return out


def scan_alarms(root: Path) -> list[AlarmRecord]:
    out=[]
    for p in root.rglob('*'):
        if not p.is_file(): continue
        name=p.name.upper()
        if not any(k in name for k in ('ERR','ALM','ALARM','HIST')) or p.suffix.upper() not in {'.LS','.DG','.VA','.TXT','.CSV'}: continue
        try: out.extend(scan_alarm_file(p))
        except Exception: continue
    return out


def health_summary(root: Path) -> dict[str,str]:
    manifest=load_manifest(root); files=[p for p in root.rglob('*') if p.is_file()]
    summary={'Robot':manifest.get('robot_name',''),'Host':manifest.get('host',''),'Backup created':manifest.get('created_at',''),
             'Files':str(len(files)),'Programs':str(sum(p.suffix.upper()=='.LS' for p in files)),'Binary TP programs':str(sum(p.suffix.upper()=='.TP' for p in files))}
    patterns={'Controller':re.compile(r'R-?30I[A-Z0-9 +]*|R-?50IA',re.I),'Software':re.compile(r'V\d+\.\d+(?:P/\d+)?',re.I),
              'Robot model':re.compile(r'\b(?:M|R|LR|CRX|ARC MATE|P)-[A-Z0-9/+-]+',re.I)}
    for p in files:
        if p.suffix.upper() not in {'.DG','.VA','.LS','.TXT'}: continue
        try: text=read_text(p)[:200000]
        except Exception: continue
        for key,pat in patterns.items():
            if key not in summary:
                m=pat.search(text)
                if m: summary[key]=m.group(0)
    return summary


def keyword_evidence(root: Path, keywords: tuple[str,...]) -> list[tuple[Path,int,str]]:
    out=[]
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix.upper() not in {'.DG','.VA','.LS','.TXT','.CSV'}: continue
        try: lines=read_text(p).splitlines()
        except Exception: continue
        for n,line in enumerate(lines,1):
            if any(k.upper() in line.upper() for k in keywords): out.append((p,n,line.strip()))
    return out


def mastering_evidence(root: Path):
    return keyword_evidence(root,('$DMR_GRP','MASTER_COUN','MASTER','REF_POS','PULSE'))


def dcs_evidence(root: Path):
    rows=keyword_evidence(root,('DCS','CPC','JPC','SAFE I/O','SIGNATURE'))
    for p in root.rglob('*'):
        if p.is_file() and any(k in p.name.upper() for k in ('DCS','SAFE','CPC','JPC')) and p.suffix.upper() not in {'.DG','.VA','.LS','.TXT','.CSV'}:
            rows.append((p,0,'Binary/special configuration file present; hash-only comparison'))
    return rows


def dcs_compare(old_root: Path,new_root: Path):
    return [c for c in compare_workspaces(old_root,new_root) if any(k in c.relative_path.upper() for k in ('DCS','SAFE','CPC','JPC'))]


def alarm_intelligence(root: Path, timeline_events=None, precursor_window_s: float = 5.0):
    """Summarize recurring alarms and, when timestamps align, common preceding signal changes."""
    alarms=scan_alarms(root); counts={}
    for a in alarms:
        row=counts.setdefault(a.code,{'count':0,'messages':{},'precursors':{}});row['count']+=1;row['messages'][a.message]=row['messages'].get(a.message,0)+1
    if timeline_events:
        def parse_ts(text):
            if not text:return None
            for fmt in ('%Y/%m/%d %H:%M:%S','%m/%d/%Y %H:%M:%S','%Y-%m-%d %H:%M:%S','%m-%d-%Y %H:%M:%S'):
                try:return datetime.strptime(text,fmt).timestamp()
                except ValueError:pass
            return None
        for a in alarms:
            t=parse_ts(a.timestamp)
            if t is None:continue
            for e in timeline_events:
                if 0 <= t-e.timestamp <= precursor_window_s:
                    key=f'{e.signal}: {e.old}→{e.new}';d=counts[a.code]['precursors'];d[key]=d.get(key,0)+1
    out=[]
    for code,row in counts.items():
        msg=max(row['messages'],key=row['messages'].get) if row['messages'] else ''
        prec=sorted(row['precursors'].items(),key=lambda x:x[1],reverse=True)[:5]
        out.append({'code':code,'count':row['count'],'message':msg,'precursors':prec})
    return sorted(out,key=lambda x:(-x['count'],x['code']))
