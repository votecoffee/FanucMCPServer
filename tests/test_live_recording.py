import json

import pytest

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services import live_recording
from fanuc_workbench.services.monitoring import Sample,WatchSpec


def make_recording(limit=10000):
    source=live_recording.recording_source(live=True,profile=RobotProfile('Robot','192.0.2.1',password='synthetic-secret'))
    watches=[WatchSpec('R[1]','Count'),WatchSpec('PR[1]','Pose')]
    recording=live_recording.LiveRecording(sample_limit=limit);recording.ensure_context(source,watches)
    return recording,source,watches


def test_recording_binds_source_and_watches_and_preserves_original_sample_bytes(tmp_path):
    recording,source,watches=make_recording()
    sample=Sample(1,{'Count':1,'Pose':{'J1':2}}, {'DI1':'Synthetic unavailable'})
    recording.add(sample);sample.values['Pose']['J1']=99
    assert recording.last_sample.values['Pose']['J1']==2
    assert recording.received_samples==1
    assert 'password' not in source['profile']
    with pytest.raises(ValueError,match='source or watches'):
        recording.ensure_context({**source,'profile':{**source['profile'],'host':'192.0.2.2'}},watches)
    with pytest.raises(ValueError,match='source or watches'):
        recording.ensure_context(source,[WatchSpec('R[2]','Count')])
    path=recording.export_json(tmp_path/'recording.json');document=json.loads(path.read_text())
    assert document['format']=='fanuc-desktop-recording' and document['schema_version']==1
    assert document['samples'][0]['errors']=={'DI1':'Synthetic unavailable'}
    assert document['samples'][0]['values']['Pose']['J1']==2
    assert 'synthetic-secret' not in path.read_text()


def test_limits_and_invalid_samples_leave_all_retained_data_unchanged():
    recording,source,watches=make_recording(2)
    recording.add(Sample(1,{'Count':0}));before=recording.document()
    for sample in (Sample(float('nan'),{}),Sample(0,{}),Sample(2,{'Count':float('inf')}),Sample(1e200,{})):
        with pytest.raises(ValueError):recording.add(sample)
        assert recording.document()==before
    recording.add(Sample(2,{'Count':1}))
    assert recording.timeline.events[0].old==0 and recording.timeline.events[0].new==1
    with pytest.raises(ValueError,match='reached'):recording.ensure_context(source,watches)
    with pytest.raises(ValueError,match='limit'):recording.add(Sample(3,{'Count':2}))
    assert recording.received_samples==2 and len(recording.trend.samples)==2


def test_duplicate_keys_invalid_expressions_and_error_values_do_not_create_false_cycle_states():
    recording,source,watches=make_recording()
    with pytest.raises(ValueError,match='unique'):recording.ensure_context(source,[WatchSpec('R[1]','Same'),WatchSpec('R[2]','Same')])
    with pytest.raises(ValueError,match='Unsupported'):recording.ensure_context(source,[WatchSpec('EXEC program')])
    recording.cycle_recording=True
    recording.add(Sample(1,{'Count':1},{'Count':'Unavailable'}),cycle_key='Count')
    assert [event.state for event in recording.cycle.events]==['UNAVAILABLE']


def test_failed_export_preserves_existing_file_and_cleans_temporary_output(tmp_path,monkeypatch):
    recording,_,_=make_recording();recording.add(Sample(1,{'Count':1}))
    path=tmp_path/'recording.json';path.write_bytes(b'original export')
    def fail(*_):raise OSError('Synthetic replacement failure')
    monkeypatch.setattr(live_recording.os,'replace',fail)
    with pytest.raises(OSError):recording.export_json(path)
    assert path.read_bytes()==b'original export' and list(tmp_path.iterdir())==[path]


def test_numeric_plot_segments_break_on_errors_missing_values_nonfinite_data_and_pauses():
    samples=[Sample(1,{'Count':1}),Sample(2,{'Count':2}),Sample(3,{'Count':999},{'Count':'Unavailable'}),
             Sample(4,{'Count':4}),Sample(5,{}),Sample(6,{'Count':'nan'}),Sample(7,{'Count':7}),Sample(8,{'Count':8})]
    assert live_recording.numeric_segments(samples,'Count',break_before=[7])==[
        [(1,1.0),(2,2.0)],[(4,4.0)],[(7,7.0)],[(8,8.0)]]


def test_timeline_does_not_infer_transitions_across_unavailable_or_paused_observation():
    recording,_,_=make_recording()
    recording.add(Sample(1,{'Count':0}));recording.add(Sample(2,{'Count':999},{'Count':'Unavailable'}))
    recording.add(Sample(3,{'Count':1}));assert not recording.timeline.events
    recording.add(Sample(4,{'Count':2}));assert len(recording.timeline.events)==1
    recording.pause('User pause',timestamp=4.5)
    recording.add(Sample(6,{'Count':4}));assert len(recording.timeline.events)==1
    assert recording.trend_breaks==[4] and recording.document()['gap_before_samples']==[4]
    recording.clear_timeline();recording.add(Sample(7,{'Count':5}));assert not recording.timeline.events
    recording.trend.clear();recording.add(Sample(8,{'Count':6}))
    assert not recording.trend_breaks and recording.document()['first_retained_sample']==6
    assert recording.received_samples==7


def test_cycle_records_unavailable_and_paused_periods_separately():
    recording,_,_=make_recording();recording.cycle_recording=True
    recording.add(Sample(1,{'Count':1}),cycle_key='Count')
    recording.add(Sample(2,{'Count':None},{'Count':'Unavailable'}),cycle_key='Count')
    recording.add(Sample(3,{'Count':2}),cycle_key='Count')
    recording.pause('User pause',timestamp=3.5)
    recording.add(Sample(5,{'Count':3}),cycle_key='Count')
    assert [event.state for event in recording.cycle.events]==['1','UNAVAILABLE','2','PAUSED','3']
