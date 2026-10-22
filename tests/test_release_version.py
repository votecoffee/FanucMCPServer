"""Fixed identities and package boundaries; no service or native runtime starts."""
import importlib
import json
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tomllib
import zipfile

from packaging.version import Version
import pytest

from fanuc_workbench import __version__, __package_version__
from fanuc_workbench import version


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def forbid_process_start(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Version checks cannot start a subprocess")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.mark.parametrize("display,canonical,windows", [
    ("26.0102.030405", "26.102.30405", "26.102.304.5"),
    ("00.0101.000000", "0.101.0", "0.101.0.0"),
    ("24.0229.000001", "24.229.1", "24.229.0.1"),
    ("99.1231.235959", "99.1231.235959", "99.1231.2359.59"),
])
def test_padding_semver_pep440_calendar_and_bounded_windows_fields(display, canonical, windows):
    assert version.canonical_timestamp(display) == canonical
    assert version.display_timestamp(canonical) == display
    assert version.same_version(display, canonical)
    assert str(Version(display)) == canonical and Version(display) == Version(canonical)
    numeric = r"(0|[1-9][0-9]*)"
    assert re.fullmatch(rf"{numeric}\.{numeric}\.{numeric}", canonical)
    assert version.windows_file_version(display) == windows
    assert all(0 <= int(field) <= 65535 for field in windows.split("."))


@pytest.mark.parametrize("value", [None, "26.0000.000000", "26.0229.000000", "26.0132.000000",
    "26.1301.000000", "26.0101.240000", "26.0101.006000", "26.0101.000060", "26.0101.1000000",
    "2026.0101.000000", "26.0101.000000-dev", "26.0101.000000\n"])
def test_invalid_timestamps_are_refused(value):
    with pytest.raises(ValueError):
        version.canonical_timestamp(value)


def test_source_app_plugin_and_both_package_identities_are_one_fixed_stamp():
    build = runpy.run_path(str(ROOT / "scripts/build_robot_plugin.py"))
    release = build["verify_release"](ROOT)
    assert __version__ == release["display_version"]
    assert __package_version__ == release["package_version"]
    assert release["timezone"] == "America/New_York"
    assert release == version.release_metadata()
    assert not version.same_version(None, None)
    assert not version.same_version(__version__, "26.0101.000001")
    for relative in ("pyproject.toml", "packaging/bridge/pyproject.toml"):
        assert tomllib.loads((ROOT / relative).read_text())["project"]["version"] == __package_version__


def test_explicit_stamp_is_idempotent_and_build_refuses_mixed_identities(tmp_path):
    paths = ("pyproject.toml", "packaging/bridge/pyproject.toml", "fanuc_workbench/version.py", "plugins/fanuc-mcp-server/plugin.json")
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    stamp = runpy.run_path(str(ROOT / "scripts/stamp_release.py"))["stamp"]
    build = runpy.run_path(str(ROOT / "scripts/build_robot_plugin.py"))["verify_release"]
    result = stamp(tmp_path, "26.0102.030405")
    before = {relative: (tmp_path / relative).read_bytes() for relative in paths}
    assert stamp(tmp_path, "26.0102.030405") == result == build(tmp_path)
    assert before == {relative: (tmp_path / relative).read_bytes() for relative in paths}
    assert result["package_version"] == result["plugin_version"] == "26.102.30405"
    with pytest.raises(ValueError, match="fully padded"):
        stamp(tmp_path, "26.102.30405")
    plugin = tmp_path / paths[-1]
    data = json.loads(plugin.read_text()); data["version"] = "26.0102.030405"
    plugin.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="canonical release"):
        build(tmp_path)


def test_mcp_initialization_metadata_and_capabilities_use_loaded_app_version(tmp_path):
    from fanuc_workbench.bridge.config import BridgeConfig
    from fanuc_workbench.bridge.service import RobotBridge
    from fanuc_workbench.bridge.mcp_server import create_server
    with RobotBridge(BridgeConfig(tmp_path / "state", tmp_path / "edits")) as bridge:
        server = create_server(bridge)
        assert server._lowlevel_server.create_initialization_options().server_version == __version__
        assert bridge.operations.capabilities()["runtime"]["release"] == version.release_metadata()
        assert bridge.extensions.current is None


@pytest.mark.parametrize("module", ["bridge.stdio_supervisor", "bridge.mcp_server", "bridge.doctor",
    "bridge.framework_host", "bridge.extensions_cli", "bridge.guidance", "desktop_install", "program_tools"])
