"""Archive/path integrity and scan-to-import fencing, using synthetic code only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from fanuc_workbench.bridge import stable_launcher as launch


def archives(root, members=None):
    root.mkdir(exist_ok=True, parents=True)
    (root / "layout.json").write_bytes(launch.encoded({"schema_version": 1, "layout": "archive-v1"}))
    for name in ("dependencies.zip", "stdlib.zip"):
        with zipfile.ZipFile(root / name, "w") as archive:
            for path, data in (members or [("allowed.py", b"VALUE=17\n")]):
                info = zipfile.ZipInfo(path)
                info.filename = path  # Keep deliberate backslashes on Windows.
                archive.writestr(info, data)


@pytest.mark.parametrize("member", ["../escape.py", "/absolute.py", "a\\bad.py", "C:drive.py",
    "a/./b.py", "a//b.py", "trailing./bad.py", "native.pyd", "stale.pyc"])
def test_unsafe_archive_members_refuse_before_import(tmp_path, member):
    archives(tmp_path, [(member, b"raise AssertionError('never executed')")])
    with pytest.raises(ValueError):
        launch.verify_archives(tmp_path)


def test_case_ambiguous_and_link_archives_refuse(tmp_path):
    archives(tmp_path, [("module.py", b""), ("MODULE.py", b"")])
    with pytest.raises(ValueError, match="ambiguous"):
        launch.verify_archives(tmp_path)
    info = zipfile.ZipInfo("link.py")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(tmp_path / "dependencies.zip", "w") as archive:
        archive.writestr(info, "elsewhere.py")
    with pytest.raises(ValueError, match="ambiguous"):
        launch.verify_archives(tmp_path)


@pytest.mark.parametrize("path", ["../escape", "DLLs/../../escape", "C:/escape", "Lib\\escape"])
def test_bootstrap_subset_cannot_escape(tmp_path, path):
    with pytest.raises(ValueError, match="member path"):
        launch.verify_bootstrap_python(tmp_path, [{"path": path, "sha256": "0" * 64, "bytes": 0}])


@pytest.mark.skipif(os.name != "nt", reason="Windows deny-write/delete handles")
def test_verified_files_cannot_be_changed_between_scan_and_import(tmp_path):
    runtime = tmp_path / "runtimes/26.1024.110000"
    archives(runtime)
    (runtime / "native/pywin32_system32").mkdir(parents=True)
    (runtime / "native/substitute.py").write_bytes(b"raise AssertionError('unselected native directory')")
    python = tmp_path / "python"
    (python / "DLLs").mkdir(parents=True)
    (python / "Lib").mkdir()
    (python / "Lib/unselected.py").write_bytes(b"raise AssertionError('legacy Lib')")
    manifest = {"runtime_files": launch.inventory(runtime), "python_files": []}
    script = tmp_path / "probe.py"
    script.write_text("""
import runpy,sys,json,importlib
from pathlib import Path
n=runpy.run_path(sys.argv[1],run_name='synthetic_launcher')
r=Path(sys.argv[2]);m=json.loads(sys.argv[3])
n['pinned_tree'](r,m['runtime_files'])
n['verify_archives'](r)
for operation in ('write','replace','rename'):
    try:
        if operation=='write': (r/'dependencies.zip').write_bytes(b'changed')
        elif operation=='replace':
            (r/'replacement').write_bytes(b'changed')
            (r/'replacement').replace(r/'dependencies.zip')
        else: r.rename(r.with_name('moved'))
    except OSError: pass
    else: raise AssertionError(operation+' bypassed pin')
n['configure_packages'](r,m)
assert importlib.import_module('allowed').VALUE==17
for name in ('substitute','unselected'):
    try: importlib.import_module(name)
    except ModuleNotFoundError: pass
    else: raise AssertionError('directory import admitted: '+name)
print('PIN_AND_IMPORT_PASS')
""", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-B", str(script), str(Path(launch.__file__).resolve()),
        str(runtime), json.dumps(manifest)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "PIN_AND_IMPORT_PASS" in result.stdout
    # The fixture process released its own handles; nothing was deleted/replaced.
    assert (runtime / "dependencies.zip").read_bytes().startswith(b"PK")


def test_archive_payload_tamper_is_a_hash_failure(tmp_path):
    archives(tmp_path)
    manifest = launch.inventory(tmp_path)
    (tmp_path / "dependencies.zip").write_bytes(b"substituted after inventory")
    with pytest.raises(ValueError, match="mismatch"):
        launch.verify(tmp_path, manifest)


def test_checked_bytecode_requires_paired_source_and_exact_format(tmp_path):
    from importlib._bootstrap_external import _code_to_hash_pyc
    from importlib.util import source_hash
    source = b"VALUE=17\n"
    bytecode = bytes(_code_to_hash_pyc(compile(source, "allowed.py", "exec"), source_hash(source), checked=True))
    archives(tmp_path, [("allowed.py", source), ("allowed.pyc", bytecode)])
    launch.verify_archives(tmp_path)
    archives(tmp_path, [("allowed.py", b"VALUE=18\n"), ("allowed.pyc", bytecode)])
    with pytest.raises(ValueError, match="source hash"):
        launch.verify_archives(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows immutable payload versus publication sharing")
def test_pinned_runtime_keeps_sibling_publication_replaceable(tmp_path):
    runtime = tmp_path / "runtimes/26.1026.80000"
    archives(runtime)
    entry = tmp_path / "StartMcp.py"
    entry.write_bytes(b"old reviewed entry")
    script = tmp_path / "publication.py"
    script.write_text("""
