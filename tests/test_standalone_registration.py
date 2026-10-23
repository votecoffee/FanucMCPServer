import json
from types import SimpleNamespace
import sys

import pytest

from test_plugin_upgrade import load_setup


@pytest.mark.parametrize("selected", [None, "plugin", "none"])
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
    monkeypatch.setattr(setup, "register_plugin", lambda *args: calls.append("plugin"))
    from fanuc_workbench.services import stable_install
    def install(*args, **kwargs):
        calls.append("runtime")
        assert kwargs["expected_selection"] == "absent"
        return {"connection":{"command":str(runtime / "python/pythonw.exe"),
            "args":["-I","-B",str(runtime / "StartMcp.py")],"cwd":str(runtime)}}
    monkeypatch.setattr(stable_install, "install", install)
    argv = ["setup.py", "--runtime-dir", str(runtime), "--config", str(config),
        "--expected-selection-sha256", "absent", "--management-registry", str(tmp_path / "registry"),
        "--installed-plugin-version", "26.1022.180303"]
    if selected is not None: argv += ["--register", selected]
    monkeypatch.setattr(sys, "argv", argv)
    setup.main()
    route = selected or "plugin"
    assert calls == (["runtime"] if route == "none" else ["runtime", route])
    server = json.loads((root / "plugin/mcp.json").read_bytes())["mcpServers"]["fanuc-mcp-server"]
    assert server["command"] == str(runtime / "python/pythonw.exe")
    assert server["cwd"] == str(runtime) and str(root) not in json.dumps(server)


@pytest.mark.parametrize("removed", [["--register", "broker"], ["--register", "auto"],
                                    ["--register", "mcp"], ["--broker-runtime", "runtime.json"], ["--broker-allow-writes"]])
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
    monkeypatch.setattr(sys, "argv", ["setup.py", "--runtime-dir", str(runtime), "--config", str(config),
        "--expected-selection-sha256", "absent", "--management-registry", str(tmp_path / "registry")])
    with pytest.raises(ValueError, match="Legacy runtime retained"): setup.main()
    assert marker.read_bytes() == before
