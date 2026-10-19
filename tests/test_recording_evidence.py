import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

import pytest

from fanuc_workbench.bridge.extension_release import install_release, load_release
from fanuc_workbench.extensions.recording_v1_1_0.feature import self_test
from fanuc_workbench.services.recording_analysis import inspect_bundle
from test_bridge_operations import bridge_case
from test_recording_bundle import artifact, recording_case
from test_snpx_evidence import trace


def bundle_case(bridge, *, count=3, provider='files'):
    samples, references = [], []
    for index in range(count):
        sample = {'timestamp': index * 2, 'values': {'DI[1]': bool(index % 2)}, 'errors': {}}
        capture = dict(sample)
        files = {}
        if provider == 'native':
            capture['values'] = {'DI[1]': {'kind': 'DI', 'value': bool(index % 2)}}
            files['native_sample.json'] = capture
        else:
            if provider == 'snpx':
                wire = trace(bytes(2))
                request = bytearray.fromhex(wire[2]['request_hex'])
                request[43] = 0x48
                struct.pack_into('<H', request, 46, 8)
                wire[2]['request_hex'] = request.hex()
                wire_bytes = json.dumps(wire).encode()
                capture.update(provider='snpx', wire_sha256=hashlib.sha256(wire_bytes).hexdigest())
                files['wire.json'] = wire_bytes
            files['sample.json'] = capture
        references.append(artifact(bridge, 'sample', files)['artifact_id'])
        samples.append(sample)
    recording = artifact(bridge, 'recording', {'recording.json': {'samples': samples, 'sample_artifacts': references}})
    bundled = bridge.operations.bundle_recording(recording['artifact_id'])
    root = bridge.operations.store.verify(bundled['artifact_id'])
    return root, bundled


def rewrite_file(root, original_path, document, index=0):
    path = root / original_path
    path.write_bytes(json.dumps(document).encode())
    manifest_path = root / 'bundle.json'
    manifest = json.loads(manifest_path.read_bytes())
    entries = ([manifest['recording']] if original_path.startswith('recording/')
               else manifest['samples'][index]['files'])
    entry = next(row for row in entries if row['path'] == original_path)
    entry.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')


@pytest.mark.parametrize('provider', ['files', 'native', 'snpx'])
def test_bundle_links_and_optional_wire_checks_preserve_offline_limits(bridge_case, provider):
    bridge, robot, _ = bridge_case
    root, _ = bundle_case(bridge, provider=provider)
    original = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    result = inspect_bundle(root / 'bundle.json', limit=1)
    assert result['bundle_hashes_verified'] and result['all_selected_samples_match_recording']
    assert result['all_selected_evidence_consistent']
    assert result['provider_counts'] == {provider: 3} and result['total'] == 3 and result['next_offset'] == 1
    assert not result['issue_counts'] and result['selection_covers_recording']
    assert result['all_selected_snpx_traces_valid'] == (True if provider == 'snpx' else None)
    assert not result['source_manifest_claim_verified'] and not result['capture_provenance_verified']
    assert not result['recording_completion_verified'] and not result['io_value_binding_checked']
    if provider == 'snpx':
        assert result['items'][0]['wire']['successful_read_exchanges'] == 1
        assert result['items'][0]['wire']['io_value_binding_checked'] is False
    assert {path: path.read_bytes() for path in original} == original
    assert robot.connects == robot.uploads == 0


def test_native_pr_values_use_same_projection_as_watch_recording(bridge_case):
    bridge, _, _ = bridge_case
    capture = {'timestamp': 0, 'values': {'PR[1]': {'kind': 'PR', 'values': {'J1': 5.25}}}, 'errors': {}}
    ref = artifact(bridge, 'sample', {'native_sample.json': capture})['artifact_id']
    rec = artifact(bridge, 'recording', {'recording.json': {'samples': [
        {'timestamp': 0, 'values': {'PR[1]': {'J1': 5.25}}, 'errors': {}}], 'sample_artifacts': [ref]}})
    result = bridge.operations.bundle_recording(rec['artifact_id'])
    assert inspect_bundle(bridge.operations.store.root(result['artifact_id']) / 'bundle.json')['all_selected_samples_match_recording']


