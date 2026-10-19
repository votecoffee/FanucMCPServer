import hashlib
import json

import pytest

from fanuc_workbench.services.program_analysis import trace_logic, explain_wait, TriggeredRecorder, triggered_captures
from fanuc_workbench.extensions.programs_v1_1_0.diagnostics import Condition


def listing(root, name='MAIN', body='1: WAIT DI[1]=ON ;', positions=''):
    path=root/(name+'.LS')
    path.write_text(f'/PROG {name}\n/ATTR\nCOMMENT = "Synthetic";\n/MN\n{body}\n/POS\n{positions}\n/END\n',encoding='ascii')
    return path


def observation(path=None, timestamp=10, value=False, state='WAITING', **changes):
    row={'timestamp':timestamp,'source_id':'synthetic-robot','task':{'name':'T1','program':'MAIN','line':1,'state':state,
         'program_sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path else None},
         'sample':{'timestamp':timestamp,'values':{'DI[1]':value,'R[1]':2},'errors':{}},'alarms':[],'alarms_complete':True}
    row.update(changes)
    return row


def test_trace_reads_writes_and_indirection_do_not_use_comments_strings_or_description_tokens(tmp_path):
    listing(tmp_path,body='''1: ! R[999]=5 ;
2: R[1:IGNORE DO[99]]=R[2]+GI[1] ;
3: IF (DI[1]=ON),DO[1]=OFF ;
4: PR[R[3],2].J1=R[4] ;
5: MESSAGE['R[999]=1'] ;''')
    report=trace_logic(tmp_path,['MAIN.LS'],view='accesses')
    rows={(row['tp_line_number'],row['token']):row for row in report['items']}
    assert rows[(2,'R[1]')]['access']=='write'
    assert rows[(2,'R[2]')]['access']=='read' and rows[(2,'GI[1]')]['access']=='read'
    assert rows[(3,'DI[1]')]['access']=='read' and rows[(3,'DO[1]')]['access']=='write'
    assert rows[(4,'PR[R[3],2].J1')]['access']=='write' and rows[(4,'PR[R[3],2].J1')]['indirect']
    assert rows[(4,'R[3]')]['access']=='read'
    assert not any('999' in row['token'] or '99]' in row['token'] for row in rows.values())


def test_control_flow_and_select_branches_report_unknown_jump_targets(tmp_path):
    listing(tmp_path,body='''1: IF (R[1]=1) THEN ;
2: R[2]=1 ;
3: ELSE ;
4: R[2]=2 ;
5: ENDIF ;
6: SELECT R[1]=1,JMP LBL[10] ;
7: =2,JMP LBL[20] ;
8: ELSE,JMP LBL[R[1]] ;
9: LBL[10] ;
10: WAIT DI[1]=ON ;
11: END ;''')
    report=trace_logic(tmp_path,['MAIN.LS'],view='edges')
    edges=report['items']
    assert any(row['from_tp_line']==1 and row['to_tp_line']==4 and row['branch']=='condition_false' for row in edges)
    assert any(row['from_tp_line']==3 and row['to_tp_line']==6 for row in edges)
    assert any(row['from_tp_line']==6 and row['to_tp_line']==9 and row['branch'].startswith('select_case') for row in edges)
    assert sum(row['resolution']=='unresolved_label' for row in edges)==2
    assert any(row['from_tp_line']==10 and row['to_tp_line']==10 for row in edges)
    assert not report['control_flow_complete']


def test_select_continuations_keep_all_case_targets_and_one_tp_instruction(tmp_path):
    listing(tmp_path,body='''1: SELECT R[1]=1,JMP LBL[10],
 : =2,JMP LBL[20],
 : ELSE,JMP LBL[R[2]] ;
2: LBL[10] ;
3: LBL[20] ;''')
    report=trace_logic(tmp_path,['MAIN.LS'],view='instructions')
    assert report['total']==3 and len(report['items'][0]['select_cases'])==3
    edges=trace_logic(tmp_path,['MAIN.LS'],view='edges')['items']
    assert len([edge for edge in edges if edge['from_tp_line']==1 and edge['branch'].startswith('select_case')])==3


@pytest.mark.parametrize('expression,expected',[
    ('DI[1]=ON',False),('(DI[1]=ON AND R[1]>=2)',False),
    ('DI[1]=ON OR R[1]=2',True),('NOT(DI[1]=ON)',True),
    ('DI[2]=ON',None),('DI[2]=ON OR DI[1]=ON',None),
    ('DI[2]=ON AND DI[1]=ON',False),('R[1]<>3',True),
])
def test_condition_evaluation_is_three_valued(expression,expected):
    value,atoms=Condition(expression,observation()['sample']).evaluate()
    assert value is expected and atoms


def test_wait_diagnosis_requires_matching_revision_location_and_fresh_values(tmp_path):
    path=listing(tmp_path)
    row=observation(path)
    report=explain_wait(tmp_path,'MAIN.LS',row)
    assert report['status']=='condition_unsatisfied_at_sample' and report['atoms'][0]['observed_left'] is False
    assert not report['blocking_cause_verified'] and not report['controller_contacted']
    row['sample']['timestamp']=0
    assert 'sample_missing_stale_or_future' in explain_wait(tmp_path,'MAIN.LS',row)['reasons']
    row=observation(path);row['task']['program_sha256']='0'*64
    assert 'listing_revision_unverified_or_mismatched' in explain_wait(tmp_path,'MAIN.LS',row)['reasons']
    row=observation(path);row['sample']['errors']['DI[1]']='Unavailable'
    assert explain_wait(tmp_path,'MAIN.LS',row)['satisfied'] is None
    listing(tmp_path,body='1: WAIT DI[R[1]]=ON ;')
    assert 'condition_unsupported_or_indirect' in explain_wait(tmp_path,'MAIN.LS',observation(path))['reasons']


def test_trigger_capture_pre_post_gaps_source_binding_and_partial_end(tmp_path):
    path=listing(tmp_path)
    recorder=TriggeredRecorder(before=2,after=2,signals=['DI[1]'])
    for index,value in enumerate([False,False,True,True]):
        recorder.add(observation(path,timestamp=index,value=value,state='RUNNING'))
    capture=recorder.document()['captures'][0]
    assert capture['trigger_index']==2 and len(capture['observations'])==4 and capture['post_remaining']==1
    before=recorder.document()
    with pytest.raises(ValueError,match='source'): recorder.add(observation(path,timestamp=5,source_id='other'))
    assert recorder.document()==before
    recorder.add(observation(path,timestamp=4,value=True,state='PAUSED',alarms=['SRVO-001']))
    assert recorder.document()['captures'][0]['post_remaining']==0
    assert {event['kind'] for event in recorder.document()['captures'][1]['events']}=={'alarm','task_pause'}
    gap=TriggeredRecorder(signals=['DI[1]'])
    gap.add(observation(path,timestamp=1,value=False))
    row=observation(path,timestamp=2,value=None);row['sample']['errors']['DI[1]']='Missing'
    gap.add(row);gap.add(observation(path,timestamp=3,value=True))
    assert not gap.captures


def test_saved_triggers_bind_exact_listing_revision_and_page_retained_samples(tmp_path):
    path=listing(tmp_path)
    rows=[observation(path,timestamp=index,value=index>1) for index in range(5)]
    data={'format':'fanuc-diagnostic-observations','schema_version':1,'observations':rows}
    evidence=tmp_path/'observations.json';evidence.write_text(json.dumps(data))
    report=triggered_captures(tmp_path,['MAIN.LS'],'observations.json',before=1,after=2,signals=['DI[1]'])
    assert report['items'][0]['post_complete'] and report['items'][0]['program_revisions'][0]['matching_file']=='MAIN.LS'
    assert 'observations' not in report['items'][0]
    page=triggered_captures(tmp_path,['MAIN.LS'],'observations.json',before=1,after=2,signals=['DI[1]'],view='observations',limit=1)
    assert page['total']==4 and page['next_offset']==1 and page['items'][0]['timestamp']==1
    path.write_text(path.read_text()+'\n! Changed revision')
    assert triggered_captures(tmp_path,['MAIN.LS'],'observations.json',signals=['DI[1]'])['items'][0]['program_revisions'][0]['matching_file'] is None
