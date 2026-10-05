from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fanuc_workbench.parsers.ls import read_text, parse_program_info


@dataclass(slots=True)
class Reference:
    token: str
    path: Path
    program: str
    line_number: int
    access: str
    context: str

TOKEN_RE = re.compile(r"\b(?:R|PR|SR|AR|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|F)\s*\[\s*\d+(?:\s*,\s*\d+)?\s*\]|\b(?:UFRAME|UTOOL)\s*\[?\s*\d+\s*\]?|\bCALL\s+[A-Z0-9_]+|\bUALM\s*\[\s*\d+\s*\]", re.I)
CALL_RE = re.compile(r"\bCALL\s+([A-Z0-9_]+)", re.I)
LBL_RE = re.compile(r"\bLBL\s*\[\s*(\d+)\s*\]", re.I)
JMP_RE = re.compile(r"\bJMP\s+LBL\s*\[\s*(\d+)\s*\]", re.I)


def canonical_token(raw: str) -> str:
    raw = re.sub(r"\s+", "", raw.upper())
    if raw.startswith('CALL'):
        return 'CALL ' + raw[4:]
    return raw


def _access_for(line: str, token: str) -> str:
    clean = re.sub(r"\s+", "", line.upper())
    tok = re.sub(r"\s+", "", token.upper())
    if re.search(re.escape(tok) + r"\s*=", clean): return 'Write'
    if re.search(r"=\s*" + re.escape(tok), clean): return 'Read'
    if tok.startswith(('DO[','RO[','AO[','GO[','UO[')) and '=' in clean and clean.find(tok) < clean.find('='): return 'Write'
    return 'Read/Use'


def build_xref(root: Path) -> list[Reference]:
    refs: list[Reference] = []
    for path in root.rglob('*'):
        if not path.is_file() or path.name.endswith('.before_comment_edit'): continue
        if path.suffix.upper() not in {'.LS','.VA','.DG','.TXT','.CSV','.DT'}: continue
        try: lines = read_text(path).splitlines()
        except Exception: continue
        program = path.stem
        if path.suffix.upper() == '.LS':
            try: program = parse_program_info(path).program_name
            except Exception: pass
        for n,line in enumerate(lines,1):
            for m in TOKEN_RE.finditer(line):
                token = canonical_token(m.group(0))
                refs.append(Reference(token,path,program,n,_access_for(line,m.group(0)),line.strip()))
    return refs


def program_dependencies(root: Path) -> dict[str, set[str]]:
    graph: dict[str,set[str]] = {}
    for path in root.rglob('*.LS'):
        try:
            name = parse_program_info(path).program_name.upper(); text = read_text(path)
        except Exception: continue
        graph.setdefault(name,set()).update(m.group(1).upper() for m in CALL_RE.finditer(text))
    return graph


def unused_programs(root: Path) -> list[str]:
    graph = program_dependencies(root); called = {x for deps in graph.values() for x in deps}
    roots = {n for n in graph if n in {'MAIN','CELL','AUTO','PNS0001','RSR001','HOME','INIT'}}
    return sorted(n for n in graph if n not in called and n not in roots)


def register_usage(root: Path) -> dict[str, dict[str,int]]:
    out: dict[str,dict[str,int]] = {}
    for ref in build_xref(root):
        if not re.match(r"^(?:R|PR|SR|AR|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|F)\[", ref.token): continue
        row = out.setdefault(ref.token, {'Read':0,'Write':0,'Use':0})
        if ref.access == 'Write': row['Write'] += 1
        elif ref.access == 'Read': row['Read'] += 1
        else: row['Use'] += 1
    return out


def program_flow(path: Path) -> list[tuple[int,str,str]]:
    out=[]
    for n,line in enumerate(read_text(path).splitlines(),1):
        u=line.upper()
        kind=''
        if CALL_RE.search(line): kind='CALL'
        elif JMP_RE.search(line): kind='JMP'
        elif LBL_RE.search(line): kind='LBL'
        elif re.search(r'\bIF\b',u): kind='IF'
        elif re.search(r'\bSELECT\b',u): kind='SELECT'
        elif re.search(r'\bWAIT\b',u): kind='WAIT'
        elif re.search(r'\bRUN\s+',u): kind='RUN'
        elif re.search(r'\bMONITOR\b|\bCONDITION\b',u): kind='CONDITION'
        if kind: out.append((n,kind,line.strip()))
    return out


def validate_ls(path: Path, workspace: Path | None = None) -> list[str]:
    text=read_text(path); issues=[]; upper=text.upper()
    for sec in ('/PROG','/ATTR','/MN','/POS','/END'):
        if sec not in upper: issues.append(f'Missing {sec} section')
    labels={m.group(1) for m in LBL_RE.finditer(text)}; jumps={m.group(1) for m in JMP_RE.finditer(text)}
    for lbl in sorted(jumps-labels): issues.append(f'JMP references missing LBL[{lbl}]')
    if workspace:
        known={p.stem.upper() for p in workspace.rglob('*.LS')}
        for m in CALL_RE.finditer(text):
            if m.group(1).upper() not in known: issues.append(f'CALL target not found in workspace: {m.group(1)}')
    return issues
