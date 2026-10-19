"""Core-owned, offline copying of explicit recording references into one artifact."""
from __future__ import annotations

import hashlib
import json
from fanuc_workbench.bridge.storage import confined
from fanuc_workbench.extensions.recording.feature import MAX_BYTES, _constant, _pairs, _validate


MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
MAX_SAMPLE_FILE_BYTES = 4 * 1024 * 1024
MAX_FILES = 1000


def bundle_recording(bridge, artifact_id, sample_offset=0, sample_limit=20):
    if (type(sample_offset) is not int or sample_offset < 0
            or type(sample_limit) is not int or not 1 <= sample_limit <= 100):
        raise ValueError('Use sample_offset >= 0 and sample_limit 1-100')
    store, config = bridge.operations.store, bridge.config
    recording = store.get(artifact_id)
    if recording['kind'] != 'recording':
        raise ValueError('Supply a retained recording artifact ID')
    source = store.file(artifact_id, 'recording.json')
    with source.open('rb') as stream:
        data = stream.read(min(MAX_BYTES, config.max_file_bytes) + 1)
    if len(data) > min(MAX_BYTES, config.max_file_bytes):
        raise ValueError('Recording exceeds the bundle file limit')
    try:
        document = json.loads(data.decode('utf-8-sig'), object_pairs_hook=_pairs, parse_constant=_constant)
        samples, _, references = _validate(document)
    except (UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError('Unsupported recording JSON') from error
    if len(references) != len(samples) or len(set(references)) != len(references):
        raise ValueError('Recording requires one distinct sample reference per sample')
    expected = next(row for row in recording['files'] if row['path'] == 'recording.json')
    if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
        raise ValueError('Recording bytes changed during bundling')
    # Complete preflight precedes any output, including all selected source hashes.
    total_bytes, total_files, selected = len(data), 2, []
    maximum_bytes = min(MAX_PAYLOAD_BYTES, config.max_backup_bytes)
    maximum_files = min(MAX_FILES, config.max_backup_files)
    for index in range(sample_offset, min(len(samples), sample_offset + sample_limit)):
        identifier = references[index]
        item = store.get(identifier)
        if item['kind'] != 'sample' or item['robot_id'] != recording['robot_id']:
            raise ValueError('Sample reference kind or configured robot differs from recording')
        names = {row['path'] for row in item['files']}
        if ('sample.json' in names) == ('native_sample.json' in names):
            raise ValueError('Sample requires exactly one recognized capture JSON file')
        for row in item['files']:
            if (type(row['bytes']) is not int or row['bytes'] < 0
                    or row['bytes'] > min(MAX_SAMPLE_FILE_BYTES, config.max_file_bytes)):
                raise ValueError('Sample file exceeds the bundle file limit')
            total_bytes += row['bytes']
            total_files += 1
        if total_bytes > maximum_bytes or total_files > maximum_files:
            raise ValueError('Selected evidence exceeds bundle byte or file limits; select fewer samples')
        selected.append((index, identifier, item))
    if total_bytes > maximum_bytes or total_files > maximum_files:
        raise ValueError('Recording exceeds bundle byte or file limits')
    store.verify(artifact_id)
    for _, identifier, _ in selected:
        store.verify(identifier)
    destination = bridge._folder()
    recording_path = destination / 'recording/recording.json'
    recording_path.parent.mkdir()
    recording_path.write_bytes(data)
    rows = []
    for index, identifier, item in selected:
        files = []
        for entry in item['files']:
            original = entry['path']
            relative = f'samples/{index:03d}/{original}'
            target = confined(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with store.file(identifier, original).open('rb') as src, target.open('xb') as dst:
                while chunk := src.read(65536):
                    size += len(chunk)
                    if size > entry['bytes']:
                        raise ValueError('Sample grew during bundling')
                    digest.update(chunk)
                    dst.write(chunk)
            if size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
                raise ValueError('Sample bytes changed during bundling')
            files.append({'path': relative, 'original_path': original, 'bytes': size, 'sha256': digest.hexdigest()})
        rows.append({'index': index, 'artifact_id': identifier, 'robot_id': item['robot_id'], 'files': files})
    manifest = {'schema_version': 1, 'kind': 'recording_evidence',
        'recording': {'artifact_id': artifact_id, 'robot_id': recording['robot_id'],
                      'path': 'recording/recording.json', 'bytes': len(data), 'sha256': expected['sha256']},
        'selection': {'offset': sample_offset, 'limit': sample_limit, 'total': len(samples),
                      'count': len(rows), 'next_offset': sample_offset + len(rows)
                          if sample_offset + len(rows) < len(samples) else None},
        'samples': rows, 'source_manifests_checked': True, 'controller_contacted': False,
        'capture_provenance_verified': False, 'recording_completion_verified': False}
    manifest_bytes = json.dumps(manifest, indent=2, allow_nan=False).encode('utf-8')
    manifest_size = len(manifest_bytes)
    if manifest_size > min(1024 * 1024, config.max_file_bytes) or total_bytes + manifest_size > maximum_bytes:
        raise ValueError('Evidence manifest exceeds bundle limits; select fewer samples')
    (destination / 'bundle.json').write_bytes(manifest_bytes)
    artifact = store.register(destination, 'recording_evidence', robot_id=recording['robot_id'])
    bridge._audit('recording_evidence_bundled', artifact_id=artifact['artifact_id'],
                  recording_artifact=artifact_id, sample_offset=sample_offset, samples=len(rows))
    return {'artifact_id': artifact['artifact_id'], 'path': 'bundle.json', 'selection': manifest['selection'],
            'files': total_files, 'bytes': total_bytes + manifest_size, 'controller_contacted': False,
            'controller_written': False, 'capture_provenance_verified': False}
