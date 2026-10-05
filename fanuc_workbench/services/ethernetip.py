from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from fanuc_workbench.parsers.ls import read_text
@dataclass(slots=True)
class EipConnection:
    source:str; role:str=''; connection:str=''; ip:str=''; slot:str=''; input_size:str=''; output_size:str=''; rpi:str=''; status:str=''; raw:str=''
PATTERNS={
 'ip':re.compile(r'\b(?:IP|ADDRESS|HOST)\s*[:=]\s*(\d{1,3}(?:\.\d{1,3}){3})',re.I),
 'slot':re.compile(r'\bSLOT\s*[:=]\s*(\d+)',re.I),'input_size':re.compile(r'\b(?:INPUT|IN)\s*(?:SIZE|WORDS?|BYTES?)\s*[:=]\s*(\d+)',re.I),
 'output_size':re.compile(r'\b(?:OUTPUT|OUT)\s*(?:SIZE|WORDS?|BYTES?)\s*[:=]\s*(\d+)',re.I),'rpi':re.compile(r'\bRPI\s*[:=]\s*([\d.]+\s*(?:MS|MSEC)?)',re.I),
}
def scan_ethernetip(root:Path)->list[EipConnection]:
    rows=[]
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix.upper() not in {'.DG','.VA','.TXT','.CSV','.XML'}:continue
        try:lines=read_text(p).splitlines()
        except Exception:continue
        for line in lines:
            up=line.upper()
            if 'ETHERNET/IP' not in up and 'EIP' not in up and not ('RPI' in up and ('SLOT' in up or 'SIZE' in up)):continue
            role='Scanner' if 'SCANNER' in up else ('Adapter' if 'ADAPTER' in up else '')
            vals={k:(m.group(1).strip() if (m:=pat.search(line)) else '') for k,pat in PATTERNS.items()}
            conn='';m=re.search(r'\b(?:CONNECTION|CONN)\s*#?\s*[:=]?\s*(\d+)',line,re.I);conn=m.group(1) if m else ''
            status='Running' if 'RUNNING' in up else ('Enabled' if 'ENABLE' in up else '')
            rows.append(EipConnection(str(p),role,conn,**vals,status=status,raw=line.strip()))
    return rows
