"""ROBOGUIDE handoff preserves user software/files; no launch is attempted."""
import subprocess

import pytest

from fanuc_workbench.services.roboguide import launch_roboguide, stage_backup_for_roboguide


def test_unverified_roboguide_launch_is_refused_without_starting_software(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No software should launch"))
    with pytest.raises(RuntimeError, match="ownership and dirty-workcell recovery"):
        launch_roboguide()


def test_existing_staged_backup_is_never_deleted_or_overwritten(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "MAIN.LS").write_bytes(b"synthetic source")
    destination = tmp_path / "handoff"
    destination.mkdir()
    staged = stage_backup_for_roboguide(source, destination)
    extra = staged / "retained-work.txt"
    extra.write_bytes(b"preserve existing edits")
    with pytest.raises(FileExistsError):
        stage_backup_for_roboguide(source, destination)
    assert extra.read_bytes() == b"preserve existing edits"
    assert (staged / "MAIN.LS").read_bytes() == (source / "MAIN.LS").read_bytes()
