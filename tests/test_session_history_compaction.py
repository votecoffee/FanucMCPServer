from contextlib import contextmanager
from pathlib import Path
import runpy

from fanuc_workbench.bridge import stable_launcher as launch


compact = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/compact_offline_sessions.py"))["compact"]


def test_compaction_inspects_outside_lock_and_preserves_live_unknown_and_native(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    originals = {}
    for index in range(20):
        raw = launch.encoded({"state": "closed", "profile": "standalone-offline", "native_intents": index == 3,
            "app_created_children": [], "identity": {"pid": index}})
        (directory / f"{index}.json").write_bytes(raw)
        originals[str(index)] = raw
    held = False
    batches = []
    @contextmanager
    def lock(path):
        nonlocal held
        assert not held
        held = True
        try:
            yield
        finally:
            held = False
            batches.append(len(list((directory / "history").glob("*.json"))))
    class Driver:
        def state(self, identity):
            assert not held
            return {1: "alive", 2: "unknown"}.get(identity["pid"], "absent")
    result = compact(tmp_path, {**vars(launch), "locked": lock, "process_creation_times": lambda: None}, Driver())
    assert len(result["moved"]) == 17
    assert max(b - a for a, b in zip(batches, batches[1:])) <= 8
    for name in ("1", "2", "3"):
        assert (directory / (name + ".json")).read_bytes() == originals[name]
    for row in result["moved"]:
        assert (directory / "history" / row["retained"]).read_bytes() == originals[Path(row["original"]).stem]


def test_compaction_rechecks_exact_receipt_after_process_inspection(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    path = directory / "receipt.json"
    raw = launch.encoded({"state": "closed", "profile": "standalone-offline", "native_intents": False,
        "app_created_children": [], "identity": {"pid": 123}})
    path.write_bytes(raw)
    changed = raw.replace(b'"closed"', b'"active"')
    class Driver:
        def state(self, identity):
            path.write_bytes(changed)
            return "absent"
    result = compact(tmp_path, vars(launch), Driver())
    assert not result["moved"] and result["changed_retained"] == [path.name]
    assert path.read_bytes() == changed
