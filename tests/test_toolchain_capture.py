"""Metadata-only toolchain CLI; synthetic bytes and no native execution."""
import hashlib
import json
import subprocess

import pytest

from fanuc_workbench.program_tools import main
from test_native_validation_offline import setup_tools


def test_capture_cli_pins_all_inputs_without_execution_or_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Metadata capture cannot execute a utility"))
    options = setup_tools(tmp_path); destination = tmp_path / "toolchain.json"
    arguments = ["toolchain", "--destination", str(destination)]
    for key, value in options.items():
        if key == "components":
            for component in value: arguments.extend(["--component", str(component)])
        else: arguments.extend(["--" + key.replace("_", "-"), str(value)])
    result = main(arguments)
    original = destination.read_bytes(); document = json.loads(original)
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert not result["compiler_invoked"] and not result["controller_contacted"]
    assert {row["role"] for row in document["files"]} == {"maketp", "printtp", "configuration", "component"}
    assert not document["dependency_completeness_verified"]
    assert document["context_manifest"][0]["path"] == "support/config.dat"
    with pytest.raises(FileExistsError): main(arguments)
    assert destination.read_bytes() == original


def test_explicit_relative_profile_resolves_beside_configuration(tmp_path):
    from fanuc_workbench.services.local_native import capture_toolchain
    options = setup_tools(tmp_path)
    config = options["configuration"]
    config.write_text(config.read_text().replace("Path=profile", "Path=."))
    for name in ("asc2bint.dll", "bin2asct.dll"):
        (tmp_path / name).write_bytes((tmp_path / "profile" / name).read_bytes())
    manifest = capture_toolchain(**options)
    assert manifest["options"]["components"] == [str(value) for value in options["components"]]


def test_component_names_are_explicit_and_not_inferred_from_vendor_layout(tmp_path):
    from fanuc_workbench.services.local_native import capture_toolchain
    options = setup_tools(tmp_path)
    selected = tmp_path / "selected-support-vendor.dll"; selected.write_bytes(b"inert alternative layout")
    for value in options["components"]: value.unlink()
    options["components"] = [selected]
    manifest = capture_toolchain(**options)
    assert manifest["options"]["components"] == [str(selected)]
    assert [row["path"] for row in manifest["files"] if row["role"] == "component"] == [str(selected)]
    assert not manifest["dependency_completeness_verified"]


@pytest.mark.parametrize("selection", [None, [], "guessed.dll"])
def test_missing_explicit_component_selection_refuses_without_execution(tmp_path, selection):
    from fanuc_workbench.services.local_native import capture_toolchain
    options = setup_tools(tmp_path); options["components"] = selection
    with pytest.raises(ValueError, match="explicit supporting"):
        capture_toolchain(**options)


@pytest.mark.parametrize("target", ["configuration", "maketp", "duplicate"])
def test_component_pins_cannot_alias_core_pins_or_each_other(tmp_path, target):
    from fanuc_workbench.services.local_native import capture_toolchain
    options = setup_tools(tmp_path)
    options["components"] = ([options["components"][0]] * 2 if target == "duplicate" else [options[target]])
    with pytest.raises(ValueError, match="distinct|Duplicate"):
        capture_toolchain(**options)
