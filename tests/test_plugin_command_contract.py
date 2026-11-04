"""Modern plugin launch commands keep the interpreter outside disposable caches."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from test_plugin_upgrade import load_setup


def test_plugin_uses_bare_command_with_exact_stable_python_search_path(tmp_path):
    setup = load_setup()
    root = tmp_path / "Stable Runtime With Spaces"
    server = setup.plugin_connection(root / "python/pythonw.exe", None, root)["mcpServers"]["fanuc-mcp-server"]
    assert server["command"] == "pythonw.exe"
    assert server["env"]["PATH"] == str(root / "python")
    assert server["args"] == ["-I", "-B", str(root / "StartMcp.py")]
    assert server["cwd"] == "${PLUGIN_DATA}"


def test_plugin_only_reuses_exact_selection_without_installing_runtime(tmp_path, monkeypatch):
    setup = load_setup()
    from fanuc_workbench.bridge import stable_launcher as launcher
    from fanuc_workbench.services import stable_install
    from test_stable_launcher import fixture
    root = tmp_path / "Stable Runtime"
    manifest, runtime = fixture(root)
    policy = runtime / "policy.json"
    policy.write_text(json.dumps({"robots": {}, "allow_writes": False}))
    manifest["runtime_files"] = launcher.inventory(runtime)
    (root / "application.json").write_bytes(launcher.encoded({"schema_version": 1, "app": "fanuc-mcp-server"}))
    launcher.publish(root, manifest, expected_selection="absent")
    selection = root / "launcher/current.json"
    previous = selection.read_bytes()
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(stable_install, "install", lambda *a, **k: pytest.fail("Plugin-only publication cannot install a runtime"))
    result = setup.existing_connection(root, hashlib.sha256(previous).hexdigest(), manifest["launcher_sha256"])
    assert result["runtime_unchanged"] and result["connection"]["command"] == str(root / "python/pythonw.exe")
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="selection changed"):
        setup.existing_connection(root, "f" * 64, manifest["launcher_sha256"])


@pytest.mark.parametrize("relative", [True, False])
def test_unbound_or_path_separator_roots_are_refused(tmp_path, relative):
    setup = load_setup()
    root = Path("relative") if relative else tmp_path / "split;path"
    with pytest.raises(ValueError):
        setup.plugin_connection(root / "python/pythonw.exe", None, root)


def test_guarded_registration_rejects_stale_or_duplicate_route_before_cli(tmp_path, monkeypatch):
    setup = load_setup()
    home = tmp_path / "profile"
    home.mkdir()
    path = home / "config.toml"
    path.write_bytes(b'[mcp_servers.fanuc-mcp-server]\nenabled=true\n')
    monkeypatch.setattr(setup, "_register_plugin", lambda *a: pytest.fail("Unsafe preimage reached Codex CLI"))
    environment = {"CODEX_HOME": str(home)}
    with pytest.raises(ValueError, match="changed"):
        setup.register_plugin("inert", tmp_path, environment, "f" * 64)
    with pytest.raises(ValueError, match="disabled bundled"):
        setup.register_plugin("inert", tmp_path, environment, hashlib.sha256(path.read_bytes()).hexdigest())


def test_guarded_registration_preserves_disabled_bundled_route(tmp_path, monkeypatch):
    setup = load_setup()
    home = tmp_path / "profile"
    home.mkdir()
    path = home / "config.toml"
    raw = (b'# retained\r\n[mcp_servers.fanuc-mcp-server]\r\nenabled=true\r\n'
           b'[plugins."fanuc-mcp-server@fanuc-mcp-server-local"]\r\nenabled=true\r\n'
           b'[plugins."fanuc-mcp-server@fanuc-mcp-server-local".mcp_servers.fanuc-mcp-server]\r\nenabled=false\r\n')
    path.write_bytes(raw)
    monkeypatch.setattr(setup, "_register_plugin", lambda *a: {"version": "test"})
    assert setup.register_plugin("inert", tmp_path, {"CODEX_HOME": str(home)}, hashlib.sha256(raw).hexdigest()) == {"version": "test"}
    assert path.read_bytes() == raw


def test_cli_owned_marketplace_row_and_eol_normalization_preserve_other_settings(tmp_path, monkeypatch):
    setup = load_setup()
    home = tmp_path / "profile"
    home.mkdir()
    source = tmp_path / "marketplace"
    source.mkdir()
    config = home / "config.toml"
    raw = (b'# keep comment\r\n[mcp_servers.fanuc-mcp-server]\r\nenabled=true\r\n'
           b'[plugins."fanuc-mcp-server@fanuc-mcp-server-local"]\r\nenabled=true\r\n'
           b'[plugins."fanuc-mcp-server@fanuc-mcp-server-local".mcp_servers.fanuc-mcp-server]\r\nenabled=false\r\n')
    config.write_bytes(raw)
    def cli(*args):
        config.write_bytes(raw.replace(b'\r\n', b'\n') + (
            '\n[marketplaces.fanuc-mcp-server-local]\nsource_type="local"\nsource=' + json.dumps(str(source)) + '\n').encode())
        return {"version": "test"}
    monkeypatch.setattr(setup, "_register_plugin", cli)
    setup.register_plugin("inert", source, {"CODEX_HOME": str(home)}, hashlib.sha256(raw).hexdigest())
    updated = config.read_bytes()
    assert updated.startswith(raw) and b"\n" not in updated.replace(b"\r\n", b"")


def test_guarded_owned_source_migration_keeps_policy_comments_and_other_settings(tmp_path, monkeypatch):
    import tomllib
    from test_plugin_upgrade import bundle
    setup = load_setup()
    old, new = tmp_path / "old", tmp_path / "new"
    bundle(old, "1.0.0")
    bundle(new, "2.0.0")
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.toml"
    raw = ('# retained\r\n[marketplaces.fanuc-mcp-server-local]\r\nsource_type="local"\r\n'
           'source = ' + json.dumps(str(old)) + ' # source note\r\n'
           '[plugins."fanuc-mcp-server@fanuc-mcp-server-local"]\r\nenabled=true\r\n'
           '[plugins."fanuc-mcp-server@fanuc-mcp-server-local".mcp_servers.fanuc-mcp-server]\r\nenabled=false\r\n').encode()
    config.write_bytes(raw)
    expected = raw.replace(json.dumps(str(old)).encode(), json.dumps(str(new)).encode())
    def cli(*args):
        assert config.read_bytes() == expected
        return {"version": "2.0.0"}
    monkeypatch.setattr(setup, "_register_plugin", cli)
    setup.register_plugin("inert", new, {"CODEX_HOME": str(home)}, hashlib.sha256(raw).hexdigest())
    assert config.read_bytes() == expected
    assert tomllib.loads(config.read_text())["plugins"]["fanuc-mcp-server@fanuc-mcp-server-local"]["mcp_servers"]["fanuc-mcp-server"]["enabled"] is False
    assert old.is_dir()


def test_guarded_source_migration_refuses_foreign_binding_without_mutation(tmp_path, monkeypatch):
    setup = load_setup()
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.toml"
    raw = ('[marketplaces.fanuc-mcp-server-local]\nsource_type="local"\nsource=' + json.dumps(str(tmp_path / "foreign")) + '\n').encode()
    config.write_bytes(raw)
    monkeypatch.setattr(setup, "_register_plugin", lambda *a: pytest.fail("Foreign binding reached CLI"))
    with pytest.raises(ValueError, match="unrecognized source"):
        setup.register_plugin("inert", tmp_path / "new", {"CODEX_HOME": str(home)}, hashlib.sha256(raw).hexdigest())
    assert config.read_bytes() == raw