def test_cli_version_exits_before_runtime_or_required_arguments(module, monkeypatch, capsys):
    target = importlib.import_module("fanuc_workbench." + module)
    monkeypatch.setattr(sys, "argv", ["fanuc-cli", "--version"])
    with pytest.raises(SystemExit) as result:
        target.main()
    assert result.value.code == 0
    assert capsys.readouterr().out.strip() == "FanucMCPServer " + __version__


def test_gui_version_does_not_construct_a_desktop_session(monkeypatch, capsys):
    from fanuc_workbench import app
    monkeypatch.setattr(sys, "argv", ["fanuc-gui", "--version"])
    monkeypatch.setattr(app, "DesktopSession", lambda: pytest.fail("No GUI session may start"))
    assert app.main() == 0
    assert capsys.readouterr().out.strip() == "FanucMCPServer " + __version__


def test_manager_reports_loaded_source_separately_and_pads_installed_identities(tmp_path, monkeypatch):
    from fanuc_workbench.bridge import management
    monkeypatch.setattr(management, "installed_version", lambda name: {
        "fanuc-mcp-server-bridge": "26.102.30405", "universal-codex-tools": "0.20.0", "mcp": "2.3.0"}.get(name, "unknown"))
    pointer = tmp_path / "runtime.json"
    pointer.write_text(json.dumps({"plugin_id": management.PLUGIN_ID}))
    (tmp_path / "plugin.json").write_text(json.dumps({"name": "fanuc-mcp-server", "version": "26.102.30405"}))
    reporting = management.ServiceReporting(runtime=pointer, registry=tmp_path / "unused-registry")
    reporting.refresh()
    versions = reporting.status().versions
    assert versions["app"].running == __version__
    assert versions["app"].installed == versions["plugin"].installed == "26.0102.030405"
    assert versions["framework"].installed == "0.20.0" and versions["sdk"].installed == "2.3.0"
    assert reporting.agent.server is None and reporting.thread is None


def test_builder_reads_actual_wheel_distribution_version(tmp_path):
    verify = runpy.run_path(str(ROOT / "scripts/build_robot_plugin.py"))["verify_bridge_version"]
    wheel = tmp_path / "synthetic.whl"
    def write(value):
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("synthetic.dist-info/METADATA", f"Name: fanuc-mcp-server-bridge\nVersion: {value}\n\n")
    write(version.PACKAGE_VERSION)
    verify(wheel, version.release_metadata())
    write("0.17.0.dev0")
    with pytest.raises(ValueError, match="Built bridge metadata"):
        verify(wheel, version.release_metadata())


@pytest.mark.parametrize("stale_payload", [False, True])
def test_wheel_builder_uses_fresh_staging_and_rejects_stale_payloads(tmp_path, monkeypatch, stale_payload):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    build = runpy.run_path(str(ROOT / "scripts/build_app_wheels.py"))["build"]
    source = tmp_path / "source"
    for relative in ("pyproject.toml", "packaging/bridge/pyproject.toml", "fanuc_workbench/version.py",
                     "plugins/fanuc-mcp-server/plugin.json", "LICENSE", "README.md"):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    stale = source / "build/lib/fanuc_workbench/removed.py"
    stale.parent.mkdir(parents=True); stale.write_bytes(b"# retired payload")
    calls = []
    def run(command, **kwargs):
        stage, destination = Path(command[-1]), Path(command[-2])
        assert "--no-isolation" in command and not (stage / "build").exists()
        assert not (stage / "fanuc_workbench/removed.py").exists()
        destination.mkdir()
        distribution = "fanuc-mcp-server" + ("-bridge" if stage.name == "bridge" else "")
        with zipfile.ZipFile(destination / "synthetic.whl", "w") as archive:
            archive.writestr("synthetic.dist-info/METADATA", f"Name: {distribution}\nVersion: {version.PACKAGE_VERSION}\n\n")
            archive.writestr("fanuc_workbench/version.py", (stage / "fanuc_workbench/version.py").read_bytes())
            if stale_payload:
                archive.writestr("fanuc_workbench/removed.py", b"# retired payload")
        calls.append(stage.name)
    monkeypatch.setattr(subprocess, "run", run)
    output = tmp_path / "wheels"
    if stale_payload:
        with pytest.raises(ValueError, match="stale or changed package bytes"):
            build(source, output)
    else:
        assert build(source, output)["release"] == version.release_metadata()
        assert calls == ["desktop", "bridge"]
        with pytest.raises(FileExistsError, match="Preserve earlier artifacts"):
            build(source, output)
