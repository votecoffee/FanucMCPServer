"""Immutable selection and exact offline ownership; no robot/native operations."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fanuc_workbench.bridge import stable_launcher as launch


def fixture(root, release="26.1023.165651"):
    root.mkdir(exist_ok=True)
    (root / "python").mkdir(exist_ok=True)
    (root / "python/pythonw.exe").write_bytes(b"inert executable fixture")
    runtime = root / "runtimes" / release
    runtime.mkdir(parents=True)
    (runtime / "payload.txt").write_bytes(b"synthetic source")
    (root / "StartMcp.py").write_bytes(Path(launch.__file__).read_bytes())
    manifest = {"schema_version": 1, "profile": "standalone-offline", "runtime": release,
        "framework": "0.20.0", "sdk": "2.3.0", "launcher_sha256": launch.digest((root / "StartMcp.py").read_bytes()),
        "python_files": launch.inventory(root / "python"), "runtime_files": launch.inventory(runtime)}
    return manifest, runtime


def test_cache_removal_is_irrelevant_to_verified_app_owned_selection(tmp_path):
    import shutil
    cache = tmp_path / "disposable cache"
    cache.mkdir()
    root = tmp_path / "App with spaces 工具"
    manifest, runtime = fixture(root)
    identifier = launch.publish(root, manifest, expected_selection="absent")
    shutil.rmtree(cache)  # Only this test's empty synthetic cache.
    selected, actual, backend = launch.selected(root)
    assert selected["snapshot_id"] == identifier and actual == manifest and backend == runtime


@pytest.mark.parametrize("target", ["entry", "python", "payload", "extra", "manifest", "selection"])
def test_tamper_refuses_before_import_or_execution(tmp_path, target):
    manifest, runtime = fixture(tmp_path)
    identifier = launch.publish(tmp_path, manifest, expected_selection="absent")
    paths = {"entry": tmp_path / "StartMcp.py", "python": tmp_path / "python/pythonw.exe",
        "payload": runtime / "payload.txt", "extra": runtime / "extra.py",
        "manifest": tmp_path / "launcher/snapshots" / identifier / "manifest.json",
        "selection": tmp_path / "launcher/current.json"}
    paths[target].write_bytes(b"changed")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        launch.selected(tmp_path)


def test_atomic_update_preserves_prior_bytes_and_failed_cas_never_restores_stale_selection(tmp_path):
    first, old_runtime = fixture(tmp_path, "26.1023.100001")
    old_id = launch.publish(tmp_path, first, expected_selection="absent")
    old = (tmp_path / "launcher/current.json").read_bytes()
    second, new_runtime = fixture(tmp_path, "26.1023.100002")
    new_id = launch.publish(tmp_path, second, expected_selection=launch.digest(old))
    current = (tmp_path / "launcher/current.json").read_bytes()
    assert new_id != old_id and old_runtime.is_dir() and new_runtime.is_dir()
    assert (tmp_path / "launcher/backups" / (launch.digest(old) + ".json")).read_bytes() == old
    with pytest.raises(ValueError, match="selection changed"):
        launch.publish(tmp_path, first, expected_selection=launch.digest(old))
    assert (tmp_path / "launcher/current.json").read_bytes() == current
    (old_runtime / "payload.txt").write_bytes(b"tampered old candidate")
    with pytest.raises(ValueError, match="mismatch"):
        launch.publish(tmp_path, first, expected_selection=launch.digest(current))
    assert (tmp_path / "launcher/current.json").read_bytes() == current


def test_duplicate_metadata_keys_and_traversal_are_refused(tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        launch.parse(b'{"schema_version":1,"schema_version":2}')
    manifest, _ = fixture(tmp_path)
    manifest["runtime"] = "../other"
    launch.publish(tmp_path, {**manifest, "runtime": "26.1023.165651"}, expected_selection="absent")
    with pytest.raises(ValueError, match="member path"):
        launch.verify(tmp_path, [{"path": "../escape", "sha256": "0" * 64, "bytes": 0}])


def test_interrupted_atomic_replace_retains_exact_prior_selection(tmp_path, monkeypatch):
    first, _ = fixture(tmp_path, "26.1023.100001")
    launch.publish(tmp_path, first, expected_selection="absent")
    before = (tmp_path / "launcher/current.json").read_bytes()
    second, _ = fixture(tmp_path, "26.1023.100002")
    def interrupted(*args):
        raise OSError("Synthetic interruption before atomic pointer replacement")
    monkeypatch.setattr(launch.os, "replace", interrupted)
    with pytest.raises(OSError, match="interruption"):
        launch.publish(tmp_path, second, expected_selection=launch.digest(before))
    assert (tmp_path / "launcher/current.json").read_bytes() == before
    assert (tmp_path / "launcher/backups" / (launch.digest(before) + ".json")).read_bytes() == before
    assert not list((tmp_path / "launcher").glob(".fanuc-publish-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction refusal")
def test_selection_reparse_refused(tmp_path):
    import subprocess
    manifest, _ = fixture(tmp_path)
    launch.publish(tmp_path, manifest, expected_selection="absent")
    link = tmp_path / "junction"
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(tmp_path / "launcher")], capture_output=True)
    assert result.returncode == 0
    with pytest.raises(ValueError, match="reparse"):
        launch.read(link / "current.json")


def identity(pid, *, image="c:\\physical-store\\pythonw.exe", created="100"):
    return {"host_id": "1" * 64, "pid": pid, "created": created, "executable": image}


class Driver:
    def __init__(self, state="absent"):
        self.prior_state = state
        self.closed = False
    def capture(self, pid):
        return identity(pid)
    def _open(self, pid, terminate=False):
        assert pid == os.getppid() and terminate is False
        return SimpleNamespace(identity=identity(pid), alive=lambda: True, wait=lambda ms: False,
            close=lambda: setattr(self, "closed", True))
    def state(self, value):
        return self.prior_state


def prior(root, *, state="active", native=False):
    directory = root / "sessions"
    directory.mkdir(exist_ok=True)
    path = directory / "1.json"
    launch.atomic(path, launch.encoded({"profile": "standalone-offline", "state": state,
        "native_intents": native, "app_created_children": [], "identity": identity(123)}))
    return path


@pytest.mark.parametrize("state", ["absent", "different"])
def test_dead_generation_reconciles_only_without_native_intents_or_children(tmp_path, state):
    path = prior(tmp_path)
    driver = Driver(state)
    generation = launch.OfflineGeneration(tmp_path, {"snapshot_id": "2" * 64}, driver)
    assert launch.parse(path.read_bytes())["state"] == "reconciled-dead"
    # Physical kernel identity, not sys.executable or a logical WindowsApps alias.
    assert generation.record["identity"]["executable"] == "c:\\physical-store\\pythonw.exe"
    generation.close()
    assert driver.closed and launch.parse(generation.path.read_bytes())["state"] == "closed"


@pytest.mark.parametrize("state,native", [("unknown", False), ("absent", True)])
def test_uncertain_prior_owner_never_reconciles_or_replays(tmp_path, state, native):
    path = prior(tmp_path, native=native)
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        launch.OfflineGeneration(tmp_path, {"snapshot_id": "2" * 64}, Driver(state))
    assert path.read_bytes() == before


def test_live_peer_preserved_across_admission_and_limit(tmp_path, monkeypatch):
    path = prior(tmp_path)
    before = path.read_bytes()
    generation = launch.OfflineGeneration(tmp_path, {"snapshot_id": "2" * 64}, Driver("alive"))
    assert path.read_bytes() == before
    generation.close()
    monkeypatch.setattr(launch, "MAX_SESSIONS", 1)
    with pytest.raises(RuntimeError, match="capacity reached"):
        launch.OfflineGeneration(tmp_path, {}, Driver("alive"))
    assert path.read_bytes() == before


def test_selected_pywin32_roots_are_explicit_without_executing_pth(tmp_path, monkeypatch):
    packages = tmp_path / "site-packages"
    for name in ("win32/lib", "pywin32_system32"):
        (packages / name).mkdir(parents=True)
    (packages / "unexpected.pth").write_text("import never_execute_this_installer_hook\n")
    paths = list(sys.path)
    monkeypatch.setattr(sys, "path", paths)
    handles = []
    monkeypatch.setattr(launch.os, "add_dll_directory", lambda path: handles.append(path), raising=False)
    launch.configure_packages(tmp_path)
    assert paths[0] == str(packages)
    assert str(packages / "win32/lib") in paths
    assert handles == [str(packages / "pywin32_system32")]
    assert "never_execute_this_installer_hook" not in sys.modules
