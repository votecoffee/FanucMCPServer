from dataclasses import replace
import hashlib
import json

import pytest

from test_bridge_operations import bridge_case, finished


def artifact(bridge, kind, files, robot_id='r1'):
    folder = bridge._folder()
    for name, data in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return bridge.operations.store.register(folder, kind, robot_id=robot_id)


def recording_case(bridge, count=3):
    samples, references = [], []
    for index in range(count):
        sample = {'timestamp': index * 2, 'values': {'DI[1]': bool(index % 2)}, 'errors': {}}
        item = artifact(bridge, 'sample', {'sample.json': sample, 'IOSTATE.DG': b'synthetic bytes\r\n'})
        samples.append(sample)
        references.append(item['artifact_id'])
    recording = artifact(bridge, 'recording', {'recording.json': {'samples': samples, 'sample_artifacts': references},
                                               'trend.csv': b'original export'})
    return recording, references, samples


def test_bundle_preserves_exact_sources_and_pages_references_without_robot_access(bridge_case):
    bridge, robot, _ = bridge_case
    bridge.config = replace(bridge.config, allow_writes=False)
    recording, references, _ = recording_case(bridge)
    store = bridge.operations.store
    result = bridge.operations.bundle_recording(recording['artifact_id'], sample_offset=1, sample_limit=1)
    assert result['selection'] == {'offset': 1, 'limit': 1, 'count': 1, 'total': 3, 'next_offset': 2}
    assert store.get(result['artifact_id'])['kind'] == 'recording_evidence'
    root = store.verify(result['artifact_id'])
    manifest = json.loads((root / 'bundle.json').read_bytes())
    assert (root / 'recording/recording.json').read_bytes() == store.file(recording['artifact_id'], 'recording.json').read_bytes()
    assert manifest['samples'][0]['artifact_id'] == references[1] and manifest['samples'][0]['index'] == 1
    for row in manifest['samples'][0]['files']:
        assert (root / row['path']).read_bytes() == store.file(references[1], row['original_path']).read_bytes()
        assert hashlib.sha256((root / row['path']).read_bytes()).hexdigest() == row['sha256']
    assert result['bytes'] == sum(row['bytes'] for row in store.get(result['artifact_id'])['files'])
    assert result['files'] == 4 and not result['controller_contacted']
    assert not (root / 'trend.csv').exists()
    assert robot.connects == robot.uploads == 0


def test_actual_watch_bundle_needs_no_additional_controller_connection(bridge_case):
    bridge, robot, _ = bridge_case
    job = bridge.operations.start_watch('r1', ['R[1]'], samples=1)
    status = finished(bridge.operations.jobs, job['job_id'])
    assert status['status'] == 'complete'
    connections = robot.connects
    result = bridge.operations.bundle_recording(status['artifact_id'])
    assert result['selection']['count'] == 1 and result['selection']['next_offset'] is None
    assert robot.connects == connections and robot.uploads == 0


@pytest.mark.parametrize('offset', [0, 99])
def test_empty_selection_is_explicit_and_still_retains_recording(bridge_case, offset):
    bridge, _, _ = bridge_case
    recording, _, _ = recording_case(bridge, count=0)
    result = bridge.operations.bundle_recording(recording['artifact_id'], sample_offset=offset)
    assert result['selection']['count'] == result['selection']['total'] == 0
    assert result['selection']['next_offset'] is None and result['files'] == 2


@pytest.mark.parametrize('options', [{'sample_offset': -1}, {'sample_offset': True}, {'sample_limit': 0},
                                      {'sample_limit': 101}, {'sample_limit': False}])
def test_invalid_selection_is_refused_before_catalog_access(bridge_case, options):
    bridge, _, _ = bridge_case
    with pytest.raises(ValueError):
        bridge.operations.bundle_recording('unknown', **options)


