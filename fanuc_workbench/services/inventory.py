from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from fanuc_workbench.parsers.ls import read_text
@dataclass(slots=True)
class OptionEvidence:
    option:str; status:str; source:str; evidence:str
OPTION_PATTERNS={
'ASCII Program Loader':('ASCII PROGRAM','ASCII PROG','R507'), 'KAREL':('KAREL','R632'), 'iRVision':('IRVISION','VISION'), 'DCS':('DCS','DUAL CHECK SAFETY'),
'EtherNet/IP':('ETHERNET/IP','EIP'), 'BG Logic':('BG LOGIC','BACKGROUND LOGIC'), 'OPC UA':('OPC UA','OPCUA'), 'SFTP':('SFTP',), 'MQTT':('MQTT',), 'Collision Guard':('COLLISION GUARD',),
}
def option_inventory(root:Path)->list[OptionEvidence]:
    hits={k:None for k in OPTION_PATTERNS}
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix.upper() not in {'.DG','.VA','.LS','.TXT','.CSV','.XML'}:continue
        try:text=read_text(p)[:500000]
        except Exception:continue
        up=text.upper()
        for opt,pats in OPTION_PATTERNS.items():
            if hits[opt] is None and any(x in up for x in pats):
                pat=next(x for x in pats if x in up);line=next((l.strip() for l in text.splitlines() if pat in l.upper()),pat);hits[opt]=OptionEvidence(opt,'Evidence found',str(p),line[:240])
    return [hits[k] or OptionEvidence(k,'Not determined','','No matching evidence in readable backup files') for k in OPTION_PATTERNS]
