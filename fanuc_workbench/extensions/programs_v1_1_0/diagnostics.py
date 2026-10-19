"""Observed condition evaluation and bounded diagnostic evidence (MIT)."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

from .logic import canonical, inspect_selection, IO_TYPES, references, report_page

MAX_JSON_BYTES = 8*1024*1024
HASH = re.compile(r'[a-f0-9]{64}')


def selected_bytes(root, relative, maximum=MAX_JSON_BYTES):
    if (not isinstance(relative,str) or len(relative)>240 or not relative
        or relative!=PurePosixPath(relative).as_posix() or PurePosixPath(relative).is_absolute()
        or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}',part) or part.endswith(('.', ' '))
               or part.split('.')[0].upper() in {'CON','NUL','AUX','PRN',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
               for part in relative.split('/'))):
        raise ValueError('Use a simple explicit relative evidence path')
    root = Path(root).resolve()
    path = root/relative
    if path.absolute()!=path.resolve() or not path.resolve().is_relative_to(root):
        raise ValueError('Redirected evidence paths are unsupported')
    with path.open('rb') as stream: raw=stream.read(maximum+1)
    if len(raw)>maximum: raise ValueError('Evidence file exceeds its byte bound')
    return raw


def read_json(root, relative):
    raw=selected_bytes(root,relative)
    text=raw.decode('utf-8-sig')
    depth,quoted,escaped,tokens=0,False,False,0
    for char in text:
        if quoted:
            if escaped: escaped=False
            elif char=='\\': escaped=True
            elif char=='"': quoted=False
        elif char=='"': quoted=True;tokens+=1
        elif char in '[{': depth+=1;tokens+=1
        elif char in ']}': depth-=1
        elif char in ',:': tokens+=1
        if depth>24 or tokens>200000: raise ValueError('Evidence JSON exceeds nesting/token bounds')
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out: raise ValueError('Duplicate evidence fields')
            out[key]=value
        return out
    def constant(_): raise ValueError('Nonfinite evidence number')
    value=json.loads(text,object_pairs_hook=unique,parse_constant=constant)
    # Also catches exponent overflow and invalid surrogate strings.
    pending=[value]
    while pending:
        item=pending.pop()
        if isinstance(item,dict): pending.extend(item.keys());pending.extend(item.values())
        elif isinstance(item,list): pending.extend(item)
        elif isinstance(item,str):
            if len(item)>65536 or any(0xD800<=ord(char)<=0xDFFF for char in item): raise ValueError('Unsupported evidence text')
        elif type(item) in (int,float):
            try: valid=math.isfinite(item)
            except OverflowError: valid=False
            if not valid: raise ValueError('Nonfinite evidence number')
    return value,hashlib.sha256(raw).hexdigest()


def validate_observation(row):
    if not isinstance(row,dict) or set(row)-{'timestamp','source_id','task','sample','alarms','alarms_complete'}:
        raise ValueError('Unsupported observation fields')
    if type(row.get('timestamp')) not in (int,float) or not math.isfinite(row['timestamp']): raise ValueError('Finite observation timestamp required')
    if not isinstance(row.get('source_id'),str) or not 1<=len(row['source_id'])<=128: raise ValueError('Explicit observation source_id required')
    task=row.get('task')
    if task is not None:
        if not isinstance(task,dict) or set(task)-{'name','program','line','state','program_sha256','status_code'}: raise ValueError('Unsupported task fields')
        if not isinstance(task.get('program'),str) or not re.fullmatch(r'[A-Za-z0-9_]{1,64}',task['program']): raise ValueError('Explicit task program required')
        if type(task.get('line')) is not int or task['line']<1: raise ValueError('Explicit positive TP line required')
        if not isinstance(task.get('state'),str): raise ValueError('Explicit task state required')
        if not isinstance(task.get('name'),str) or not 1<=len(task['name'])<=128: raise ValueError('Explicit task name required')
        if task.get('program_sha256') is not None and (not isinstance(task['program_sha256'],str) or not HASH.fullmatch(task['program_sha256'])): raise ValueError('Invalid listing revision hash')
    sample=row.get('sample')
    if sample is not None:
        if not isinstance(sample,dict) or set(sample)-{'timestamp','values','errors'}: raise ValueError('Unsupported sample fields')
        if type(sample.get('timestamp')) not in (int,float) or not math.isfinite(sample['timestamp']): raise ValueError('Finite sample timestamp required')
        if not isinstance(sample.get('values'),dict) or not isinstance(sample.get('errors',{}),dict): raise ValueError('Sample values/errors must be objects')
        if len(sample['values'])+len(sample.get('errors',{}))>2000: raise ValueError('Too many sample signals')
        if any(not isinstance(key,str) for key in [*sample['values'],*sample.get('errors',{})]): raise ValueError('Signal names must be strings')
        if any(not isinstance(error,str) for error in sample.get('errors',{}).values()): raise ValueError('Sample errors must be text')
    if 'alarms' in row and (not isinstance(row['alarms'],list) or len(row['alarms'])>100 or any(not isinstance(alarm,str) for alarm in row['alarms'])):
        raise ValueError('Alarms must be up to 100 explicit active alarm strings')
    if 'alarms_complete' in row and type(row['alarms_complete']) is not bool: raise ValueError('Invalid alarm completeness flag')
    return deepcopy(row)


def observations(root,path):
    data,digest=read_json(root,path)
    if (not isinstance(data,dict) or data.get('format')!='fanuc-diagnostic-observations'
        or type(data.get('schema_version')) is not int or data['schema_version']!=1
        or not isinstance(data.get('observations'),list) or len(data['observations'])>2000):
        raise ValueError('Expected version 1 diagnostic observations (up to 2000 rows)')
    rows=[validate_observation(row) for row in data['observations']]
    if any(a['source_id']!=b['source_id'] or a['timestamp']>b['timestamp'] for a,b in zip(rows,rows[1:])):
        raise ValueError('Observations mix sources or move backwards')
    return rows,digest


class Condition:
    """Small three-valued comparison/AND/OR/NOT grammar. Never executes LS."""
    TOKEN=re.compile(r'\s*(>=|<=|<>|!=|==|=|>|<|\(|\)|[-+]?(?:\d+(?:\.\d*)?|\.\d+)|[A-Z][A-Z0-9_]*(?:\[\d+(?:,\d+)?\])?)',re.I)

    def __init__(self,expression,sample):
        self.tokens=[];self.at=0;self.sample=sample;self.atoms=[]
        # Keep spaces between words; canonicalization alone would join AND/OR.
        text=re.sub(r'\s*([\[\],])\s*',r'\1',expression.upper()).strip()
        end=0
        for match in self.TOKEN.finditer(text):
            if match.start()!=end: raise ValueError('Unsupported condition syntax')
            self.tokens.append(match[1]);end=match.end()
        if end!=len(text) or len(self.tokens)>256: raise ValueError('Unsupported condition syntax')

    def take(self):
        if self.at>=len(self.tokens): raise ValueError('Incomplete condition')
        token=self.tokens[self.at];self.at+=1;return token

    def peek(self): return self.tokens[self.at] if self.at<len(self.tokens) else None

    def scalar(self):
        token=self.take()
        if token in ('ON','TRUE'): return True,token
        if token in ('OFF','FALSE'): return False,token
        if re.fullmatch(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)',token): return float(token),token
        if not re.fullmatch(r'(?:R|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|F)\[\d+\]',token): raise ValueError('Unsupported condition operand')
        values={canonical(key):value for key,value in self.sample.get('values',{}).items()}
        errors={canonical(key) for key in self.sample.get('errors',{})}
        value=None if token in errors else values.get(token)
        if token.split('[')[0] in IO_TYPES and isinstance(value,str) and value.upper() in ('ON','OFF'):
            value=value.upper()=='ON'
        if type(value) not in (int,float,bool) or not math.isfinite(value): value=None
        return value,token

    def primary(self):
        if self.peek()=='NOT':
            self.take();value=self.primary();return None if value is None else not value
        if self.peek()=='(':
            self.take();value=self.expression()
            if self.take()!=')': raise ValueError('Expected closing condition bracket')
            return value
        left,lt=self.scalar();op=self.take();right,rt=self.scalar()
        if op not in ('=','==','!=','<>','>','<','>=','<='): raise ValueError('Expected comparison')
        value=None
        if left is not None and right is not None:
            if op in ('=','=='): value=left==right
            elif op in ('!=','<>'): value=left!=right
            elif op=='>': value=left>right
            elif op=='<': value=left<right
            elif op=='>=': value=left>=right
            elif op=='<=': value=left<=right
        self.atoms.append({'left':lt,'operator':op,'right':rt,'observed_left':left,'observed_right':right,'satisfied':value})
        return value

    def conjunction(self):
        value=self.primary()
        while self.peek()=='AND':
            self.take();other=self.primary()
            value=False if value is False or other is False else None if value is None or other is None else True
        return value

    def expression(self):
        value=self.conjunction()
        while self.peek()=='OR':
            self.take();other=self.conjunction()
            value=True if value is True or other is True else None if value is None or other is None else False
        return value

    def evaluate(self):
        value=self.expression()
        if self.at!=len(self.tokens): raise ValueError('Unsupported condition tail')
        return value,self.atoms


def explain_wait(root,path,observation,*,max_age_seconds=5):
    if type(max_age_seconds) not in (int,float) or not math.isfinite(max_age_seconds) or not 0<=max_age_seconds<=300:
        raise ValueError('Use a maximum sample age of 0..300 seconds')
    row=validate_observation(observation)
    listing=inspect_selection(root,[path])[0]
    task,sample=row.get('task'),row.get('sample')
    result={'status':'unresolved','reasons':[],'condition':None,'satisfied':None,'atoms':[],
            'source':listing['file'],'observation_timestamp':row['timestamp'],'source_id':row['source_id'],
            'alarms':row.get('alarms',[]),'alarms_complete':row.get('alarms_complete',False),
            'blocking_cause_verified':False,'capture_is_atomic':False,'controller_contacted':False}
    if not task or task['program'].upper()!=listing['file']['program']: result['reasons'].append('task_program_mismatch_or_missing')
    if not task or task.get('program_sha256')!=listing['file']['source_sha256']: result['reasons'].append('listing_revision_unverified_or_mismatched')
    if not sample or not 0<=row['timestamp']-sample['timestamp']<=max_age_seconds: result['reasons'].append('sample_missing_stale_or_future')
    nodes=[node for node in listing['nodes'] if task and node['tp_line_number']==task['line']]
    if len(nodes)!=1: result['reasons'].append('task_line_missing_or_ambiguous')
    result['listing_issue_count']=len(listing['issues'])
    if any(issue['code'] in ('program_sections_unsupported','unsupported_mn_boundary') for issue in listing['issues']): result['reasons'].append('listing_structure_unresolved')
    if result['reasons']: return result
    node=nodes[0];result['instruction']=node;result['condition']=node['condition']
    if node['kind']!='WAIT': result['reasons'].append('reported_instruction_is_not_wait');return result
    try: result['satisfied'],result['atoms']=Condition(node['condition'],sample).evaluate()
    except ValueError: result['reasons'].append('condition_unsupported_or_indirect');return result
    if result['satisfied'] is None: result['reasons'].append('condition_values_unavailable');return result
    result['status']='condition_satisfied_at_sample' if result['satisfied'] else 'condition_unsatisfied_at_sample'
    if task['state'].upper()!='WAITING': result['reasons'].append('task_wait_state_unverified')
    result['unsatisfied_terms']=[atom for atom in result['atoms'] if atom['satisfied'] is False]
    return result


class TriggeredRecorder:
    """Bounded pre/post observations; callers own acquisition and program revisions."""
    def __init__(self,*,before=20,after=20,triggers=('alarm','task_pause','program_transition','signal_change'),signals=(),max_captures=20):
        if any(type(n) is not int or not 0<=n<=100 for n in (before,after)) or type(max_captures) is not int or not 1<=max_captures<=20:
            raise ValueError('Use pre/post counts 0..100 and capture limit 1..20')
        if not set(triggers)<={'alarm','task_pause','program_transition','signal_change'} or not triggers: raise ValueError('Unknown trigger kind')
        if len(signals)>100 or any(not isinstance(signal,str) for signal in signals): raise ValueError('Use up to 100 explicit trigger signals')
        self.before,self.after,self.triggers,self.signals=before,after,set(triggers),[canonical(signal) for signal in signals]
        self.max_captures=max_captures;self.history=deque(maxlen=before);self.captures=[];self.previous=None;self.received=0

    def add(self,observation):
        row=validate_observation(observation)
        # Bound live caller objects too, including nested values.
        raw=json.dumps(row,allow_nan=False,ensure_ascii=True).encode()
        if len(raw)>16384: raise ValueError('Observation exceeds 16 KiB')
        if self.received>=2000: raise ValueError('Triggered recorder reached 2000 observations')
        previous=self.previous
        if previous and (row['source_id']!=previous['source_id'] or row['timestamp']<previous['timestamp']): raise ValueError('Observation source changed or clock moved backwards')
        for capture in self.captures:
            if capture['post_remaining']:
                capture['observations'].append(row);capture['post_remaining']-=1
        events=[]
        if previous:
            if 'alarm' in self.triggers and previous.get('alarms_complete') and row.get('alarms_complete'):
                new=set(row.get('alarms',[]))-set(previous.get('alarms',[]))
                if new: events.append({'kind':'alarm','new':sorted(new)})
            oldtask,newtask=previous.get('task') or {},row.get('task') or {}
            if oldtask and newtask and oldtask.get('name')==newtask.get('name'):
                if 'task_pause' in self.triggers and oldtask['state'].upper()=='RUNNING' and newtask['state'].upper()=='PAUSED': events.append({'kind':'task_pause'})
                revision_changed=oldtask.get('program_sha256') and newtask.get('program_sha256') and oldtask['program_sha256']!=newtask['program_sha256']
                if 'program_transition' in self.triggers and (oldtask['program']!=newtask['program'] or revision_changed): events.append({'kind':'program_transition'})
            if 'signal_change' in self.triggers:
                oldsample,newsample=previous.get('sample') or {},row.get('sample') or {}
                oldvalues={canonical(k):v for k,v in oldsample.get('values',{}).items()}
                newvalues={canonical(k):v for k,v in newsample.get('values',{}).items()}
                errors={canonical(k) for k in [*oldsample.get('errors',{}),*newsample.get('errors',{})]}
                for signal in self.signals:
                    if signal not in errors and oldvalues.get(signal) is not None and newvalues.get(signal) is not None and oldvalues[signal]!=newvalues[signal]:
                        events.append({'kind':'signal_change','signal':signal,'old':oldvalues[signal],'new':newvalues[signal]})
        if events and len(self.captures)<self.max_captures:
            self.captures.append({'trigger_index':self.received,'timestamp':row['timestamp'],'events':events,
                'pre_available':len(self.history),'pre_requested':self.before,'post_requested':self.after,
                'post_remaining':self.after,'observations':[*self.history,row]})
        self.history.append(row);self.previous=row;self.received+=1
        return deepcopy(events)

    def document(self):
        return {'format':'fanuc-triggered-captures','schema_version':1,'received_observations':self.received,
                'captures':deepcopy(self.captures),'capture_limit_reached':len(self.captures)>=self.max_captures,
                'capture_is_atomic':False,'controller_contacted':False}


def triggered_captures(root,paths,observation_path,*,before=20,after=20,triggers=None,signals=(),view='captures',capture_index=0,offset=0,limit=20):
    rows,digest=observations(root,observation_path)
    recorder=TriggeredRecorder(before=before,after=after,triggers=triggers or ('alarm','task_pause','program_transition','signal_change'),signals=signals)
    for row in rows: recorder.add(row)
    data=recorder.document()
    listings=inspect_selection(root,paths)
    revisions={listing['file']['source_sha256']:listing['file'] for listing in listings}
    for capture in data['captures']:
        requirements={(row['task']['program'],row['task'].get('program_sha256')) for row in capture['observations'] if row.get('task')}
        capture['program_revisions']=[{'program':program,'sha256':sha,'matching_file':revisions[sha]['path'] if sha in revisions and revisions[sha]['program']==program.upper() else None}
                                      for program,sha in sorted(requirements,key=str)]
        capture['post_complete']=capture['post_remaining']==0
    if view=='observations':
        if type(capture_index) is not int or not 0<=capture_index<len(data['captures']): raise ValueError('Select an existing capture index')
        selected=data['captures'][capture_index]['observations']
    elif view=='captures': selected=[{key:value for key,value in capture.items() if key!='observations'} for capture in data['captures']]
    else: raise ValueError('Choose captures or observations view')
    return report_page(selected,view=view,offset=offset,limit=limit,observation_sha256=digest,
                       received_observations=len(rows),capture_limit_reached=data['capture_limit_reached'])