@pytest.mark.parametrize('problem', ['missing', 'changed', 'extra-file', 'wrong-kind', 'wrong-robot'])
def test_missing_tampered_or_mismatched_references_never_register_a_bundle(bridge_case, problem):
    bridge, robot, _ = bridge_case
    recording, references, _ = recording_case(bridge)
    store = bridge.operations.store
    identifier = references[1]
    if problem == 'missing':
        (store.catalog / (identifier + '.json')).unlink()
    elif problem == 'changed':
        (store.root(identifier) / 'sample.json').write_text('{}')
    elif problem == 'extra-file':
        (store.root(identifier) / 'extra.txt').write_text('unexpected')
    else:
        path = store.catalog / (identifier + '.json')
        metadata = json.loads(path.read_bytes())
        metadata['kind' if problem == 'wrong-kind' else 'robot_id'] = 'other'
        path.write_text(json.dumps(metadata))
    before = set(store.catalog.glob('*.json'))
    with pytest.raises(ValueError):
        bridge.operations.bundle_recording(recording['artifact_id'])
    assert set(store.catalog.glob('*.json')) == before
    assert robot.connects == robot.uploads == 0


@pytest.mark.parametrize('references', [[], ['a' * 32], ['a' * 32, 'a' * 32], ['../outside', 'b' * 32]])
def test_reference_alignment_and_ids_are_required(bridge_case, references):
    bridge, _, _ = bridge_case
    item = artifact(bridge, 'recording', {'recording.json': {
        'samples': [{'timestamp': 0, 'values': {}}, {'timestamp': 2, 'values': {}}], 'sample_artifacts': references}})
    with pytest.raises(ValueError):
        bridge.operations.bundle_recording(item['artifact_id'])


@pytest.mark.parametrize('limits', [{'max_backup_bytes': 10}, {'max_backup_files': 2}, {'max_file_bytes': 10}])
def test_configured_byte_file_and_individual_size_limits_apply(bridge_case, limits):
    bridge, _, _ = bridge_case
    recording, _, _ = recording_case(bridge)
    bridge.config = replace(bridge.config, **limits)
    with pytest.raises(ValueError, match='limit'):
        bridge.operations.bundle_recording(recording['artifact_id'])


def test_wrong_artifact_kind_and_ambiguous_capture_files_are_refused(bridge_case):
    bridge, _, _ = bridge_case
    recording, references, _ = recording_case(bridge)
    with pytest.raises(ValueError, match='recording artifact'):
        bridge.operations.bundle_recording(references[0])
    store = bridge.operations.store
    path = store.root(references[0]) / 'native_sample.json'
    path.write_text('{}')
    metadata = store.get(references[0])
    metadata['files'].append({'path': path.name, 'bytes': 2, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (store.catalog / (references[0] + '.json')).write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='exactly one'):
        bridge.operations.bundle_recording(recording['artifact_id'])


def test_copy_hash_recheck_detects_changes_after_source_verification(bridge_case, monkeypatch):
    bridge, _, _ = bridge_case
    recording, references, _ = recording_case(bridge, count=1)
    store, calls = bridge.operations.store, []
    original = store.file

    def changed(identifier, relative):
        path = original(identifier, relative)
        if identifier == references[0] and relative == 'IOSTATE.DG':
            calls.append(relative)
            if len(calls) == 2:
                path.write_bytes(b'changed same size!')
        return path

    monkeypatch.setattr(store, 'file', changed)
    before = set(store.catalog.glob('*.json'))
    with pytest.raises(ValueError, match='changed|grew'):
        bridge.operations.bundle_recording(recording['artifact_id'])
    assert set(store.catalog.glob('*.json')) == before


def test_catalog_route_preserves_read_only_policy_and_requires_exact_revision(bridge_case):
    bridge, robot, _ = bridge_case
    recording, _, _ = recording_case(bridge, count=1)
    bridge.config = replace(bridge.config, allow_writes=False)
    descriptor = bridge.extensions.describe('core.recordings.bundle_evidence')
    args = {'artifact_id': recording['artifact_id'], 'sample_limit': 1}
    with pytest.raises(ValueError, match='revision'):
        bridge.extensions.run_read(descriptor['id'], args, 'stale')
    result = bridge.extensions.run_read(descriptor['id'], args, descriptor['revision'])['result']
    assert not result['controller_contacted'] and result['selection']['count'] == 1
    assert robot.connects == robot.uploads == 0