import sys,json,runpy
from pathlib import Path
n=runpy.run_path(sys.argv[1],run_name='pin_fixture')
root=Path(sys.argv[2]);runtime=root/'runtimes/26.1026.80000'
n['pinned_tree'](runtime,n['inventory'](runtime))
n['atomic'](root/'StartMcp.py',b'new reviewed entry')
assert (root/'StartMcp.py').read_bytes()==b'new reviewed entry'
try: runtime.rename(runtime.with_name('moved'))
except OSError: pass
else: raise AssertionError('Pinned directory rename was allowed')
""", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-B", str(script), str(Path(launch.__file__).resolve()),
        str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows archive/native configuration")
def test_archive_importer_preserves_resources_metadata_namespaces_and_source(tmp_path):
    runtime = tmp_path / "runtimes/26.1026.80001"
    archives(runtime, [("tiny_package/__init__.py", b"VALUE=17\n"),
        ("tiny_package/submodule.py", b"def answer():\n    return 42\n"),
        ("tiny_package/resources/note.txt", b"synthetic resource"),
        ("namespace_pkg/child.py", b"VALUE=23\n"),
        ("tiny_package-1.0.dist-info/METADATA", b"Metadata-Version: 2.1\nName: tiny-package\nVersion: 1.0\n")])
    (runtime / "native/pywin32_system32").mkdir(parents=True)
    script = tmp_path / "resources.py"
    script.write_text("""
import sys,runpy,inspect,pkgutil,importlib.metadata,importlib.resources.readers
from pathlib import Path
# This tiny archive intentionally omits the stdlib. Materialize the standard
# metadata parser before the synthetic import roots replace the real roots.
importlib.metadata.version('pytest')
importlib.resources.files('importlib')
n=runpy.run_path(sys.argv[1],run_name='archive_contract')
r=Path(sys.argv[2]);n['verify_archives'](r)
n['configure_packages'](r,{'runtime_files':n['inventory'](r),'python_files':[]})
import tiny_package, tiny_package.submodule, namespace_pkg.child
assert tiny_package.submodule.answer()==42 and namespace_pkg.child.VALUE==23
assert importlib.metadata.version('tiny-package')=='1.0'
assert pkgutil.get_data('tiny_package','resources/note.txt')==b'synthetic resource'
assert importlib.resources.files('tiny_package').joinpath('resources/note.txt').read_bytes()==b'synthetic resource'
assert 'return 42' in inspect.getsource(tiny_package.submodule.answer)
assert tiny_package.__loader__.__class__.__name__=='PinnedZipImporter'
""", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-B", str(script), str(Path(launch.__file__).resolve()),
        str(runtime)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows selected archive importer")
def test_deferred_pair_validation_still_refuses_before_module_execution(tmp_path):
    from importlib._bootstrap_external import _code_to_hash_pyc
    from importlib.util import source_hash
    runtime = tmp_path / "runtimes/26.1026.80002"
    source = b"raise AssertionError('bytecode must never execute')\n"
    bytecode = bytes(_code_to_hash_pyc(compile(source, "forbidden.py", "exec"), source_hash(source), checked=True))
    archives(runtime, [("forbidden.py", b"VALUE=17\n"), ("forbidden.pyc", bytecode)])
    (runtime / "native/pywin32_system32").mkdir(parents=True)
    with pytest.raises(ValueError, match="source hash"):
        launch.verify_archives(runtime)
    # Complete archive hashes remain mandatory in selected(). Only pair
    # decoding is deferred, and the loader must refuse before marshal/exec.
    script = tmp_path / "deferred.py"
    script.write_text("""
import runpy,sys
from pathlib import Path
n=runpy.run_path(sys.argv[1],run_name='deferred_fixture');r=Path(sys.argv[2])
n['verify_archives'](r,eager_bytecode=False)
n['configure_packages'](r,{'runtime_files':n['inventory'](r),'python_files':[]})
try: import forbidden
except ImportError as exc: assert 'source' in str(exc)
else: raise AssertionError('Unpaired bytecode was executed')
""", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-B", str(script), str(Path(launch.__file__).resolve()),
        str(runtime)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_guarded_receipt_reader_allows_atomic_peer_publication_and_bounds_data(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    path = directory / "receipt.json"
    path.write_bytes(b'{"state":"active"}')
    with launch.session_reader(directory) as read:
        assert read(path) == b'{"state":"active"}'
        launch.atomic(path, b'{"state":"closed"}')
        assert read(path) == b'{"state":"closed"}'
        with pytest.raises(ValueError, match="escaped|file type"):
            read(tmp_path / "elsewhere.json")
        path.write_bytes(b"x" * 65537)
        with pytest.raises(ValueError, match="limit"):
            read(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows receipt directory reparse refusal")
def test_guarded_receipt_reader_rejects_directory_junction(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    junction = directory / "receipt.json"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(other)], capture_output=True)
    assert result.returncode == 0, result.stderr
    try:
        with launch.session_reader(directory) as read:
            with pytest.raises(ValueError, match="reparse|type"):
                read(junction)
    finally:
        junction.rmdir()  # Remove only this test's empty junction, never target contents.
