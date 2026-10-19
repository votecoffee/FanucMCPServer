"""Conservative LS instruction, access and control-flow evidence (MIT)."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from . import base

FLAGS = re.I | re.ASCII
REF_START = re.compile(r'\b(PR|SR|AR|R|P|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|F|UFRAME|UTOOL)\s*\[', FLAGS)
SCALAR = re.compile(r'\b(UFRAME_NUM|UTOOL_NUM)\b', FLAGS)
IO_TYPES = {'DI','DO','GI','GO','AI','AO','RI','RO','UI','UO','F'}
MAX_NODES = 10000


def canonical(text):
    return re.sub(r'\s+', '', text).upper()


def mask(text):
    """Mask comments, quoted strings and bracket descriptions, keeping indexes."""
    out = list(text)
    stack, quote, description = [], None, None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            out[index] = ' '
            if char == quote:
                if index+1 < len(text) and text[index+1] == quote:
                    out[index+1] = ' '
                    index += 1
                else:
                    quote = None
        elif char in "'\"":
            quote = char
            out[index] = ' '
        elif char == '!' and not stack:
            out[index:] = ' '*(len(text)-index)
            break
        elif char in '[(':
            stack.append(char)
            if description is not None:
                out[index] = ' '
        elif char in '])':
            if not stack or stack[-1] != {']':'[', ')':'('}[char]:
                raise ValueError('Unbalanced LS delimiters')
            if description is not None and len(stack) > description:
                out[index] = ' '
            if description == len(stack):
                description = None
            stack.pop()
        elif char == ':' and stack and stack[-1] == '[' and description is None:
            description = len(stack)
            out[index] = ' '
        elif description is not None:
            out[index] = ' '
        index += 1
    if quote or stack:
        raise ValueError('Unbalanced LS delimiters')
    return ''.join(out)


def split_action(code):
    depth = 0
    for index, char in enumerate(code):
        if char in '[(': depth += 1
        elif char in '])': depth -= 1
        elif char == ',' and not depth:
            return code[:index].strip(), code[index+1:].strip()
    return code.strip(), ''


def references(code):
    found = []
    for match in REF_START.finditer(code):
        start, depth, end = match.end(), 1, match.end()
        while end < len(code) and depth:
            if code[end] == '[': depth += 1
            elif code[end] == ']': depth -= 1
            end += 1
        if depth:
            continue
        axis = re.match(r'\.(?:[XYZWPR]|[JE]\d+)\b',code[end:],FLAGS)
        if axis: end += axis.end()
        interior = code[start:code.rfind(']',start,end)]
        direct = re.fullmatch(r'\s*\d+(?:\s*,\s*\d+)?\s*', interior)
        found.append({'token': canonical(code[match.start():end]), 'kind': match[1].upper(),
                      'indirect': direct is None, 'start': match.start(), 'end': end})
    for match in SCALAR.finditer(code):
        found.append({'token': match[1].upper(), 'kind': match[1].upper(), 'indirect': False,
                      'start': match.start(), 'end': match.end()})
    return sorted(found, key=lambda row:(row['start'], -row['end']))


def _statement(raw):
    match = re.fullmatch(r'\s*(\d{1,6})\s*:\s*(.*)', raw)
    if match is None:
        raise ValueError('numbered_statement_required')
    code = mask(match[2]).strip()
    if not code and match[2].lstrip().startswith('!'):
        return int(match[1]),match[2],''
    if not code.endswith(';') or ';' in code[:-1]:
        raise ValueError('single_terminated_statement_required')
    code = code[:-1].strip()
    return int(match[1]), match[2], code


def _classify(code):
    upper = code.upper()
    kind, condition, action = 'OTHER', None, code
    if upper.startswith('IF '):
        condition, action = split_action(code[3:].strip())
        kind = 'IF'
        if re.search(r'\bTHEN\s*$', condition, FLAGS):
            condition = re.sub(r'\bTHEN\s*$', '', condition, flags=FLAGS).strip()
            kind = 'IF_THEN'
    elif upper.startswith('SELECT '):
        condition, action = split_action(code[7:].strip())
        kind = 'SELECT'
    elif upper.startswith('WAIT '):
        condition, action = split_action(code[5:].strip())
        kind = 'WAIT'
    elif upper in ('ELSE','ENDIF','END','ABORT'):
        kind = upper
    elif re.match(r'LBL\s*\[', code, FLAGS): kind = 'LABEL'
    elif re.match(r'JMP\s+LBL\s*\[', code, FLAGS): kind = 'JMP'
    elif re.match(r'(CALL|RUN)\b', code, FLAGS): kind = upper.split()[0]
    elif re.match(r'[JLC]\s+(?:P|PR)\s*\[', code, FLAGS): kind = 'MOTION'
    elif re.match(r'(?:UFRAME_NUM|UTOOL_NUM|(?:PR|R|SR|AR|DO|GO|AO|RO|UO|F)\s*\[)', code, FLAGS):
        if _assignment(code) is not None: kind = 'ASSIGNMENT'
    elif not code: kind = 'COMMENT'
    return kind, condition, action


def _assignment(code):
    # Only an entire explicit reference/scalar may be an assignment target.
    for ref in references(code):
        if code[:ref['start']].strip(): continue
        tail = code[ref['end']:].lstrip()
        if tail.startswith('=') and not tail.startswith('=='):
            return ref
    return None


def parse_listing(lines, file):
    nodes, issues = [], []
    headers = [base._HEADER.fullmatch(line) for line in lines if re.match(r'\s*/PROG\b', line, FLAGS)]
    mn = [i for i,line in enumerate(lines) if line.strip().upper() == '/MN']
    ends = [i for i,line in enumerate(lines) if line.strip().upper() == '/END']
    name = headers[0][1].upper() if len(headers)==1 and headers[0] else None
    valid = bool(name and len(mn)==len(ends)==1 and mn[0]<ends[0])
    if not valid:
        issues.append({'path':file['path'], 'line_number':0, 'code':'program_sections_unsupported'})
    if not mn:
        return {'file':{**file,'program':name}, 'nodes':nodes,'accesses':[], 'edges':[], 'issues':issues}
    accesses, seen, selector = [], set(), None
    for physical in range(mn[0]+1,len(lines)):
        raw = lines[physical]
        if raw.lstrip().startswith('/'): break
        if not raw.strip(): continue
        try:
            tp, original, code = _statement(raw)
            kind, condition, action = _classify(code)
        except ValueError as exc:
            issues.append({'path':file['path'],'line_number':physical+1,'code':str(exc)})
            continue
        if kind == 'SELECT':
            selector = re.split(r'(?<![<>!=])=(?!=)', condition, maxsplit=1)[0].strip()
        elif selector and (code.startswith('=') or re.match(r'ELSE\s*,',code,FLAGS)):
            clause, action = split_action(code)
            condition = selector+clause if clause.startswith('=') else None
            kind = 'SELECT_CASE' if condition else 'SELECT_ELSE'
        else:
            selector = None
        if tp in seen: issues.append({'path':file['path'],'line_number':physical+1,'code':'duplicate_tp_line'})
        seen.add(tp)
        node = {'path':file['path'],'program':name,'line_number':physical+1,'tp_line_number':tp,
                'kind':kind,'code':code,'source':raw,'condition':condition,'action':action}
        nodes.append(node)
        # Conditions read; only the explicit action's LHS writes. Nested index refs always read.
        target = _assignment(action) if kind in ('ASSIGNMENT','IF','SELECT','SELECT_CASE','SELECT_ELSE') else None
        action_start = code.rfind(action) if action else len(code)
        for ref in references(code):
            access = 'write' if target and ref['start']==action_start+target['start'] and ref['end']==action_start+target['end'] else 'read'
            accesses.append({**{key:node[key] for key in ('path','program','line_number','tp_line_number')},
                             **{key:ref[key] for key in ('token','kind','indirect')},'access':access,
                             'resolution':'unresolved_indirect' if ref['indirect'] else 'explicit'})
        if kind == 'OTHER' and code:
            issues.append({'path':file['path'],'line_number':physical+1,'code':'unsupported_instruction','source':raw})
    labels = {}
    for node in nodes:
        if node['kind']=='LABEL':
            match = re.fullmatch(r'LBL\s*\[\s*(\d+)\s*\]',node['code'],FLAGS)
            if match: labels.setdefault(int(match[1]),[]).append(node['tp_line_number'])
    edges, blocks, pairs = [], [], {}
    for index,node in enumerate(nodes):
        if node['kind']=='IF_THEN': blocks.append([index,None])
        elif node['kind']=='ELSE':
            if blocks and blocks[-1][1] is None: blocks[-1][1]=index
            else: issues.append({'path':file['path'],'line_number':node['line_number'],'code':'unmatched_else'})
        elif node['kind']=='ENDIF':
            if blocks:
                first, alternate = blocks.pop();pairs[first]=(alternate,index)
                if alternate is not None: pairs[alternate]=(None,index)
            else: issues.append({'path':file['path'],'line_number':node['line_number'],'code':'unmatched_endif'})
    for index,_ in blocks: issues.append({'path':file['path'],'line_number':nodes[index]['line_number'],'code':'unclosed_if'})
    def edge(node,target,branch,resolution='resolved'):
        edges.append({'path':file['path'],'from_tp_line':node['tp_line_number'],'to_tp_line':target,
                      'branch':branch,'resolution':resolution})
    for index,node in enumerate(nodes):
        following = nodes[index+1]['tp_line_number'] if index+1<len(nodes) else None
        kind = node['kind']
        if kind in ('END','ABORT'): continue
        action = node['action']
        jump = re.search(r'\bJMP\s+LBL\s*\[(.*?)\]',action,FLAGS)
        if jump:
            target = int(jump[1]) if jump[1].strip().isdigit() else None
            choices = labels.get(target,[])
            edge(node, choices[0] if len(choices)==1 else None, 'jump',
                 'resolved' if len(choices)==1 else 'unresolved_label')
            if kind=='JMP': continue
        if kind=='IF_THEN' and index in pairs:
            alternate,end = pairs[index]
            after = (alternate+1) if alternate is not None else end+1
            edge(node,following,'condition_true')
            edge(node,nodes[after]['tp_line_number'] if after<len(nodes) else None,'condition_false')
        elif kind=='ELSE' and index in pairs:
            end = pairs[index][1]+1
            edge(node,nodes[end]['tp_line_number'] if end<len(nodes) else None,'else_end')
        elif kind=='WAIT':
            edge(node,node['tp_line_number'],'condition_false_or_unavailable')
            edge(node,following,'condition_true')
        else:
            edge(node,following,'next_possible' if kind in ('OTHER','IF','SELECT','SELECT_CASE','SELECT_ELSE') else 'next')
    return {'file':{**file,'program':name},'nodes':nodes,'accesses':accesses,'edges':edges,'issues':issues}


def inspect_selection(root, paths):
    base._options(paths, [], 'programs', 0, 100)
    root, listings, total = Path(root).resolve(), [], 0
    for relative in sorted(paths):
        lines, file = base._read(root,relative,base.MAX_TOTAL_BYTES-total)
        total += file['source_bytes']
        listings.append(parse_listing(lines,file))
        if sum(len(row['nodes']) for row in listings)>MAX_NODES:
            raise ValueError('Selected inputs exceed 10000 instruction nodes')
        if sum(row['file']['lines_total'] for row in listings)>base.MAX_LINES:
            raise ValueError('Selected inputs exceed total line budget')
    return listings


def report_page(rows, *, view, offset=0, limit=100, **summary):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('Use offset >=0 and limit 1..100')
    return {**summary,'view':view,'total':len(rows),'offset':offset,'next_offset':offset+limit if offset+limit<len(rows) else None,
            'items':rows[offset:offset+limit], 'controller_contacted':False,'controller_written':False,
            'runtime_behavior_verified':False,'evidence_only':True}


def trace_logic(root, paths, *, view='instructions', offset=0, limit=100):
    if view not in ('instructions','accesses','edges','issues','signals'):
        raise ValueError('Unknown trace view')
    listings = inspect_selection(root,paths)
    accesses = [item for listing in listings for item in listing['accesses']]
    signals = {}
    for access in accesses:
        row = signals.setdefault(access['token'],{'token':access['token'],'readers':0,'writers':0,'indirect':access['indirect']})
        row['readers' if access['access']=='read' else 'writers'] += 1
    rows = (list(signals.values()) if view=='signals' else
            [item for listing in listings for item in listing[{'instructions':'nodes','accesses':'accesses','edges':'edges','issues':'issues'}[view]]])
    files = [listing['file'] for listing in listings]
    return report_page(rows,view=view,offset=offset,limit=limit,files=files,
        selection_sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
        instruction_counts=dict(Counter(node['kind'] for listing in listings for node in listing['nodes'])),
        unresolved_indirect_count=sum(row['indirect'] for row in accesses),
        issue_count=sum(len(listing['issues']) for listing in listings),
        control_flow_complete=False, compiler_validation_performed=False)
