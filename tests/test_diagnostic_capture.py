import hashlib
import json

import pytest

from fanuc_workbench.services.diagnostic_capture import DiagnosticCaptureSession,native_observation
from fanuc_workbench.services.program_analysis import TriggeredRecorder


def native(timestamp,value=False,state=1,active=False,program='MAIN'):
    return ({'timestamp':timestamp,'values':{'DI[1]':value,'R[1]':{'kind':'R','value':7}},'errors':{}},
            {'timestamp':timestamp+.1,'tasks':[{'name':'T1','program':program,'current_line':1,'status_code':state}],
             'alarms':{'active_alarm_flag':active,'history':[{'mnemonic':'SRVO','number':1,'message':'Synthetic history'}]},'errors':{}})


def test_native_projection_never_guesses_numeric_task_states_or_active_alarm_history():
    data,status=native(1,active=True)
    row=native_observation(data,status,'synthetic','T1',{})
    assert row['task']['state']=='UNVERIFIED_STATUS' and row['task']['program_sha256'] is None
    assert row['sample']['values']['R[1]']==7
    assert row['alarm_active_flag'] and row['alarms']==[] and not row['alarms_complete'] and row['alarm_history']
    status['errors']['tasks']='Unavailable';status['errors']['alarms']='Unavailable'
    row=native_observation(data,status,'synthetic','T1',{})
    assert 'task' not in row and 'alarm_active_flag' not in row


def test_native_trigger_retains_actual_ls_bytes_hash_read_time_and_partial_bundle(tmp_path):
    raw=b'/PROG MAIN\n/ATTR\n/MN\n1: WAIT DI[1]=ON ;\n/POS\n/END\n'
    revision=tmp_path/'read';revision.mkdir();(revision/'MAIN.LS').write_bytes(raw)
    calls=[]
    def read_program(name):
        calls.append(name)
        return {'local_path':str(revision/'native_capture.json'),'target':name,'value':{'ls_sha256':hashlib.sha256(raw).hexdigest()}}
    session=DiagnosticCaptureSession('synthetic',task_name='T1',status_states={'1':'RUNNING','2':'PAUSED'},before=1,after=2,signals=['DI[1]'])
    session.observe_native(*native(1),'synthetic',read_program)
    assert not calls
    session.observe_native(*native(2,True,state=2,active=True),'synthetic',read_program)
    assert calls==['MAIN']
    with pytest.raises(ValueError,match='target changed'):session.observe_native(*native(3),'other',read_program)
    result=session.save(tmp_path/'bundle')
    assert result['captures']==1 and result['program_revisions']==1 and not session.armed
    capture=json.loads((tmp_path/'bundle/captures.json').read_text())['captures'][0]
    trigger=capture['observations'][capture['pre_available']]
    assert trigger['task']['revision_binding']=='native_ls_read_after_trigger' and trigger['task']['revision_capture_timestamp']
    assert capture['post_remaining']==2
    manifest=json.loads((tmp_path/'bundle/manifest.json').read_text())
    for row in manifest['files']:assert hashlib.sha256((tmp_path/'bundle'/row['path']).read_bytes()).hexdigest()==row['sha256']
    assert (revision/'MAIN.LS').read_bytes()==raw


def test_revision_failures_retain_capture_and_gap_rows_do_not_infer_signal_change(tmp_path):
    session=DiagnosticCaptureSession('synthetic',task_name='T1',signals=['DI[1]'])
    def missing(_):raise OSError('Synthetic missing native listing')
    session.observe_native(*native(1),'synthetic',missing)
    session.observe_native(*native(2,True),'synthetic',missing)
    assert session.recorder.captures[0]['program_revision_error']=='OSError' and not session.revisions
    recorder=TriggeredRecorder(signals=['DI[1]'])
    recorder.add(native_observation(*native(1),'synthetic','T1',{}))
    gap=native_observation(*native(10,True),'synthetic','T1',{});gap['gap_before']=True
    recorder.add(gap)
    assert not recorder.captures
