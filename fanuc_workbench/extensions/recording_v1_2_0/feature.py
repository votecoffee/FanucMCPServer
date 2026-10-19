"""Offline recording bundles; original MIT code, retained wire.py Apache-2.0."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from . import summary
from .wire import inspect_evidence, self_test as wire_self_test
from .handshake import analyze_handshake, self_test as handshake_self_test


MAX_PAYLOAD_BYTES, MAX_FILES = 32 * 1024 * 1024, 1000
_HASH = re.compile(r'[0-9a-f]{64}')


def _json(data):
    try:
        return json.loads(data.decode('utf-8-sig'), object_pairs_hook=summary._pairs,
                          parse_constant=summary._constant)
    except (UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError('Unsupported evidence JSON') from error


def _relative(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or any(ord(char) < 32 or char in '\\:' for char in value)
            or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts
            or PurePosixPath(value).as_posix() != value or value == '.'):
        raise ValueError('Evidence path must be a normalized relative path')
    return value


def _path(root, relative):
    relative = _relative(relative)
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('Evidence path escapes bundle root')
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent.is_relative_to(root)):
        raise ValueError('Evidence paths cannot contain symbolic links')
    return path


def _verified(root, entry, maximum):
    if (not isinstance(entry, dict) or not {'path', 'bytes', 'sha256'} <= entry.keys()
            or type(entry['bytes']) is not int or not 0 <= entry['bytes'] <= maximum
            or not isinstance(entry['sha256'], str) or not _HASH.fullmatch(entry['sha256'])):
        raise ValueError('Invalid bounded evidence file record')
    path = _path(root, entry['path'])
    with path.open('rb') as stream:
        data = stream.read(entry['bytes'] + 1)
    if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError('Evidence bytes differ from bundle manifest')
    return data


def _capture_match(capture, recorded, native):
    if (not isinstance(capture, dict) or not {'timestamp', 'values', 'errors'} <= capture.keys()
            or not isinstance(capture['values'], dict) or not isinstance(capture['errors'], dict)):
        raise ValueError('Capture requires timestamp, values and errors')
    values = capture['values']
    if native:
        if any(not isinstance(value, dict) or ('values' if value.get('kind') == 'PR' else 'value') not in value
               for value in values.values()):
            raise ValueError('Invalid structured native capture')
        values = {name: value.get('values') if value.get('kind') == 'PR' else value.get('value')
                  for name, value in values.items()}
    summary._validate({'samples': [{'timestamp': capture['timestamp'], 'values': values, 'errors': capture['errors']}]})
    return summary._key([capture['timestamp'], values, capture['errors']]) == summary._key(
        [recorded['timestamp'], recorded['values'], recorded.get('errors', {})])


def inspect_bundle(path: Path, *, root: Path | None = None, offset=0, limit=20):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use offset >= 0 and limit 1-100')
    path = Path(path)
    root = Path(root or path.parent).resolve()
    if path.parent.resolve() != root or path.is_symlink():
        raise ValueError('Bundle manifest must be directly inside bundle root')
    with path.open('rb') as stream:
        manifest_bytes = stream.read(1024 * 1024 + 1)
    if len(manifest_bytes) > 1024 * 1024:
        raise ValueError('Bundle manifest exceeds 1 MiB')
    manifest = _json(manifest_bytes)
    if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or manifest.get('kind') != 'recording_evidence'
            or not isinstance(manifest.get('recording'), dict)
            or not isinstance(manifest.get('samples'), list) or len(manifest['samples']) > 100):
        raise ValueError('Supply a version 1 recording evidence bundle')
    recording = manifest['recording']
    if recording.get('robot_id') is not None and (not isinstance(recording['robot_id'], str)
                                                 or not 1 <= len(recording['robot_id']) <= 128):
        raise ValueError('Invalid configured robot identifier')
    if recording.get('path') != 'recording/recording.json':
        raise ValueError('Unsupported recording path')
    recording_data = _verified(root, recording, summary.MAX_BYTES)
    samples, _, references = summary._validate(_json(recording_data))
    if len(references) != len(samples) or len(references) != len(set(references)):
        raise ValueError('Recording references do not align uniquely with samples')
    if not isinstance(recording.get('artifact_id'), str) or not summary._ARTIFACT_ID.fullmatch(recording['artifact_id']):
        raise ValueError('Invalid recording ID')
    selection = manifest.get('selection')
    if (not isinstance(selection, dict) or any(type(selection.get(key)) is not int
            for key in ['offset', 'limit', 'total', 'count']) or selection['offset'] < 0
            or not 1 <= selection['limit'] <= 100 or selection['total'] != len(samples)):
        raise ValueError('Invalid bundle selection')
    indices = list(range(selection['offset'], min(len(samples), selection['offset'] + selection['limit'])))
    next_offset = selection['offset'] + len(indices) if selection['offset'] + len(indices) < len(samples) else None
    if (selection['count'] != len(indices) or selection.get('next_offset') != next_offset
            or (selection.get('next_offset') is not None and type(selection['next_offset']) is not int)):
        raise ValueError('Bundle selection metadata differs from recording')
    if len(manifest['samples']) != len(indices):
        raise ValueError('Bundle selected sample count differs')
    expected_paths = {path.name, recording['path']}
    payload_bytes = len(recording_data) + len(manifest_bytes)
    rows = []
    for index, item in zip(indices, manifest['samples']):
        if (not isinstance(item, dict) or type(item.get('index')) is not int or item['index'] != index
                or item.get('artifact_id') != references[index] or item.get('robot_id') != recording.get('robot_id')
                or not isinstance(item.get('files'), list) or not item['files']):
            raise ValueError('Bundle sample identity or index differs from recording')
        files = {}
        for entry in item['files']:
            if not isinstance(entry, dict):
                raise ValueError('Invalid sample file record')
            original = _relative(entry.get('original_path'))
            if entry.get('path') != f'samples/{index:03d}/{original}' or entry['path'] in expected_paths:
                raise ValueError('Sample path or file uniqueness differs from bundle contract')
            payload = _verified(root, entry, 4 * 1024 * 1024)
            payload_bytes += len(payload)
            expected_paths.add(entry['path'])
            if payload_bytes > MAX_PAYLOAD_BYTES or len(expected_paths) > MAX_FILES:
                raise ValueError('Bundle exceeds byte or file limit')
            files[original] = (entry, payload)
        if ('sample.json' in files) == ('native_sample.json' in files):
            raise ValueError('Sample requires exactly one recognized capture JSON')
        native = 'native_sample.json' in files
        entry, capture_data = files['native_sample.json' if native else 'sample.json']
        issues, wire = [], None
        provider, matches = 'native' if native else 'unknown' if 'wire.json' in files else 'files', None
        try:
            capture = _json(capture_data)
            if not native and isinstance(capture, dict) and capture.get('provider') == 'snpx':
                provider = 'snpx'
            matches = _capture_match(capture, samples[index], native)
        except (ValueError, TypeError):
            issues.append('capture_shape_invalid')
        else:
            if not matches:
                issues.append('recording_sample_differs_from_capture')
            if not native and capture.get('provider') == 'snpx':
                provider = 'snpx'
                if 'wire.json' not in files:
                    issues.append('snpx_wire_missing')
                else:
                    wire_entry = files['wire.json'][0]
                    try:
                        result = inspect_evidence(_path(root, entry['path']), _path(root, wire_entry['path']))
                    except ValueError:
                        issues.append('snpx_evidence_invalid')
                    else:
                        if (result['capture_source_sha256'] != entry['sha256']
                                or result['wire_source_sha256'] != wire_entry['sha256']):
                            raise ValueError('Evidence changed during wire inspection')
                        wire = {key: result[key] for key in ['supported_trace_valid', 'wire_hash_matches_capture',
                            'successful_read_exchanges', 'zero_payload_read_exchanges',
                            'application_write_requests_observed', 'issues', 'exchange_issue_counts',
                            'io_value_binding_checked', 'word_payload_matches_wire']}
                        if not wire['supported_trace_valid']:
                            issues.append('snpx_trace_outside_supported_contract')
        if provider == 'unknown':
            issues.append('sample_provider_unknown')
        rows.append({'index': index, 'artifact_id': item['artifact_id'], 'provider': provider,
                     'recording_sample_matches_capture': matches, 'wire': wire, 'issues': issues})
    actual_paths = set()
    for count, member in enumerate(root.rglob('*'), 1):
        if count > 4000 or member.is_symlink() or not member.resolve().is_relative_to(root):
            raise ValueError('Bundle contains unsupported paths or links')
        if member.is_file():
            actual_paths.add(member.relative_to(root).as_posix())
    if actual_paths != expected_paths or payload_bytes > MAX_PAYLOAD_BYTES:
        raise ValueError('Bundle file set or byte limit differs from manifest')
    snpx = [row for row in rows if row['provider'] in {'snpx', 'unknown'}]
    return {'bundle_source_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
            'recording_source_sha256': hashlib.sha256(recording_data).hexdigest(),
            'bundle_hashes_verified': True, 'source_manifest_claim_verified': False,
            'recording_samples_total': len(samples), 'selection': selection,
            'selection_covers_recording': selection['offset'] == 0 and len(rows) == len(samples),
            'total': len(rows), 'offset': offset, 'next_offset': offset + limit if offset + limit < len(rows) else None,
            'items': rows[offset:offset + limit], 'provider_counts': dict(Counter(row['provider'] for row in rows)),
            'issue_counts': dict(Counter(issue for row in rows for issue in row['issues'])),
            'all_selected_samples_match_recording': all(row['recording_sample_matches_capture'] is True for row in rows)
                if rows else None,
            'all_selected_evidence_consistent': all(not row['issues'] for row in rows) if rows else None,
            'all_selected_snpx_traces_valid': all(row['wire'] and row['wire']['supported_trace_valid'] for row in snpx)
                if snpx else None,
            'capture_provenance_verified': False, 'recording_completion_verified': False,
            'io_value_binding_checked': False, 'mapping_verified': False,
            'controller_contacted': False, 'controller_written': False, 'evidence_only': True}


def self_test():
    return summary.self_test() and wire_self_test() and handshake_self_test()


def handle(operation_id, arguments, context):
    if operation_id == 'workbench.recording.handshake':
        options = {key: value for key, value in arguments.items() if key not in {'artifact_id', 'path'}}
        return analyze_handshake(Path(context['recording']['file']), **options)
    if operation_id == 'workbench.recording.summarize':
        return summary.handle(operation_id, arguments, context)
    if operation_id != 'workbench.recording.inspect_bundle':
        raise ValueError('Unknown recording operation')
    options = {key: value for key, value in arguments.items() if key not in {'artifact_id', 'path'}}
    return inspect_bundle(Path(context['bundle']['file']), root=Path(context['bundle']['root']), **options)
