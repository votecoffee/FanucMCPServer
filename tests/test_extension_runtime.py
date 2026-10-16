from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

from fanuc_workbench.bridge.extension_release import install_release, load_registry, load_release
from fanuc_workbench.bridge.extension_process import FeatureWorker


def release(root, *, version='1.0.0', code=None, operations=None, name='demo'):
    root.mkdir(parents=True)
    payload = code or "def handle(operation_id, arguments, context):\n    return {'value': arguments['value']}\n"
    (root / 'feature.py').write_text(payload, encoding='utf-8')
    schema = {'type': 'object', 'properties': {'value': {'type': 'string', 'maxLength': 100}},
              'required': ['value'], 'additionalProperties': False}
    manifest = {'schema_version': 1, 'id': name, 'version': version, 'host_api': 1,
        'entrypoint': 'feature.py', 'files': {'feature.py': hashlib.sha256((root / 'feature.py').read_bytes()).hexdigest()},
        'operations': operations or [{'id': name + '.read', 'description': 'Read a local feature result', 'effect': 'read',
            'input_schema': schema, 'output_schema': {'type': 'object'}}]}
    (root / 'extension.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


def test_local_install_is_pinned_preserves_versions_and_does_not_overwrite(tmp_path):
    source, state = tmp_path / 'source', tmp_path / 'state'
    release(source)
    result = install_release(state, source)
    target = state / 'releases/demo/1.0.0'
    assert target.is_dir() and result['status'] == 'staged_only' and not result['controller_contacted']
    assert load_registry(state)[0]['manifest_sha256'] == result['manifest_sha256']
    assert install_release(state, source) == result
    newer = tmp_path / 'newer'
    release(newer, version='1.1.0')
    install_release(state, newer)
    assert target.is_dir() and load_registry(state)[0]['version'] == '1.1.0'
    original = (target / 'feature.py').read_bytes()
    (source / 'feature.py').write_text('changed')
    with pytest.raises(ValueError, match='hash changed'):
        install_release(state, source)
    assert (target / 'feature.py').read_bytes() == original


@pytest.mark.parametrize('mutation', [
    lambda m: m.update(host_api=2),
    lambda m: m.update(id='core'),
    lambda m: m.update(version='../1'),
    lambda m: m.update(entrypoint='../feature.py'),
    lambda m: m['operations'][0].update(effect='write'),
    lambda m: m['operations'][0].update(id='core.read'),
    lambda m: m['operations'][0].update(id='demo.'),
    lambda m: m['operations'].append(copy.deepcopy(m['operations'][0])),
    lambda m: m['operations'][0]['input_schema'].update({'$ref': 'https://example.invalid/schema'}),
    lambda m: m['operations'][0].update(proposal_kind='file_replace'),
    lambda m: m['operations'][0].update(effect='prepare', proposal_kind='motion'),
    lambda m: m.update(arbitrary_code_path='elsewhere'),
])
def test_manifest_refuses_bad_contracts_before_code_execution(tmp_path, mutation):
    root = tmp_path / 'release'
    manifest = release(root)
    mutation(manifest)
    (root / 'extension.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises((ValueError, TypeError)):
        load_release(root)


def test_unlisted_files_duplicate_json_and_modified_bytes_are_refused(tmp_path):
    root = tmp_path / 'release'
    release(root)
    (root / 'extra.txt').write_text('unlisted')
    with pytest.raises(ValueError, match='unlisted'):
        load_release(root)
    (root / 'extra.txt').unlink()
    manifest_path = root / 'extension.json'
    original = manifest_path.read_text()
    manifest_path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match='Duplicate'):
        load_release(root)
    manifest_path.write_text(original)
    (root / 'feature.py').write_text('modified')
    with pytest.raises(ValueError, match='hash changed'):
        load_release(root)


def test_worker_runs_in_own_process_and_does_not_forward_secrets(tmp_path, monkeypatch):
    root = tmp_path / 'release'
    release(root, code="""import os
def handle(operation_id, arguments, context):
    print('feature output is not protocol output')
    return {'value': arguments['value'], 'secret': os.environ.get('FANUC_TEST_SECRET'), 'pid': os.getpid()}
""")
    monkeypatch.setenv('FANUC_TEST_SECRET', 'do-not-forward')
    manifest, checksum = load_release(root)
    worker = FeatureWorker(root, manifest, checksum, sys.executable)
    try:
        assert worker.healthy and worker.pid != os.getpid()
        result = worker.call('demo.read', {'value': 'exact'}, {})
        assert result == {'value': 'exact', 'secret': None, 'pid': worker.pid}
    finally:
        worker.close()
    assert not worker.healthy and worker.process.poll() is not None


@pytest.mark.parametrize('code', [
    "raise RuntimeError('private import details')\n",
    "def self_test(): return False\ndef handle(*args): return {}\n",
    "def handle(*args): return {}\ndef self_test():\n    import time\n    time.sleep(20)\n",
])
def test_bad_or_hanging_candidate_never_becomes_ready(tmp_path, code):
    root = tmp_path / 'release'
    release(root, code=code)
    manifest, checksum = load_release(root)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='startup or self-test'):
        FeatureWorker(root, manifest, checksum, sys.executable, startup_timeout=.75)
    assert time.monotonic() - started < 8


def test_timed_out_worker_is_closed_and_call_is_not_retried(tmp_path):
    root = tmp_path / 'release'
    release(root, code="def handle(*args):\n    import time\n    time.sleep(20)\n    return {}\n")
    manifest, checksum = load_release(root)
    worker = FeatureWorker(root, manifest, checksum, sys.executable, operation_timeout=.2)
    try:
        with pytest.raises(RuntimeError, match='no call was retried'):
            worker.call('demo.read', {'value': 'x'}, {})
        assert not worker.healthy and worker.process.poll() is not None
        with pytest.raises(RuntimeError, match='unavailable'):
            worker.call('demo.read', {'value': 'x'}, {})
    finally:
        worker.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows job-object lifecycle check')
def test_closing_worker_terminates_its_actual_python_and_spawned_child(tmp_path):
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = w.HANDLE
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    def alive(pid):
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = w.DWORD()
            assert kernel.GetExitCodeProcess(handle, c.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    root = tmp_path / 'release'
    release(root, code="""import subprocess, sys
def handle(*args):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    return {'child_pid': child.pid}
""")
    manifest, checksum = load_release(root)
    worker = FeatureWorker(root, manifest, checksum, sys.executable)
    try:
        child = worker.call('demo.read', {'value': 'x'}, {})['child_pid']
        assert alive(child) and alive(worker.pid)
    finally:
        worker.close()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (alive(child) or alive(worker.pid)):
        time.sleep(.02)
    assert not alive(child) and not alive(worker.pid)
