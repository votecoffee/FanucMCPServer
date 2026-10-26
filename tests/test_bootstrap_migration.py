"""Reviewed metadata-only entry migration preserves live/retained generations."""
import pytest
from fanuc_workbench.bridge import stable_launcher as launch
from test_stable_launcher import fixture


def test_bootstrap_migration_requires_exact_prior_bytes_and_retains_backup(tmp_path):
    original, _ = fixture(tmp_path, "26.1023.100001")
    launch.publish(tmp_path, original, expected_selection="absent")
    pointer = (tmp_path / "launcher/current.json").read_bytes()
    entry = (tmp_path / "StartMcp.py").read_bytes()
    candidate, _ = fixture(tmp_path, "26.1023.100002")
    new_entry = entry + b"\n# Reviewed synthetic bootstrap revision\n"
    candidate["launcher_sha256"] = launch.digest(new_entry)
    with pytest.raises(ValueError, match="hashes differ"):
        launch.publish(tmp_path, candidate, expected_selection=launch.digest(pointer),
            bootstrap_bytes=new_entry, expected_bootstrap="0" * 64)
    assert (tmp_path / "StartMcp.py").read_bytes() == entry
    launch.publish(tmp_path, candidate, expected_selection=launch.digest(pointer),
        bootstrap_bytes=new_entry, expected_bootstrap=launch.digest(entry))
    assert (tmp_path / "launcher/bootstrap-backups" / (launch.digest(entry) + ".py")).read_bytes() == entry
    assert (tmp_path / "StartMcp.py").read_bytes() == new_entry
    assert launch.parse((tmp_path / "launcher/bootstrap-migration.json").read_bytes())["state"] == "committed"
    launch.selected(tmp_path)


def test_interrupted_entry_selection_update_refuses_and_explicitly_rolls_forward(tmp_path, monkeypatch):
    original, _ = fixture(tmp_path, "26.1023.100001")
    launch.publish(tmp_path, original, expected_selection="absent")
    pointer = (tmp_path / "launcher/current.json").read_bytes()
    entry = (tmp_path / "StartMcp.py").read_bytes()
    candidate, _ = fixture(tmp_path, "26.1023.100002")
    new_entry = entry + b"\n# Reviewed synthetic revision\n"
    candidate["launcher_sha256"] = launch.digest(new_entry)
    replace = launch.os.replace
    def interrupted(source, target):
        if str(target).endswith("current.json"):
            raise OSError("Synthetic interruption before selection")
        return replace(source, target)
    monkeypatch.setattr(launch.os, "replace", interrupted)
    with pytest.raises(OSError, match="interruption"):
        launch.publish(tmp_path, candidate, expected_selection=launch.digest(pointer),
            bootstrap_bytes=new_entry, expected_bootstrap=launch.digest(entry))
    assert (tmp_path / "launcher/current.json").read_bytes() == pointer
    assert launch.parse((tmp_path / "launcher/bootstrap-migration.json").read_bytes())["state"] == "committing"
    monkeypatch.setattr(launch.os, "replace", replace)
    launch.publish(tmp_path, candidate, expected_selection=launch.digest(pointer),
        bootstrap_bytes=new_entry, expected_bootstrap=launch.digest(new_entry))
    launch.selected(tmp_path)
