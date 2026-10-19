import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_plugin_upgrade import load_setup


@pytest.mark.parametrize("prior,plugins,ready,expected", [
    ({}, [], False, "plugin"), ({}, [], True, "broker"),
    ({"registration": "plugin"}, [], True, "plugin"),
    ({"registration": "broker"}, [], False, "broker"),
    ({"registration": "mcp"}, [], True, "mcp"),
    ({}, [{"name": "fanuc-workbench", "installed": True}], True, "none"),
])
def test_auto_preserves_routes(prior, plugins, ready, expected):
    assert load_setup().select_registration("auto", prior, plugins, ready) == expected
    assert load_setup().select_registration("plugin", prior, plugins, ready) == "plugin"


@pytest.mark.parametrize("enabled,version,disabled,works", [
    (True, "0.11.1", False, True), (False, "0.11.1", False, False),
    (True, "0.7.0", False, False), (True, "1.0.0", False, False),
    (True, "0.11.1", True, False),
])
def test_broker_detection_uses_enabled_profile_and_runtime(tmp_path, monkeypatch, enabled, version, disabled, works):
    setup = load_setup()
    source, state = tmp_path / "plugin", tmp_path / "state"
    source.mkdir(); state.mkdir()
    (state / "settings.json").write_text("{}")
    python = tmp_path / "python.exe"; python.write_bytes(b"fixture")
    (source / "runtime.json").write_text(json.dumps({"python": str(python), "state": str(state)}))
    if disabled:
        (tmp_path / "config.toml").write_text('[plugins."universal-tools".mcp_servers.universal-tools]\nenabled=false\n')
    row = {"name": "universal-tools", "version": version, "installed": True, "enabled": enabled,
           "source": {"source": "local", "path": str(source)}}
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=version)
    monkeypatch.setattr(setup.subprocess, "run", run)
    if works:
        assert setup.find_broker([row], {"CODEX_HOME": str(tmp_path)})["state"] == str(state)
    else:
        with pytest.raises(ValueError): setup.find_broker([row], {"CODEX_HOME": str(tmp_path)})
    assert all(command[-1] == "--version" for command in calls)


def test_broker_skill_upgrade_preserves_edits(tmp_path):
    setup = load_setup()
    source = tmp_path / "bundle/plugin/skills/fanuc-robot"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("first version")
    profile = tmp_path / "profile"
    saved = setup.install_broker_skill(tmp_path / "bundle", profile, {})
    (source / "SKILL.md").write_text("second version")
    saved = setup.install_broker_skill(tmp_path / "bundle", profile, {"broker_skill": saved})
    target = Path(saved["path"]) / "SKILL.md"
    assert target.read_text() == "second version"
    target.write_text("local edits")
    with pytest.raises(ValueError, match="unowned or edited"):
        setup.install_broker_skill(tmp_path / "bundle", profile, {"broker_skill": saved})
    assert target.read_text() == "local edits"


@pytest.mark.parametrize("blocked", ["different", "disabled", "recovery"])
def test_existing_registration_and_recovery_are_not_overwritten(tmp_path, monkeypatch, blocked):
    setup = load_setup()
    state = tmp_path / "state"; (state / "services").mkdir(parents=True)
    runtime = tmp_path / "runtime"; runtime.mkdir()
    service = state / "services/fanuc-mcp-server.json"
    current = {"id": "fanuc-mcp-server", "enabled": blocked != "disabled", "allow_writes": True}
    service.write_text(json.dumps(current))
    before = service.read_bytes()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "contexts":
            return SimpleNamespace(stdout=json.dumps({"contexts": [{"service_id": "fanuc-mcp-server", "state": "lost"}] if blocked == "recovery" else []}))
        if "--manifest" in command:
            (runtime / "broker-service.json").write_text(json.dumps({"id": "fanuc-mcp-server", "allow_writes": False}))
        return SimpleNamespace()
    monkeypatch.setattr(setup.subprocess, "run", run)
    with pytest.raises(ValueError):
        setup.register_broker(tmp_path, runtime, tmp_path / "python", tmp_path / "config", {"python": "broker", "state": str(state)}, {}, {}, False)
    assert service.read_bytes() == before and not any("register" in command for command in calls)


