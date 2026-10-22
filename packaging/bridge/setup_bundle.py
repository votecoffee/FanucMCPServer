"""Offline setup for a built local plugin bundle. Uses only the standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def compatibility():
    return {"python": list(sys.version_info[:2]), "platform": sys.platform, "machine": platform.machine()}


def verify_bundle(root):
    manifest = json.loads((root / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest["compatibility"] != compatibility():
        raise ValueError("This bundle's dependency wheels require a matching Python version, OS and architecture")
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("Bundle manifest contains a missing or external file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Bundle hash mismatch: " + item["path"])
    return manifest


def select_config(runtime, explicit, installation):
    """Preserve an installed selection; fail before changing a runtime if it is lost."""
    if explicit is not None:
        config = explicit.resolve()
        if not config.is_file():
            raise ValueError("The supplied configuration file does not exist")
    elif installation.get("config"):
        config = Path(installation["config"])
        if not config.is_absolute() or not config.is_file():
            raise ValueError("The installed configuration is missing; supply --config to select its replacement")
        config = config.resolve()
    else:
        config = runtime / "robot_bridge.local.json"
        if installation and not config.is_file():
            raise ValueError("This older runtime has no saved configuration selection; supply --config once to retain it")
    return config


def plugin_connection(python, pointer, runtime):
    return {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", "mcpServers": {
        "fanuc-mcp-server": {"type": "stdio", "command": str(python),
            "args": ["-B", "-m", "fanuc_workbench.bridge.stdio_supervisor", "--runtime", str(pointer)],
            "cwd": str(runtime)}}}


def generated_file(root, relative, payload):
    path = root / relative
    if (path.is_symlink() or (path.exists() and getattr(path.stat(), "st_file_attributes", 0) & 0x400)
            or not path.resolve().is_relative_to(root.resolve())):
        raise ValueError("Generated plugin state must remain an ordinary file inside this bundle")
    fd, temporary = tempfile.mkstemp(prefix=".fanuc-setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def register_plugin(codex, root, environment):
    """Update our local marketplace without taking over a name owned elsewhere."""
    name = "fanuc-mcp-server-local"
    listed = subprocess.run([codex, "plugin", "marketplace", "list", "--json"], check=True,
                            env=environment, capture_output=True, text=True)
    existing = next((row for row in json.loads(listed.stdout)["marketplaces"] if row["name"] == name), None)
    previous = None
    if existing and Path(existing["root"]).resolve() != root.resolve():
        previous = Path(existing["root"]).resolve()
        descriptor = previous / ".agents" / "plugins" / "marketplace.json"
        try:
            data = json.loads(descriptor.read_text(encoding="utf-8"))
            owned = (data["name"] == name and [item["name"] for item in data["plugins"]] == ["fanuc-mcp-server"]
                     and (previous / "BUNDLE_MANIFEST.json").is_file()
                     and json.loads((previous / "plugin" / "plugin.json").read_text(encoding="utf-8"))["name"] == "fanuc-mcp-server")
        except (OSError, ValueError, KeyError, TypeError):
            owned = False
        if not owned:
            raise ValueError("The marketplace name is owned by an unrecognized source; registration was not changed")
        subprocess.run([codex, "plugin", "marketplace", "remove", name, "--json"], check=True, env=environment)
    try:
        subprocess.run([codex, "plugin", "marketplace", "add", str(root), "--json"], check=True, env=environment)
        installed = subprocess.run([codex, "plugin", "add", "fanuc-mcp-server@" + name, "--json"],
                                   check=True, env=environment, capture_output=True, text=True)
        result = json.loads(installed.stdout)
        expected = json.loads((root / "plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        if result.get("version") != expected:
            raise ValueError("Codex reported a plugin version different from this bundle")
        print(json.dumps(result, indent=2))
    except BaseException:
        if previous is not None:
            # Registration rollback only; never delete either local source tree.
            subprocess.run([codex, "plugin", "marketplace", "remove", name, "--json"], env=environment)
            subprocess.run([codex, "plugin", "marketplace", "add", str(previous), "--json"],
                           check=True, env=environment)
        raise


def main():
    parser = argparse.ArgumentParser(description="Install a local FanucMCPServer plugin runtime from bundled wheels")
    parser.add_argument("--runtime-dir", required=True, type=Path, help="Stable dedicated directory outside the plugin cache")
    parser.add_argument("--config", type=Path, help="Existing bridge JSON; otherwise retain the installed selection, or create a zero-robot configuration on first install")
    parser.add_argument("--register", choices=("none", "plugin", "mcp"), default="plugin",
                        help="Install the standalone plugin by default; none installs only the runtime; mcp registers the direct server without a plugin")
    parser.add_argument("--codex-home", type=Path, help="Optional separate Codex profile for registration/testing")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    verify_bundle(root)
    runtime = args.runtime_dir.resolve()
    if runtime == root or runtime.is_relative_to(root / "plugin"):
        raise ValueError("Keep runtime outside the plugin directory/cache")
    marker = runtime / "installation.json"
    if runtime.exists() and any(runtime.iterdir()) and not marker.exists():
        raise ValueError("Runtime directory is not owned by this installer; choose an empty dedicated directory")
    installation = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    if marker.exists() and (not isinstance(installation, dict) or installation.get("kind") not in
                            {"fanuc-mcp-server-runtime", "fanuc-workbench-runtime"}):
        raise ValueError("Unexpected runtime ownership marker")
    config = select_config(runtime, args.config, installation)
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull,
                       PIP_DISABLE_PIP_VERSION_CHECK="1")
    selected = args.register
    if installation.get("registration") == "broker" or installation.get("broker"):
        raise ValueError("The broker route has been removed. Reconcile retained work in the old installation, then select a new standalone runtime directory")
    if args.register != "none":
        codex = shutil.which("codex")
        if not codex:
            raise ValueError("Codex CLI was not found; choose --register none to install only the runtime")
        if args.codex_home:
            profile_dir = args.codex_home.resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            environment["CODEX_HOME"] = str(profile_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({**installation, "kind": installation.get("kind", "fanuc-mcp-server-runtime"),
                                 "compatibility": compatibility()}, indent=2), encoding="utf-8")
    venv_dir = runtime / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    wheels = root / "plugin" / "wheels"
    packages = list(wheels.glob("fanuc_mcp_server_bridge-*.whl"))
    if len(packages) != 1:
        raise ValueError("Bundle must contain exactly one FANUC bridge wheel")
    if installation.get("kind") == "fanuc-workbench-runtime":
        # The renamed distributions share stable Python import paths. Remove
        # the old wheel first so two distributions do not own the same files.
        subprocess.run([str(python), "-B", "-m", "pip", "uninstall", "--yes", "fanuc-workbench-bridge"],
                       check=True, env=environment)
    subprocess.run([str(python), "-B", "-m", "pip", "install", "--no-compile", "--no-index", "--find-links", str(wheels),
                    "--upgrade", "--force-reinstall", str(packages[0])], check=True, env=environment)
    subprocess.run([str(python), "-m", "pip", "check"], check=True, env=environment)
    if not config.exists():
        with config.open("x", encoding="utf-8") as stream:
            json.dump({"state_dir": "evidence", "source_dir": "edits", "robots": {},
                       "allow_writes": False, "approval_mode": "elicitation", "max_file_bytes": 16777216,
                       "max_backup_files": 5000, "max_backup_bytes": 536870912}, stream, indent=2)
    settings = {"python": str(python), "config": str(config)}
    if selected == "plugin":
        settings["plugin_id"] = "fanuc-mcp-server@fanuc-mcp-server-local"
    checked = subprocess.run([str(python), "-B", "-m", "fanuc_workbench.bridge.doctor", "--config", str(config)],
                             env=environment, capture_output=True, text=True, check=True, timeout=60)
    doctor = json.loads(checked.stdout)
    (runtime / "doctor.json").write_text(json.dumps(doctor, indent=2), encoding="utf-8")
    generated_file(root, "plugin/runtime.json", json.dumps(settings, indent=2).encode())
    connection = json.dumps(plugin_connection(python, root / "plugin" / "runtime.json", runtime), indent=2).encode()
    generated_file(root, "plugin/mcp.json", connection)
    marker.write_text(json.dumps({"kind": "fanuc-mcp-server-runtime", "compatibility": compatibility(),
                                 "config": str(config), "plugin_connection_sha256": hashlib.sha256(connection).hexdigest(),
                                 **{key: installation[key] for key in
                                 ("registration",) if key in installation}}, indent=2), encoding="utf-8")
    if args.register != "none":
        if selected == "plugin":
            register_plugin(codex, root, environment)
        elif selected == "mcp":
            subprocess.run([codex, "mcp", "add", "fanuc-mcp-server", "--", str(python), "-B", "-m",
                            "fanuc_workbench.bridge.stdio_supervisor", "--config", str(config)], check=True, env=environment)
        saved = json.loads(marker.read_text(encoding="utf-8"))
        saved["registration"] = selected
        marker.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(json.dumps({"status": "installed", "runtime": str(runtime), "config": str(config),
                      "registration": selected,
                      "tools": doctor["tool_count"],
                      "configured_robots": doctor["configured_robots"], "controller_contacted": False}, indent=2))


if __name__ == "__main__":
    main()
