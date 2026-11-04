"""One disposable installer selection control; no executable or settings writes."""
import json
import os
from pathlib import Path
import tempfile

import pytest

from fanuc_workbench.bridge import stable_launcher as launcher
from fanuc_workbench.services import frontend_selection, stable_install
from test_plugin_upgrade import load_setup
from test_stable_launcher import fixture


@pytest.mark.skipif(os.name != "nt", reason="Canonical Windows installer and executable-name casing")
def test_install_and_bundle_repair_keep_verified_native_selection(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="fanuc-native-install-") as temporary:
        base = Path(temporary)
        root = base / "App" / "MCP"
        root.parent.mkdir()
        manifest, runtime = fixture(root)
        policy = {"robots": {}, "allow_writes": False, "state_dir": str(base / "evidence"), "source_dir": str(base / "inputs")}
        (runtime / "policy.json").write_bytes(launcher.encoded(policy))
        manifest["runtime_files"] = launcher.inventory(runtime)
        manifest["release"] = {"display_version": "26.1023.165651"}
        (root / "application.json").write_bytes(launcher.encoded({"schema_version": 1, "app": "fanuc-mcp-server"}))
        snapshot = launcher.publish(root, manifest, expected_selection="absent")
        current = launcher.digest((root / "launcher/current.json").read_bytes())
        frontend = root.parent / "Frontends" / "retained"
        frontend.mkdir(parents=True)
        (frontend / "FanucMcpFrontend.exe").write_bytes(b"inert synthetic frontend; never executed")
        (frontend / "FanucMcpFrontend.exe.config").write_bytes(b"inert config")
        (frontend / "Backend.py").write_bytes(b"# inert backend\n")
        (frontend / "LegacyEntry.py").write_bytes((root / "StartMcp.py").read_bytes())
        (frontend / "catalog.json").write_bytes(launcher.encoded({"snapshot_id": snapshot}))
        native = {"schema_version": 1, "app": "fanuc-mcp-server", "profile": "standalone-offline",
            "backend_root": str(root), "runtime": manifest["runtime"], "snapshot_id": snapshot,
            "application_sha256": launcher.digest((root / "application.json").read_bytes()), "files": launcher.inventory(frontend)}
        native_path = frontend / "frontend.json"
        native_path.write_bytes(launcher.encoded(native))
        command = {"command": str(frontend / "FanucMcpFrontend.exe"), "args": ["--manifest", str(native_path), "--sha256", launcher.digest(native_path.read_bytes())],
            "cwd": str(root), "env": {"PYTHONUTF8": "1"}}
        home = base / "profile"
        home.mkdir()
        text = '[mcp_servers.fanuc-mcp-server]\nenabled=true\n' + ''.join(key+'='+json.dumps(command[key])+'\n' for key in ("command", "args", "cwd"))
        text += '[mcp_servers.fanuc-mcp-server.env]\nPYTHONUTF8="1"\n[plugins."fanuc-mcp-server@fanuc-mcp-server-local".mcp_servers.fanuc-mcp-server]\nenabled=false\n'
        (home / "config.toml").write_text(text, encoding="utf-8", newline="\n")
        def inventory():
            return {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
        before = inventory()
        monkeypatch.setattr(stable_install.subprocess, "run", lambda *a, **k: pytest.fail("Native preservation must not run pip or a process"))
        result = stable_install.install(root, base / "unused-wheels", base / "unused-inventory.json", runtime / "policy.json",
            expected_selection=current, expected_bootstrap=manifest["launcher_sha256"], registry=base / "registry", plugin_version="1", codex_home=home)
        assert result["native_frontend_preserved"] and result["runtime_unchanged"] and result["connection"] == command
        assert result["snapshot_id"] == snapshot and result["framework"] == launcher.FRAMEWORK_VERSION and result["sdk"] == "2.3.0"
        assert result["release"] == manifest["release"]
        assert result["requested_release"] == stable_install.release_metadata()
        assert "backend not reinstalled" in result["action"]
        setup = load_setup()
        repaired = setup.existing_connection(root, current, manifest["launcher_sha256"], codex_home=home, config=runtime / "policy.json")
        assert repaired["connection"] == command
        recipe = frontend_selection.plugin_connection(root, repaired["connection"])["mcpServers"]["fanuc-mcp-server"]
        assert recipe["command"] == "FanucMcpFrontend.exe" and recipe["args"] == command["args"]
        assert recipe["env"]["PATH"] == str(frontend) and recipe["cwd"] == "${PLUGIN_DATA}"
        assert inventory() == before
        settings = home / "config.toml"
        original_settings = settings.read_bytes()
        lowercase = {**command, "command": str(frontend / "fanucmcpfrontend.exe")}
        settings.write_bytes(original_settings.replace(json.dumps(command["command"]).encode(), json.dumps(lowercase["command"]).encode()))
        before_variant = inventory()
        retained_variant = stable_install.install(root, base / "unused", base / "unused", runtime / "policy.json",
            expected_selection=current, registry=base / "registry", plugin_version="1", codex_home=home)
        assert retained_variant["connection"] == lowercase and retained_variant["runtime_unchanged"]
        assert retained_variant["release"] == manifest["release"] and inventory() == before_variant
        settings.write_bytes(original_settings)  # Restore only this synthetic input.
        assert inventory() == before
        with pytest.raises(ValueError, match="selection changed"):
            stable_install.install(root, base / "unused", base / "unused", runtime / "policy.json", expected_selection="f" * 64,
                registry=base / "registry", plugin_version="1", codex_home=home)
        payload = frontend / "Backend.py"
        original = payload.read_bytes()
        payload.write_bytes(b"drift")
        with pytest.raises(ValueError, match="mismatch"):
            stable_install.install(root, base / "unused", base / "unused", runtime / "policy.json", expected_selection=current,
                registry=base / "registry", plugin_version="1", codex_home=home)
        assert payload.read_bytes() == b"drift"  # No silent repair or eager fallback.
        payload.write_bytes(original)  # Restore only this control's synthetic input.
        assert inventory() == before
        policy_path = runtime / "policy.json"
        original_policy = policy_path.read_bytes()
        requested = base / "changed-policy.json"
        requested.write_bytes(launcher.encoded({**policy, "source_dir": str(base / "different-inputs")}))
        with pytest.raises(ValueError, match="policy differs"):
            stable_install.install(root, base / "unused", base / "unused", requested, expected_selection=current,
                registry=base / "registry", plugin_version="1", codex_home=home)
        assert policy_path.read_bytes() == original_policy
        with pytest.raises(ValueError, match="policy differs"):
            setup.existing_connection(root, current, manifest["launcher_sha256"], codex_home=home, config=requested)
        from scripts.build_robot_plugin import copy_installer_services
        source = Path(__file__).resolve().parents[1]
        copied = base / "copied-installer"
        copy_installer_services(source, copied)
        assert (copied / "fanuc_workbench/services/frontend_selection.py").read_bytes() == (source / "fanuc_workbench/services/frontend_selection.py").read_bytes()
