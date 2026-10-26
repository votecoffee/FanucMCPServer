"""Offline setup for a built local plugin bundle. Uses only the standard library."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


_version_path = Path(__file__).resolve().with_name("release_version.py")
if not _version_path.is_file():  # Source checkout; bundles carry their own hash-bound copy.
    _version_path = Path(__file__).resolve().parents[2] / "fanuc_workbench/version.py"
_version_spec = importlib.util.spec_from_file_location("fanuc_setup_version", _version_path)
versions = importlib.util.module_from_spec(_version_spec)
_version_spec.loader.exec_module(versions)


def compatibility():
    return {"python": list(sys.version_info[:2]), "platform": sys.platform, "machine": platform.machine()}


def verify_bundle(root):
    manifest = json.loads((root / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("release") != versions.release_metadata():
        raise ValueError("Bundle release identity differs from its fixed release stamp")
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
    # The recipe never pins a disposable plugin cache or its runtime.json.
    root = Path(runtime)
    expected = root / "python" / "pythonw.exe"
    if Path(python) != expected:
        raise ValueError("Prepare the app-owned stable windowless launcher before publication")
    return {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", "mcpServers": {
        "fanuc-mcp-server": {"type": "stdio", "command": str(python),
            "args": ["-I", "-B", str(root / "StartMcp.py")], "cwd": str(root),
            "env": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"}}}}


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
        if not versions.same_version(result.get("version"), expected):
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
    parser = argparse.ArgumentParser(description="Install a reviewed app-owned offline FANUC runtime and standalone plugin")
    parser.add_argument("--runtime-dir", required=True, type=Path, help="Dedicated app-owned installation outside checkouts/caches")
    parser.add_argument("--config", required=True, type=Path, help="Explicit zero-robot read-only offline policy")
    parser.add_argument("--expected-selection-sha256", required=True, help="Exact selection hash, or absent on first install")
    parser.add_argument("--management-registry", required=True, type=Path)
    parser.add_argument("--installed-plugin-version", help="Observed plugin version when registration is deferred")
    parser.add_argument("--expected-bootstrap-sha256", help="Exact prior entry hash for a reviewed bootstrap migration")
    parser.add_argument("--register", choices=("none", "plugin"), default="plugin")
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    verify_bundle(root)
    if (args.runtime_dir / "installation.json").exists():
        raise ValueError("Legacy runtime retained for reconciliation; choose a fresh app-owned installation root")
    environment = {k:v for k,v in os.environ.items() if k.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    if args.codex_home:
        environment["CODEX_HOME"] = str(args.codex_home.resolve())
    codex = shutil.which("codex") if args.register == "plugin" else None
    if args.register == "plugin" and not codex:
        raise ValueError("Codex CLI unavailable; choose --register none to prepare without registration")
    if args.register == "none" and not args.installed_plugin_version:
        raise ValueError("Supply the observed --installed-plugin-version when registration is deferred")
    from fanuc_workbench.services.stable_install import install
    from fanuc_workbench.bridge.stable_launcher import inventory, encoded
    wheels = root / "plugin/wheels"
    wheel_manifest = root / "wheel-inventory.json"
    generated_file(root, "wheel-inventory.json", encoded(inventory(wheels)))
    result = install(args.runtime_dir, wheels, wheel_manifest, args.config,
        expected_selection=args.expected_selection_sha256, registry=args.management_registry,
        plugin_version=args.installed_plugin_version or versions.PACKAGE_VERSION,
        expected_bootstrap=args.expected_bootstrap_sha256)
    connection = {"$schema":"https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers":{"fanuc-mcp-server":{"type":"stdio", **result["connection"]}}}
    generated_file(root, "plugin/mcp.json", json.dumps(connection, indent=2).encode())
    if args.register == "plugin":
        register_plugin(codex, root, environment)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