@pytest.mark.parametrize("available,prior,expected", [
    (True, None, "broker"), (False, None, "plugin"),
    (True, "plugin", "plugin"), (False, "broker", "error"),
])
def test_setup_auto_records_route_and_never_fails_over(tmp_path, monkeypatch, available, prior, expected):
    import sys
    setup = load_setup()
    root = tmp_path / "bundle"
    wheels = root / "plugin/wheels"; wheels.mkdir(parents=True)
    (wheels / "fanuc_mcp_server_bridge-0.9.0.dev0-py3-none-any.whl").write_bytes(b"fixture")
    runtime = tmp_path / "runtime"
    config = tmp_path / "policy.json"; config.write_text("{}")
    if prior:
        runtime.mkdir()
        (runtime / "installation.json").write_text(json.dumps({"kind": "fanuc-mcp-server-runtime",
            "config": str(config), "registration": prior}))
    setup.__file__ = str(root / "setup.py")
    monkeypatch.setattr(setup, "verify_bundle", lambda _: None)
    monkeypatch.setattr(setup.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(setup, "installed_plugins", lambda *args: [])
    def find(*args):
        if not available: raise ValueError("fixture unavailable")
        return {"python": "broker-python", "state": str(tmp_path / "broker"), "version": "0.11.1"}
    monkeypatch.setattr(setup, "find_broker", find)
    calls = []
    monkeypatch.setattr(setup.venv, "EnvBuilder", lambda **_: SimpleNamespace(create=lambda _: calls.append("runtime")))
    monkeypatch.setattr(setup, "require_idle_broker", lambda *args: calls.append("idle"))
    monkeypatch.setattr(setup, "register_plugin", lambda *args: calls.append("plugin"))
    monkeypatch.setattr(setup, "register_broker", lambda *args: calls.append("broker") or {})
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({"tool_count": 48, "configured_robots": []})))
    profile = tmp_path / "new-codex-profile"
    monkeypatch.setattr(sys, "argv", ["setup.py", "--runtime-dir", str(runtime), "--config", str(config),
                                     "--register", "auto", "--codex-home", str(profile)])
    if expected == "error":
        before = (runtime / "installation.json").read_bytes()
        with pytest.raises(ValueError, match="prior integration preserved"): setup.main()
        assert calls == [] and (runtime / "installation.json").read_bytes() == before
    else:
        setup.main()
        assert profile.is_dir()
        assert calls[-1] == expected
        assert json.loads((runtime / "installation.json").read_text())["registration"] == expected
        if expected == "broker": assert calls.index("idle") < calls.index("runtime")


def test_active_broker_blocks_runtime_only_upgrade_before_file_changes(tmp_path, monkeypatch):
    import sys
    setup = load_setup()
    runtime = tmp_path / "runtime"; runtime.mkdir()
    config = tmp_path / "policy.json"; config.write_text("{}")
    marker = runtime / "installation.json"
    marker.write_text(json.dumps({"kind": "fanuc-mcp-server-runtime", "config": str(config),
        "registration": "broker", "broker": {"python": "fixture", "state": "fixture"}}))
    before = marker.read_bytes()
    monkeypatch.setattr(setup, "verify_bundle", lambda _: None)
    def blocked(*args): raise ValueError("active fixture work")
    monkeypatch.setattr(setup, "require_idle_broker", blocked)
    monkeypatch.setattr(setup.venv, "EnvBuilder", lambda **_: pytest.fail("Runtime changed before preflight"))
    monkeypatch.setattr(sys, "argv", ["setup.py", "--runtime-dir", str(runtime)])
    with pytest.raises(ValueError, match="active fixture"): setup.main()
    assert marker.read_bytes() == before
