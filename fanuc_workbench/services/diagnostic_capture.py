"""Opt-in capture adapter for existing native reads, with preserved LS revisions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time

from fanuc_workbench.services.program_analysis import TriggeredRecorder


def native_source_id(bridge,robot):
    profile=bridge.config.robots[robot].profile
    identity={'robot_id':robot,'host':profile.host,'port':profile.port,'transport':profile.transport}
    return hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()


def native_observation(data,status,source_id,task_name,status_states):
    row={'timestamp':status['timestamp'],'source_id':source_id,
         'sample':{'timestamp':data['timestamp'],'values':{},'errors':dict(data.get('errors',{}))},
         'alarms':[],'alarms_complete':False}
    for name,value in data.get('values',{}).items():
        # Native numeric registers are typed records; retain their scalar payload.
        row['sample']['values'][name]=value.get('value') if isinstance(value,dict) and value.get('kind')=='R' else value
    tasks=[] if status.get('errors',{}).get('tasks') else [task for task in status.get('tasks',[]) if task.get('name')==task_name]
    if len(tasks)==1 and tasks[0].get('program') and type(tasks[0].get('current_line')) is int and tasks[0]['current_line']>0:
        task=tasks[0];code=task.get('status_code')
        row['task']={'name':task_name,'program':task['program'],'line':task['current_line'],
                     'status_code':code,'state':status_states.get(str(code),'UNVERIFIED_STATUS'),
                     'program_sha256':None}
    alarms={} if status.get('errors',{}).get('alarms') else status.get('alarms',{})
    if type(alarms.get('active_alarm_flag')) is bool:
        row['alarm_active_flag']=alarms['active_alarm_flag']
        row['alarms_complete']=alarms['active_alarm_flag'] is False
    row['alarm_history']=[str(alarm.get('mnemonic',''))+'-'+str(alarm.get('number',''))+': '+str(alarm.get('message',''))
                          for alarm in alarms.get('history',[])[:100]]
    return row


class DiagnosticCaptureSession:
    def __init__(self,source_id,*,task_name,status_states=None,before=20,after=20,signals=()):
        if not isinstance(task_name,str) or not task_name.strip(): raise ValueError('Choose an explicit native task name')
        status_states={} if status_states is None else status_states
        if not isinstance(status_states,dict) or any(not isinstance(key,str) or value not in ('RUNNING','PAUSED','WAITING','IDLE','ABORTED') for key,value in status_states.items()):
            raise ValueError('Use an explicit numeric-status-to-state mapping')
        self.source_id,self.task_name,self.status_states=source_id,task_name,dict(status_states)
        self.recorder=TriggeredRecorder(before=before,after=after,signals=signals)
        self.revisions={};self.armed=True;self.lock=threading.RLock()

    def observe_native(self,data,status,source_id,read_program):
        if source_id!=self.source_id: raise ValueError('Native capture target changed. Disarm and create a new capture.')
        with self.lock:
            if not self.armed:return
            row=native_observation(data,status,source_id,self.task_name,self.status_states)
            if len(json.dumps(row,ensure_ascii=True,allow_nan=False).encode())>15*1024:raise ValueError('Native observation exceeds capture byte bound; reduce selected watches')
            count=len(self.recorder.captures)
            self.recorder.add(row)
            if len(self.recorder.captures)>count:
                capture=self.recorder.captures[-1];task=row.get('task')
                if task:
                    try:
                        receipt=read_program(task['program'])
                        folder=Path(receipt['local_path']).parent
                        with (folder/(task['program']+'.LS')).open('rb') as stream:raw=stream.read(1024*1024+1)
                        digest=hashlib.sha256(raw).hexdigest()
                        if len(raw)>1024*1024 or not raw or receipt.get('target')!=task['program'] or receipt['value'].get('ls_sha256')!=digest:
                            raise ValueError('Captured native LS revision hash differs')
                        self.revisions[digest]={'program':task['program'],'bytes':raw,'captured_at':time.time()}
                        # This is a sequential read after the trigger, never simultaneous proof.
                        captured=capture['observations'][capture['pre_available']]
                        captured['task']['program_sha256']=digest
                        captured['task']['revision_capture_timestamp']=self.revisions[digest]['captured_at']
                        captured['task']['revision_binding']='native_ls_read_after_trigger'
                        capture['program_revision_sha256']=digest
                    except Exception as exc:
                        capture['program_revision_error']=type(exc).__name__
            if (len(self.recorder.captures)>=self.recorder.max_captures and not any(capture['post_remaining'] for capture in self.recorder.captures)) or self.recorder.received>=2000:
                self.armed=False

    def save(self,destination):
        with self.lock:
            self.armed=False
            data=self.recorder.document();revisions=deepcopy(self.revisions)
        destination=Path(destination);destination.mkdir(parents=False,exist_ok=False)
        files=[]
        program_dir=destination/'programs';program_dir.mkdir()
        for digest,revision in revisions.items():
            relative='programs/'+digest+'_'+revision['program']+'.LS'
            with (destination/relative).open('xb') as stream:stream.write(revision['bytes'])
            files.append({'path':relative,'sha256':digest,'bytes':len(revision['bytes']),'captured_at':revision['captured_at']})
        retained={}
        for capture in data['captures']:
            start=capture['trigger_index']-capture['pre_available']
            for index,row in enumerate(capture['observations'],start):retained[index]=deepcopy(row)
        indices=sorted(retained);rows=[]
        for at,index in enumerate(indices):
            row=retained[index]
            if at and index!=indices[at-1]+1:row['gap_before']=True
            rows.append(row)
        observations={'format':'fanuc-diagnostic-observations','schema_version':1,'retained_observation_indices':indices,'observations':rows}
        data.update(program_files=list(files),source_id=self.source_id,revision_capture_is_atomic=False)
        for name,value in [('captures.json',data),('observations.json',observations)]:
            raw=json.dumps(value,indent=2,allow_nan=False).encode()
            with (destination/name).open('xb') as stream:stream.write(raw)
            files.append({'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
        with (destination/'manifest.json').open('x',encoding='utf-8') as stream:json.dump({'format':'fanuc-triggered-diagnostic-bundle','schema_version':1,'files':files,'controller_written':False},stream,indent=2)
        return {'path':str(destination),'captures':len(data['captures']),'retained_observations':len(rows),
                'program_revisions':len(revisions),'controller_written':False}
