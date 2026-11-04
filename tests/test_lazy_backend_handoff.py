"""A catalog-bound backend keeps one connection and its original parent."""
import os

import pytest

from fanuc_workbench.bridge import stable_launcher as launch
from test_stable_launcher import Driver, fixture, identity


def test_pinned_selection_survives_later_selection_and_entry_publication(tmp_path):
    first, old = fixture(tmp_path, "26.1023.100001")
    old_id = launch.publish(tmp_path, first, expected_selection="absent")
    legacy = tmp_path / "retained-entry.py"
    legacy.write_bytes((tmp_path / "StartMcp.py").read_bytes())
    previous = launch.digest((tmp_path / "launcher/current.json").read_bytes())
    second, _ = fixture(tmp_path, "26.1023.100002")
    launch.publish(tmp_path, second, expected_selection=previous)
    (tmp_path / "StartMcp.py").write_bytes(b"later reviewed bootstrap")
    selected, manifest, runtime = launch.selected(tmp_path, pinned_snapshot=old_id, launcher_path=legacy)
    assert selected["snapshot_id"] == old_id and manifest == first and runtime == old
    (old / "payload.txt").write_bytes(b"drift still refuses")
    with pytest.raises(ValueError, match="mismatch"):
        launch.selected(tmp_path, pinned_snapshot=old_id, launcher_path=legacy)


def handoff(root):
    (root / "sessions").mkdir()
    path = root / "sessions" / ("a" * 32 + ".json")
    selection = {"schema_version": 1, "snapshot_id": "2" * 64}
    value = {"schema_version": 1, "profile": "standalone-offline", "state": "activating",
        "identity": identity(os.getppid()), "parent": identity(678), "snapshot": selection,
        "native_intents": False, "lazy_frontend": True, "handoff_nonce": "3" * 64,
        "handoff_backend": identity(os.getpid()), "app_created_children": [identity(os.getpid())],
        "containment": {"actor_limit": 2, "commit_bytes": launch.FRONTDOOR_BYTES}}
    launch.atomic(path, launch.encoded(value))
    return path, selection, value


def test_authenticated_handoff_credits_only_its_original_frontend_slot(tmp_path, monkeypatch):
    path, selection, value = handoff(tmp_path)
    monkeypatch.setattr(launch, "MAX_SESSIONS", 1)
    driver = Driver("alive")
    owner = launch.OfflineGeneration(tmp_path, selection, driver, handoff=path, handoff_nonce="3" * 64)
    try:
        assert owner.path == path
        assert len(list((tmp_path / "sessions").glob("*.json"))) == 1
        assert owner.record["frontend_identity"] == value["identity"]
        assert owner.record["parent"] == value["identity"]
        assert owner.record["containment"]["commit_bytes"] == launch.FRONTDOOR_BYTES
        assert owner.record["containment"]["actor_limit"] == 2
    finally:
        owner.close()
    assert driver.closed


@pytest.mark.parametrize("key,replacement", [
    ("identity", identity(999)), ("snapshot", {}), ("handoff_nonce", "4" * 64),
    ("native_intents", True), ("state", "closed"), ("app_created_children", []),
    ("lazy_frontend", False),
])
def test_wrong_handoff_retains_original_receipt_and_closes_parent_query(tmp_path, key, replacement):
    path, selection, value = handoff(tmp_path)
    value[key] = replacement
    original = launch.encoded(value)
    path.write_bytes(original)
    driver = Driver("alive")
    with pytest.raises(ValueError, match="differ"):
        launch.OfflineGeneration(tmp_path, selection, driver, handoff=path, handoff_nonce="3" * 64)
    assert driver.closed and path.read_bytes() == original


def test_private_entry_binds_snapshot_and_handoff_without_prebootstrap_imports(monkeypatch):
    seen = []
    monkeypatch.setattr(launch, "main", lambda *args, **kwargs: seen.append((args, kwargs)))
    launch.entrypoint(["--pinned-root", "root", "--pinned-snapshot", "a" * 64,
        "--legacy-entry", "legacy.py", "--lazy-handoff", "receipt.json", "--handoff-nonce", "b" * 64])
    assert seen[0][1]["pinned_snapshot"] == "a" * 64
    assert seen[0][1]["handoff_nonce"] == "b" * 64
    with pytest.raises(ValueError, match="arguments"):
        launch.entrypoint(["--pinned-root", "root"])
