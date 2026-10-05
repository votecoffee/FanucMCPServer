from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from fanuc_workbench.parsers.data import parse_numeric_registers,parse_position_registers,parse_frames
from fanuc_workbench.parsers.ls import read_text
from fanuc_workbench.services.workspace import scan_programs
from fanuc_workbench.services.xref import validate_ls,CALL_RE
@dataclass(slots=True)
class LintIssue:
    severity:str; code:str; file:str; line:int; message:str

def lint_workspace(root:Path)->list[LintIssue]:
    issues=[];programs={p.program_name.upper():p for p in scan_programs(root)}
    for info in programs.values():
        text=read_text(info.path)
        for msg in validate_ls(info.path):issues.append(LintIssue('Error','LS_VALIDATION',str(info.path),0,msg))
        for m in CALL_RE.finditer(text):
            target=m.group(1).upper()
            if target not in programs:issues.append(LintIssue('Error','MISSING_CALL',str(info.path),text[:m.start()].count('\n')+1,f'CALL target {target} not found'))
        labels=re.findall(r'^\s*\d+\s*:\s*LBL\[(\d+)\]',text,re.I|re.M);jumps=re.findall(r'\bJMP\s+LBL\[(\d+)\]',text,re.I)
        for j in sorted(set(jumps)-set(labels)):issues.append(LintIssue('Error','MISSING_LABEL',str(info.path),0,f'JMP LBL[{j}] has no target'))
        for n in set(labels):
            if labels.count(n)>1:issues.append(LintIssue('Warning','DUP_LABEL',str(info.path),0,f'LBL[{n}] appears {labels.count(n)} times'))
        frame_nums=[int(x) for x in re.findall(r'\bUFRAME_NUM\s*=\s*(\d+)',text,re.I)]
        pos_ufs=[int(x) for x in re.findall(r'\bUF\s*:\s*(\d+)',text,re.I)]
        if frame_nums and pos_ufs:
            common=max(set(pos_ufs),key=pos_ufs.count)
            for u in sorted(set(pos_ufs)):
                if u!=common and pos_ufs.count(u)==1:issues.append(LintIssue('Warning','UF_OUTLIER',str(info.path),0,f'UF:{u} occurs once while UF:{common} is dominant'))
    for name,parser,kind in [('NUMREG.VA',parse_numeric_registers,'R'),('POSREG.VA',parse_position_registers,'PR')]:
        for p in root.rglob(name):
            try:rows=parser(p)
            except Exception:continue
            for r in rows:
                if not getattr(r,'comment','').strip():issues.append(LintIssue('Info','MISSING_COMMENT',str(p),getattr(r,'line_number',getattr(r,'start_line',0)),f'{kind}[{r.index}] has no description'))
    for p in root.rglob('FRAME.DG'):
        try:frames=parse_frames(p)
        except Exception:continue
        for f in frames:
            if not f.comment.strip():issues.append(LintIssue('Info','FRAME_UNDOCUMENTED',str(p),f.line_number,f'{f.kind}[{f.index}] has no description'))
    return issues