def test_capture_mismatch_is_explicit_even_with_internally_consistent_bundle_hashes(bridge_case):
    bridge, _, _ = bridge_case
    root, _ = bundle_case(bridge)
    rewrite_file(root, 'samples/000/sample.json', {'timestamp': 0, 'values': {'DI[1]': 1}, 'errors': {}})
    result = inspect_bundle(root / 'bundle.json', offset=2, limit=1)
    assert result['bundle_hashes_verified'] and not result['all_selected_samples_match_recording']
    assert not result['all_selected_evidence_consistent']
    assert result['issue_counts'] == {'recording_sample_differs_from_capture': 1}
    assert result['items'][0]['index'] == 2 and not result['items'][0]['issues']
    assert result['next_offset'] is None


def test_bad_wire_contract_is_independent_of_sample_link_and_bundle_hashes(bridge_case):
    bridge, _, _ = bridge_case
    root, _ = bundle_case(bridge, provider='snpx')
    wire = json.loads((root / 'samples/000/wire.json').read_bytes())
    wire[2]['response_hex'] = ''
    rewrite_file(root, 'samples/000/wire.json', wire)
    result = inspect_bundle(root / 'bundle.json')
    assert result['bundle_hashes_verified'] and result['all_selected_samples_match_recording']
    assert not result['all_selected_snpx_traces_valid']
    assert result['items'][0]['wire']['wire_hash_matches_capture'] is False
    assert result['issue_counts'] == {'snpx_trace_outside_supported_contract': 1}


@pytest.mark.parametrize('problem', ['missing-wire', 'bad-json', 'bad-capture', 'missing-native-value'])
def test_unavailable_evidence_does_not_become_verified_zero(bridge_case, problem):
    bridge, _, _ = bridge_case
    provider = 'native' if problem == 'missing-native-value' else 'snpx'
    root, _ = bundle_case(bridge, provider=provider)
    if problem == 'missing-wire':
        manifest_path = root / 'bundle.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['samples'][0]['files'] = [row for row in manifest['samples'][0]['files'] if row['original_path'] != 'wire.json']
        (root / 'samples/000/wire.json').unlink()
        manifest_path.write_text(json.dumps(manifest))
    elif problem == 'missing-native-value':
        rewrite_file(root, 'samples/000/native_sample.json', {'timestamp': 0, 'values': {'DI[1]': {}}, 'errors': {}})
    else:
        rewrite_file(root, 'samples/000/sample.json', {'provider': 'snpx'} if problem == 'bad-capture' else ['unexpected'])
    result = inspect_bundle(root / 'bundle.json')
    assert result['items'][0]['issues']
    if provider == 'snpx':
        assert result['all_selected_snpx_traces_valid'] is False
    if problem != 'missing-wire':
        assert not result['all_selected_samples_match_recording']


@pytest.mark.parametrize('problem', ['hash', 'extra-file', 'missing-file', 'path-traversal', 'absolute-path',
    'duplicate-path', 'wrong-index', 'wrong-artifact', 'wrong-robot', 'wrong-count', 'wrong-total', 'wrong-next'])
def test_malformed_bundle_manifest_paths_and_file_sets_are_refused(bridge_case, problem):
    bridge, _, _ = bridge_case
    root, _ = bundle_case(bridge)
    manifest_path = root / 'bundle.json'
    manifest = json.loads(manifest_path.read_bytes())
    entry = manifest['samples'][0]['files'][0]
    if problem == 'hash':
        (root / entry['path']).write_text('changed')
    elif problem == 'extra-file':
        (root / 'extra.txt').write_text('extra')
    elif problem == 'missing-file':
        (root / entry['path']).unlink()
    elif problem in {'path-traversal', 'absolute-path'}:
        entry['path'] = '../outside' if problem == 'path-traversal' else 'C:/outside'
    elif problem == 'duplicate-path':
        manifest['samples'][0]['files'].append(entry)
    elif problem in {'wrong-index', 'wrong-artifact', 'wrong-robot'}:
        manifest['samples'][0][{'wrong-index': 'index', 'wrong-artifact': 'artifact_id', 'wrong-robot': 'robot_id'}[problem]] = 99
    else:
        manifest['selection'][{'wrong-count': 'count', 'wrong-total': 'total', 'wrong-next': 'next_offset'}[problem]] = 99
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, FileNotFoundError)):
        inspect_bundle(manifest_path)


