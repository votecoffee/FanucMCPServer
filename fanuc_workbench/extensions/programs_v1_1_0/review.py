"""Semantic LS change categories and explicit dependency inventory review (MIT)."""
from collections import Counter, deque
from decimal import Decimal
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re

from . import base
from .diagnostics import read_json, selected_bytes
from .logic import canonical, inspect_selection, references, report_page, IO_TYPES,mask

TOKEN=re.compile(r'''"(?:""|[^"\r\n])*"|'(?:''|[^'\r\n])*'|[A-Za-z_$][A-Za-z0-9_$]*|(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?|[^\s]''')
GENERATED=re.compile(r'^\s*(OWNER|PROG_SIZE|PROGRAM_SIZE|CREATE|MODIFIED|FILE_NAME|LINE_COUNT|MEMORY_SIZE)\s*=',re.I)


def tokens(text):
    out=[]
    for token in TOKEN.findall(text):
        if token[0].isdigit() or token.startswith('.') and len(token)>1 and token[1].isdigit():
            token=str(Decimal(token).normalize())
        elif token[0] not in "'\"": token=token.upper()
        out.append(token)
    return out


def code_and_comments(text):
    out,comments=[],[]
    depth,quote,description=0,None,False
    index=0
    while index<len(text):
        char=text[index]
        if quote:
            (comments if description else out).append(char)
            if char==quote:
                if index+1<len(text) and text[index+1]==quote:
                    (comments if description else out).append(text[index+1]);index+=1
                else: quote=None
        elif char in "'\"":
            quote=char;(comments if description else out).append(char)
        elif char=='!' and depth==0:
            comments.append(text[index:]);break
        elif char=='[':
            depth+=1;(comments if description else out).append(char)
        elif char==']':
            depth-=1
            if description and depth==0: description=False
            (comments if description else out).append(char)
        elif char==':' and depth>0:
            description=True;comments.append(char)
        else: (comments if description else out).append(char)
        index+=1
    return ''.join(out),''.join(comments)


def _components(node):
    original=re.sub(r'^\s*\d+\s*:\s*','',node['source'])
    code,comments=code_and_comments(original)
    parts={'comments':comments}
    for category,pattern in (
        ('speed',r'\b(?:\d+(?:\.\d+)?|R\s*\[[^\]]+\])\s*(?:mm/sec|cm/min|deg/sec|%)'),
        ('termination',r'\b(?:FINE|CNT\s*(?:\d+|R\s*\[[^\]]+\]))\b'),
        ('offsets',r'\b(?:Offset|Tool_Offset|Offset_Condition)\b\s*(?:,\s*(?:PR|P)\s*\[[^\]]+\])?'),
    ):
        # Settings apply only to recognized motion nodes, never quoted MESSAGE data.
        matches=list(re.finditer(pattern,code,re.I)) if node['kind']=='MOTION' else []
        parts[category]=[tokens(match[0]) for match in matches]
        for match in reversed(matches): code=code[:match.start()]+' '+code[match.end():]
    if node['kind']=='ASSIGNMENT' and re.match(r'\s*(UFRAME_NUM|UTOOL_NUM)\s*=',code,re.I):
        parts['frames_tools']=tokens(code);code='FRAME_TOOL_ASSIGNMENT'
    parts['instruction']=tokens(code)
    return parts


def _preview(value):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    return {'text':text[:1500], 'truncated':len(text)>1500,'sha256':hashlib.sha256(text.encode()).hexdigest()}


