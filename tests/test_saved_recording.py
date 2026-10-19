import hashlib
import json

import pytest

from fanuc_workbench.services import saved_recording
from fanuc_workbench.services.live_recording import LiveRecording, recording_source
from fanuc_workbench.services.monitoring import Sample, WatchSpec


def document():
    recording = LiveRecording()
    recording.ensure_context(recording_source(workspace=None), [WatchSpec('R[1]', 'Count'), WatchSpec('PR[1]', 'Pose')])
    recording.add(Sample(1, {'Count': 0, 'Pose': {'J1': 1.5}}))
    recording.add(Sample(2, {'Count': 999}, {'Count': 'Unavailable'}))
    recording.pause('Pause', timestamp=2.5)
    recording.add(Sample(4, {'Count': 1}))
    recording.add(Sample(5, {'Count': 2}))
    recording.cycle.mark('START')
    recording.cycle_recording = True
    return recording.document()


def write_document(tmp_path, data):
    path = tmp_path/'recording.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


def test_roundtrip_retains_errors_gaps_events_open_cycle_and_hash_without_changing_original(tmp_path):
    data = document()
    path = write_document(tmp_path, data)
    original = path.read_bytes()
    saved = saved_recording.load_recording(path)
    assert saved.document() == data
    assert saved.sha256 == hashlib.sha256(original).hexdigest() and saved.byte_count == len(original)
    assert saved.samples[1].errors == {'Count': 'Unavailable'} and saved.trend_breaks == [2]
    assert saved.document()['cycle_recording'] is True
    changed = saved.document()
    changed['samples'][0]['values']['Pose']['J1'] = 999
    saved.samples[0].values['Pose']['J1'] = 888
    assert saved.document()['samples'][0]['values']['Pose']['J1'] == 1.5
    assert path.read_bytes() == original


def test_cleared_rows_empty_exports_and_older_optional_fields_remain_readable(tmp_path):
    data = document()
    data['samples'] = data['samples'][2:]
    data['first_retained_sample'] = 2
    saved = saved_recording.load_recording(write_document(tmp_path, data))
    assert len(saved.samples) == 2 and saved.trend_breaks == [0]
    for key in ('first_retained_sample', 'gap_before_samples', 'pauses'):
        del data[key]
    saved = saved_recording.load_recording(write_document(tmp_path, data))
    assert saved.document()['first_retained_sample'] == 2 and saved.trend_breaks == []
    empty = LiveRecording().export_json(tmp_path/'empty.json')
    assert not saved_recording.load_recording(empty).samples


@pytest.mark.parametrize('modify', [
    lambda d: d.update(format='other'),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(created_at_utc='2026-01-01T00:00:00'),
    lambda d: d.update(created_at_utc='invalid'),
    lambda d: d.update(received_samples=True),
    lambda d: d.update(received_samples=0),
    lambda d: d.update(sample_limit=10001),
    lambda d: d.update(first_retained_sample=1),
    lambda d: d.update(gap_before_samples=[2, 2]),
    lambda d: d.update(gap_before_samples=[4]),
    lambda d: d.update(gap_before_samples=[True]),
    lambda d: d.update(pauses=[{'timestamp': 3, 'after_sample': 4, 'reason': 'Invalid'}]),
    lambda d: d.update(source={'kind': 'workspace', 'path': 'ignored', 'command': 'ignored'}),
    lambda d: d.update(source=None),
    lambda d: d.update(watches=[{'expression': 'EXEC unknown', 'alias': ''}]),
    lambda d: d.update(watches=[{'expression': 'R[1]', 'alias': 'Same'}, {'expression': 'R[2]', 'alias': 'Same'}]),
    lambda d: d.update(cycle_recording='true'),
    lambda d: d['samples'][1].update(timestamp=0),
    lambda d: d['samples'][0].update(timestamp=True),
    lambda d: d['samples'][0].update(timestamp=1e100),
    lambda d: d['samples'][0].update(errors={'Count': None}),
    lambda d: d['samples'][0].update(values=[]),
    lambda d: d['timeline'][0].update(signal=2),
    lambda d: d['cycle'][0].update(state=False),
    lambda d: d.update(extra='unknown'),
])
def test_invalid_metadata_and_rows_refuse_without_touching_source(tmp_path, modify):
    data = document()
    modify(data)
    path = write_document(tmp_path, data)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        saved_recording.load_recording(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize('raw', [
    b'{"format":"first", "format":"second"}', b'{"value":NaN}', b'{"value":Infinity}',
    b'{"value":1e999}', b'{"value":"\\ud800"}', b'{"x":"\xff"}', b'{"incomplete":',
    b'['*33 + b'0' + b']'*33,
])
def test_invalid_duplicate_nonfinite_encoding_and_deep_json_refuse(tmp_path, raw):
    path = tmp_path/'recording.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        saved_recording.load_recording(path)


def test_file_node_string_signal_and_event_bounds(tmp_path, monkeypatch):
    path = write_document(tmp_path, document())
    with monkeypatch.context() as patch:
        patch.setattr(saved_recording, 'MAX_FILE_BYTES', 32)
        with pytest.raises(ValueError, match='file limit'):
            saved_recording.load_recording(path)
    for name, limit, message in [('MAX_NODES', 20, 'JSON values'), ('MAX_STRING', 3, 'characters'),
                                 ('MAX_SIGNALS', 1, 'at most'), ('MAX_EVENTS', 0, 'at most')]:
        with monkeypatch.context() as patch:
            patch.setattr(saved_recording, name, limit)
            with pytest.raises(ValueError, match=message):
                saved_recording.load_recording(path)


def test_source_profile_is_only_metadata_and_cannot_include_a_password(tmp_path, monkeypatch):
    from fanuc_workbench.core.models import RobotProfile
    data = document()
    data['source'] = recording_source(live=True, profile=RobotProfile('Synthetic', '192.0.2.1', password='not exported'))
    # Loading must not connect, resolve the embedded workspace, or create a profile.
    monkeypatch.setattr(RobotProfile, 'from_dict', lambda *_: pytest.fail('Metadata became a connection profile'))
    path = write_document(tmp_path, data)
    assert saved_recording.load_recording(path).document()['source'] == data['source']
    data['source']['profile']['password'] = 'unwanted'
    with pytest.raises(ValueError):
        saved_recording.load_recording(write_document(tmp_path, data))


def test_utf8_bom_quoted_braces_and_escaped_quotes_preserve_exact_bytes(tmp_path):
    data = document()
    data['pauses'][0]['reason'] = 'Literal [braces] { and "quotes" \\ plus é'
    path = tmp_path/'bom.json'
    raw = b'\xef\xbb\xbf' + json.dumps(data, ensure_ascii=False).encode('utf-8')
    path.write_bytes(raw)
    saved = saved_recording.load_recording(path)
    assert saved.document() == data and saved.sha256 == hashlib.sha256(raw).hexdigest()


def test_node_bound_refuses_before_json_container_allocation(tmp_path, monkeypatch):
    path = tmp_path/'many.json'
    path.write_text('[[],[],[],[]]')
    monkeypatch.setattr(saved_recording, 'MAX_NODES', 4)
    monkeypatch.setattr(saved_recording.json, 'loads', lambda *_args, **_kwargs: pytest.fail('Decoder should not run'))
    with pytest.raises(ValueError, match='JSON values'):
        saved_recording.load_recording(path)