@pytest.mark.parametrize('options', [{'offset': True}, {'offset': -1}, {'limit': 0}, {'limit': 101}])
def test_bad_analysis_options_fail_before_open(options):
    with pytest.raises(ValueError):
        inspect_bundle(Path('does-not-exist'), **options)


def test_empty_and_partial_bundle_coverage_is_explicit(bridge_case):
    bridge, _, _ = bridge_case
    root, _ = bundle_case(bridge, count=0)
    result = inspect_bundle(root / 'bundle.json')
    assert result['all_selected_samples_match_recording'] is None and result['total'] == 0
    rec, _, _ = recording_case(bridge)
    bundled = bridge.operations.bundle_recording(rec['artifact_id'], sample_offset=1, sample_limit=1)
    result = inspect_bundle(bridge.operations.store.root(bundled['artifact_id']) / 'bundle.json')
    assert result['recording_samples_total'] == 3 and not result['selection_covers_recording']
    assert result['all_selected_samples_match_recording'] and result['total'] == 1


def test_release_preserves_prior_summary_and_licenses():
    assert self_test()
    root = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'
    previous, _ = load_release(root / 'recording')
    current, _ = load_release(root / 'recording_v1_1_0')
    assert current['operations'][0] == previous['operations'][0] and current['version'] == '1.1.0'
    assert (root / 'recording/feature.py').read_bytes() == (root / 'recording_v1_1_0/summary.py').read_bytes()
    assert {'LICENSE.txt', 'APACHE-2.0.txt', 'NOTICE.txt'} <= current['files'].keys()


def test_same_mcp_session_bundles_upgrades_inspects_and_rolls_back(bridge_case, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    bridge, robot, config = bridge_case
    rec, _, _ = recording_case(bridge)
    policy = tmp_path / 'stdio-policy.json'
    policy.write_text(json.dumps({'state_dir': str(config.state_dir), 'source_dir': str(config.source_dir),
                                  'robots': {}, 'allow_writes': False}))
    releases = Path(__file__).resolve().parents[1] / 'fanuc_workbench/extensions'

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump() for tool in (await session.list_tools()).tools]

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.isError, response
                return json.loads(response.content[0].text)

            core = await call('describe_operation', {'operation_id': 'core.recordings.bundle_evidence'})
            bundled = (await call('run_read_operation', {'operation_id': core['id'], 'revision': core['revision'],
                'arguments': {'artifact_id': rec['artifact_id']}}))['result']
            install_release(config.state_dir / 'extensions', releases / 'recording')
            await call('reload_extensions', {})
            install_release(config.state_dir / 'extensions', releases / 'recording_v1_1_0')
            assert not (await call('reload_extensions', {}))['mcp_tools_changed']
            description = await call('describe_operation', {'operation_id': 'workbench.recording.inspect_bundle'})
            request = {'operation_id': description['id'], 'revision': description['revision'],
                'arguments': {'artifact_id': bundled['artifact_id'], 'path': 'bundle.json'}}
            assert (await call('run_read_operation', request))['result']['all_selected_samples_match_recording']
            (bridge.operations.store.root(bundled['artifact_id']) / 'samples/000/sample.json').write_text('{}')
            assert (await session.call_tool('run_read_operation', request)).isError
            install_release(config.state_dir / 'extensions', releases / 'recording')
            await call('reload_extensions', {})
            assert (await session.call_tool('run_read_operation', request)).isError
            assert [tool.model_dump() for tool in (await session.list_tools()).tools] == tools and len(tools) == 48
    asyncio.run(scenario())
    assert robot.connects == robot.uploads == 0