def semantic_compare(root,before_path,after_path,*,offset=0,limit=100):
    listings=inspect_selection(root,[before_path,after_path]) if before_path!=after_path else inspect_selection(root,[before_path])
    by_path={row['file']['path']:row for row in listings}
    left,right=by_path[before_path],by_path[after_path]
    if max(len(left['nodes']),len(right['nodes']))>2000: raise ValueError('Semantic comparison supports at most 2000 instructions per listing')
    a=[_components(node) for node in left['nodes'] if node['kind']!='COMMENT']
    b=[_components(node) for node in right['nodes'] if node['kind']!='COMMENT']
    changes=[]
    def change(category,location,old,new):
        changes.append({'category':category,'location':location,'before':_preview(old),'after':_preview(new)})
    if left['file']['program']!=right['file']['program']:change('program_identity','/PROG',left['file']['program'],right['file']['program'])
    # SequenceMatcher needs hashable instruction identities.
    matcher=SequenceMatcher(a=[tuple(row['instruction']) for row in a],b=[tuple(row['instruction']) for row in b],autojunk=False)
    for operation,i,j,k,l in matcher.get_opcodes():
        for step in range(max(j-i,l-k)):
            old=a[i+step] if i+step<j else {}
            new=b[k+step] if k+step<l else {}
            for category in ('instruction','speed','termination','offsets','frames_tools','comments'):
                if old.get(category)!=new.get(category): change(category,f'instruction {i+step+1} -> {k+step+1}',old.get(category),new.get(category))
    for section in ('comments','program_comment','positions','attributes','generated_metadata','unclassified_sections'):
        def selected(listing):
            lines,current=base._read(Path(root).resolve(),listing['file']['path'],base.MAX_FILE_BYTES)
            if current['source_sha256']!=listing['file']['source_sha256']:raise ValueError('Listing revision changed during semantic review')
            if section=='comments':
                return [code_and_comments(re.sub(r'^\s*\d+\s*:\s*','',line))[1] for line in lines
                        if line.lstrip().startswith('!') or re.match(r'\s*\d+\s*:\s*!',line)]
            if section=='positions':
                start=next((i for i,line in enumerate(lines) if line.strip().upper()=='/POS'),len(lines))
                body='\n'.join(lines[start+1:])
                pattern=r'(?ms)^\s*P\[(\d+)(?::[^\]]*)?\]\s*\{.*?\}\s*;'
                result={match[1]:match[0] for match in re.finditer(pattern,body)}
                remaining=re.sub(pattern,'',body).replace('/END','').strip()
                if remaining: result['unparsed']=remaining
                return result
            if section=='program_comment': return [tokens(line) for line in lines if re.match(r'\s*COMMENT\s*=',line,re.I)]
            if section=='unclassified_sections':
                result=[];active=False
                for line in lines:
                    if line.lstrip().startswith('/'):
                        active=line.strip().upper() not in ('/MN','/POS','/ATTR','/END') and not line.lstrip().upper().startswith('/PROG ')
                    if active:result.append(tokens(line))
                return result
            if section=='generated_metadata': return [tokens(line) for line in lines if GENERATED.match(line)]
            active=False;result=[]
            for line in lines:
                if line.strip().upper()=='/ATTR': active=True;continue
                if line.lstrip().startswith('/'): active=False
                if active and not GENERATED.match(line) and not re.match(r'\s*COMMENT\s*=',line,re.I): result.append(tokens(line))
            return result
        old,new=selected(left),selected(right)
        if section=='positions':
            for key in sorted(set(old)|set(new),key=lambda key:(not key.isdigit(),int(key) if key.isdigit() else 0)):
                ov,nv=old.get(key,''),new.get(key,'')
                if key=='unparsed':
                    if tokens(ov)!=tokens(nv): change('unclassified_positions',key,ov,nv)
                    continue
                frames=lambda text:re.findall(r'\b(UF|UT)\s*:\s*([^,;\r\n]+)',text,re.I)
                if frames(ov)!=frames(nv): change('frames_tools','P['+key+']',frames(ov),frames(nv))
                strip_frames=lambda text:re.sub(r'\b(?:UF|UT)\s*:\s*[^,;\r\n]+','',code_and_comments(text)[0],flags=re.I)
                if tokens(strip_frames(ov))!=tokens(strip_frames(nv)): change('positions','P['+key+']',ov,nv)
                if code_and_comments(ov)[1]!=code_and_comments(nv)[1]: change('comments','P['+key+']',code_and_comments(ov)[1],code_and_comments(nv)[1])
        elif old!=new: change('comments' if section=='program_comment' else section,section,old,new)
    return report_page(changes,view='changes',offset=offset,limit=limit,
        before=left['file'],after=right['file'],category_counts=dict(Counter(row['category'] for row in changes)),
        unresolved_syntax=left['issues']+right['issues'],semantic_equivalence_verified=False,
        position_parser_complete=False)


