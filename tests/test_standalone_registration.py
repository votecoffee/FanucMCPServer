import json
from types import SimpleNamespace
import sys

import pytest

from test_plugin_upgrade import load_setup


@pytest.mark.parametrize("selected", [None, "plugin", "mcp", "none"])
def test_installer_registers_only_standalone_routes(tmp_path, monkeypatch, selected):
    setup = load_setup()
    root = tmp_path / "bundle"
    wheels = root / "plugin/wheels"; wheels.mkdir(parents=True)
    (wheels / "fanuc_mcp_server_bridge-0.11.0.dev0-py3-none-any.whl").write_bytes(b"fixture")
    config = tmp_path / "policy.json"; config.write_text("{}")
    runtime = tmp_path / "runtime"
    setup.__file__ = str(root / "setup.py")
    monkeypatch.setattr(setup, "verify_bundle", lambda _: None)
    monkeypatch.setattr(setup.shutil, "which", lambda _: "codex")
    calls = []
    monkeypatch.setattr(setup.venv, "EnvBuilder", lambda **_: SimpleNamespace(create=lambda _: calls.append("runtime")))
    monkeypatch.setattr(setup, "register_plugin", lambda *args: calls.append("plugin"))
    def run(command, **kwargs):
        assert "universal_tools" not in command and command[1:3] != ["plugin", "list"]
        if command[:3] == ["codex", "mcp", "add"]: calls.append("mcp")
        return SimpleNamespace(stdout=json.dumps({"tool_count": 48, "configured_robots": [],
            "app_version": setup.versions.DISPLAY_VERSION, "package_version": setup.versions.PACKAGE_VERSION}))
    monkeypatch.setattr(setup.subprocess, "run", run)
    argv = ["setup.py", "--runtime-dir", str(runtime), "--config", str(config)]
    if selected is not None: argv += ["--register", selected]
    monkeypatch.setattr(sys, "argv", argv)
    setup.main()
    route = selected or "plugin"
    assert calls == (["runtime"] if route == "none" else ["runtime", route])
    marker = json.loads((runtime / "installation.json").read_bytes())
    assert marker.get("registration") == (None if route == "none" else route)
    assert marker["config"] == str(config.resolve())
    assert marker["release"] == setup.versions.release_metadata()


@pytest.mark.parametrize("removed", [["--register", "broker"], ["--register", "auto"],
                                    ["--broker-runtime", "runtime.json"], ["--broker-allow-writes"]])
def test_removed_installer_options_fail_before_runtime_changes(tmp_path, monkeypatch, removed):
    setup = load_setup()
    monkeypatch.setattr(setup, "verify_bundle", lambda _: pytest.fail("Removed route reached bundle/runtime work"))
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(sys, "argv", ["setup.py", "--runtime-dir", str(runtime), *removed])
    with pytest.raises(SystemExit) as error: setup.main()
    assert error.value.code == 2 and not runtime.exists()


def test_legacy_broker_runtime_is_preserved_for_reconciliation(tmp_path, monkeypatch):
    setup = load_setup()
    runtime = tmp_path / "runtime"; runtime.mkdir()
    config = tmp_path / "policy.json"; config.write_text("{}")
    marker = runtime / "installation.json"
    marker.write_text(json.dumps({"kind": "fanuc-mcp-server-runtime", "config": str(config), "registration": "broker"}))
    before = marker.read_bytes()
    monkeypatch.setattr(setup, "verify_bundle", lambda _: None)
    monkeypatch.setattr(setup.venv, "EnvBuilder", lambda **_: pytest.fail("Old retained runtime changed"))
    monkeypatch.setattr(sys, "argv", ["setup.py", "--runtime-dir", str(runtime)])
    with pytest.raises(ValueError, match="broker route has been removed"): setup.main()
    assert marker.read_bytes() == before