def dependency_review(root,paths,*,entry_programs,inventory_path=None,view='requirements',offset=0,limit=100,_all=False):
    base._options(paths,entry_programs,'programs',offset,limit)
    if not entry_programs: raise ValueError('Dependency review requires explicit entry programs')
    listings=inspect_selection(root,paths)
    definitions={}
    for listing in listings: definitions.setdefault(listing['file']['program'],[]).append(listing)
    pending=deque(name.upper() for name in entry_programs)
    reachable,requirements,issues=set(),[],[]
    def need(kind,token,path,line,resolution):
        requirements.append({'kind':kind,'token':token,'path':path,'tp_line_number':line,'resolution':resolution})
    resources,mappings,inventory_hash={},{},None
    if inventory_path:
        data,inventory_hash=read_json(root,inventory_path)
        if (not isinstance(data,dict) or data.get('format')!='fanuc-dependency-inventory' or type(data.get('schema_version')) is not int or data['schema_version']!=1
            or not isinstance(data.get('resources'),dict) or not isinstance(data.get('io_mappings'),dict)):
            raise ValueError('Expected version 1 dependency inventory')
        resources,mappings=data['resources'],data['io_mappings']
        if len(resources)+len(mappings)>10000 or any(not isinstance(value,dict) or not value for value in [*resources.values(),*mappings.values()]): raise ValueError('Inventory entries require explicit nonempty evidence objects')
        if any(key!=canonical(key) for key in [*resources,*mappings]): raise ValueError('Use canonical inventory reference names')
    while pending:
        name=pending.popleft()
        if name in reachable: continue
        matches=definitions.get(name,[])
        if len(matches)!=1: raise ValueError('Entry/reachable program definition is missing or ambiguous')
        listing=matches[0];reachable.add(name);file=listing['file'];issues.extend(listing['issues'])
        for node in listing['nodes']:
            if node['kind']=='MOTION':
                for token in ('UFRAME_NUM','UTOOL_NUM'):
                    need('frame_tool_state',token,file['path'],node['tp_line_number'],
                         'inventory_supplied' if token in resources else 'unresolved_runtime_frame_tool_state')
            for branch in node.get('select_cases') or [{'action':node['action']}]:
                action=base._mask(branch['action'])
                call=base._reference(action) if action else None
                if call:
                    kind,target,target_kind=call
                    found=definitions.get(target,[])
                    valid=len(found)==1 and not found[0]['issues']
                    resolution='selected' if valid else 'unresolved_indirect' if target_kind!='direct' else 'ambiguous' if len(found)>1 else 'missing_or_unparsed_program'
                    need('program',target or branch['action'],file['path'],node['tp_line_number'],resolution)
                    if valid: pending.append(target)
                frame=re.match(r'\s*(UFRAME_NUM|UTOOL_NUM)\s*=\s*(\d+)\s*$',branch['action'],re.I)
                if frame:
                    token=('UFRAME' if frame[1].upper()=='UFRAME_NUM' else 'UTOOL')+'['+frame[2]+']'
                    need('frame_tool',token,file['path'],node['tp_line_number'],'inventory_supplied' if token in resources else 'missing_inventory')
                elif re.match(r'\s*(UFRAME_NUM|UTOOL_NUM)\s*=',branch['action'],re.I):
                    need('frame_tool',branch['action'],file['path'],node['tp_line_number'],'unresolved_frame_tool_index')
        position_ids=set()
        lines,current=base._read(Path(root).resolve(),file['path'],base.MAX_FILE_BYTES)
        if current['source_sha256']!=file['source_sha256']:raise ValueError('Listing revision changed during dependency review')
        pos_start=next((index for index,line in enumerate(lines) if line.strip().upper()=='/POS'),len(lines))
        pos_body='\n'.join(lines[pos_start+1:])
        for block in re.finditer(r'(?ms)^\s*P\[(\d+)(?::[^\]]*)?\]\s*\{.*?\}\s*;',pos_body):
            if not re.search(r'\bGP\d+\s*:',block[0],re.I):
                issues.append({'path':file['path'],'code':'position_group_unparsed','position':block[1]});continue
            position_ids.add('P['+str(int(block[1]))+']')
            unquoted=mask(block[0])
            frame_fields=list(re.finditer(r'\b(UF|UT)\s*:\s*(\d+)',unquoted,re.I))
            if {match[1].upper() for match in frame_fields}!={'UF','UT'}:
                issues.append({'path':file['path'],'code':'position_frame_tool_unparsed','position':block[1]})
            for match in frame_fields:
                token=('UFRAME' if match[1].upper()=='UF' else 'UTOOL')+'['+match[2]+']'
                need('frame_tool',token,file['path'],None,'inventory_supplied' if token in resources else 'missing_inventory')
        for access in listing['accesses']:
            kind,token=access['kind'],access['token']
            if kind in ('UFRAME_NUM','UTOOL_NUM'):
                if access['access']=='read':need('frame_tool_state',token,access['path'],access['tp_line_number'],'inventory_supplied' if token in resources else 'missing_inventory')
                continue
            resource=token.split('.')[0]
            if access['indirect']: resolution='unresolved_indirect'
            elif kind=='P': resolution='defined_in_listing' if resource in position_ids else 'missing_position'
            elif kind in IO_TYPES: resolution='mapping_supplied' if resource in mappings else 'missing_io_mapping'
            else: resolution='inventory_supplied' if resource in resources else 'missing_inventory'
            need(kind,token,access['path'],access['tp_line_number'],resolution)
        for edge in listing['edges']:
            if edge['resolution']!='resolved': issues.append({'path':file['path'],'code':'unresolved_control_flow','tp_line_number':edge['from_tp_line']})
    missing=[row for row in requirements if row['resolution'] not in ('selected','defined_in_listing','mapping_supplied','inventory_supplied')]
    selected={'requirements':requirements,'missing':missing,'issues':issues}.get(view)
    if selected is None: raise ValueError('Choose requirements, missing or issues view')
    result=report_page(selected,view=view,offset=offset,limit=limit,entry_programs=entry_programs,
        program_files=[listing['file'] for listing in listings if listing['file']['program'] in reachable],
        inventory_path=inventory_path,inventory_sha256=inventory_hash,missing_count=len(missing),issue_count=len(issues),
        requirement_count=len(requirements),explicit_dependencies_accounted_for=not missing and not issues,
        physical_compatibility_verified=False,inventory_claims_verified=False)
    if _all:result.update(items=selected,offset=0,next_offset=None,issues=issues)
    return result
