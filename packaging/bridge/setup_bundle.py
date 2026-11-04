"""Offline setup for a built local plugin bundle. Uses only the standard library."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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
    if not root.is_absolute() or Path(python) != expected or ";" in str(expected.parent):
        raise ValueError("Prepare the app-owned stable windowless launcher before publication")
    return {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", "mcpServers": {
        "fanuc-mcp-server": {"type": "stdio", "command": "pythonw.exe",
            "args": ["-I", "-B", str(root / "StartMcp.py")], "cwd": "${PLUGIN_DATA}",
            "env": {"PATH": str(expected.parent), "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"}}}}


def existing_connection(root, expected_selection, expected_bootstrap):
    """Verify an existing offline installation without publishing a generation."""
    from fanuc_workbench.bridge import stable_launcher as launcher
    root = Path(root).absolute()
    if launcher.parse(launcher.read(root / "application.json")) != {"schema_version": 1, "app": "fanuc-mcp-server"}:
        raise ValueError("Existing runtime is not owned by FANUC")
    if launcher.digest(launcher.read(root / "launcher/current.json")) != expected_selection:
        raise ValueError("Existing runtime selection changed")
    if launcher.digest(launcher.read(root / "StartMcp.py", launcher.MAX_MANIFEST)) != expected_bootstrap:
        raise ValueError("Existing runtime bootstrap changed")
    selection, manifest, runtime = launcher.selected(root)
    policy = launcher.parse(launcher.read(runtime / "policy.json"))
    if policy.get("robots") != {} or policy.get("allow_writes") is not False:
        raise ValueError("Existing runtime must retain its zero-robot read-only offline profile")
    if launcher.digest(launcher.read(root / "launcher/current.json")) != expected_selection:
        raise ValueError("Existing runtime selection changed during verification")
    return {"runtime_unchanged": True, "snapshot_id": selection["snapshot_id"],
            "release": manifest.get("release"), "connection": {
                "command": str(root / "python/pythonw.exe"),
                "args": ["-I", "-B", str(root / "StartMcp.py")], "cwd": str(root)}}


@contextmanager
def publication_lock():
    """Serialize CLI settings publication with the other installed MCP apps."""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\CodexMcpSettingsPublication-v1")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        status = kernel.WaitForSingleObject(handle, 30000)
        if status not in (0, 0x80):
            raise TimeoutError("Codex settings publication is busy; no registration was changed")
        acquired = True
        yield
    finally:
        try:
            if acquired and not kernel.ReleaseMutex(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)


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


def migrate_owned_marketplace(config, raw, before, root):
    """Move only an existing, recognized local source binding; retain its policy."""
    import copy
    import re
    import tomllib
    name = "fanuc-mcp-server-local"
    existing = before.get("marketplaces", {}).get(name)
    if not existing or existing.get("source_type") != "local":
        return
    previous = Path(existing["source"])
    if previous.resolve() == root.resolve():
        return
    try:
        descriptor = json.loads((previous / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        owned = (descriptor["name"] == name and [row["name"] for row in descriptor["plugins"]] == ["fanuc-mcp-server"]
                 and (previous / "BUNDLE_MANIFEST.json").is_file()
                 and json.loads((previous / "plugin/plugin.json").read_text(encoding="utf-8"))["name"] == "fanuc-mcp-server")
    except (OSError, ValueError, KeyError, TypeError):
        owned = False
    if not owned:
        raise ValueError("The marketplace name is owned by an unrecognized source; registration was not changed")
    text = raw.decode("utf-8")
    headers = list(re.finditer(r"(?m)^\[[^\r\n]+\][ \t]*(?:#[^\r\n]*)?\r?$", text))
    sections = [(item.end(), headers[index+1].start() if index+1 < len(headers) else len(text))
                for index, item in enumerate(headers)
                if re.fullmatch(r'\[marketplaces\.(?:fanuc-mcp-server-local|"fanuc-mcp-server-local"|\'fanuc-mcp-server-local\')\][ \t]*(?:#[^\r\n]*)?\r?', item.group())]
    if len(sections) != 1:
        raise ValueError("Cannot narrowly identify the existing FANUC marketplace section")
    start, end = sections[0]
    pattern = r"(?m)^([ \t]*source[ \t]*=[ \t]*)(?:\"(?:[^\"\\\r\n]|\\.)*\"|'[^'\r\n]*')([ \t]*(?:#[^\r\n]*)?)(\r?$)"
    section, count = re.subn(pattern, lambda match: match[1] + json.dumps(str(root)) + match[2] + match[3], text[start:end])
    updated = (text[:start] + section + text[end:]).encode("utf-8")
    expected = copy.deepcopy(before)
    expected["marketplaces"][name]["source"] = str(root)
    if count != 1 or tomllib.loads(updated.decode("utf-8")) != expected or config.read_bytes() != raw:
        raise ValueError("Codex config changed or its marketplace source cannot be migrated narrowly")
    generated_file(config.parent, config.name, updated)
    if config.read_bytes() != updated:
        raise RuntimeError("Codex marketplace migration readback differs")


def register_plugin(codex, root, environment, expected_config_sha256=None):
    with publication_lock():
        if expected_config_sha256 is not None:
            import tomllib
            config = Path(environment.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
            raw = config.read_bytes() if config.exists() else b""
            if (hashlib.sha256(raw).hexdigest() if config.exists() else "absent") != expected_config_sha256:
                raise ValueError("Codex config changed before plugin registration")
            before = tomllib.loads(raw.decode("utf-8")) if raw else {}
            if b"\r" in raw.replace(b"\r\n", b""):
                raise ValueError("Codex config has unsupported physical line endings")
            plugin_id = "fanuc-mcp-server@fanuc-mcp-server-local"
            direct = before.get("mcp_servers", {}).get("fanuc-mcp-server")
            policy = before.get("plugins", {}).get(plugin_id, {})
            if direct and direct.get("enabled", True) and policy.get("mcp_servers", {}).get("fanuc-mcp-server", {}).get("enabled") is not False:
                raise ValueError("The direct FANUC route is enabled; preserve a disabled bundled route before registration")
            migrate_owned_marketplace(config, raw, before, root)
        result = _register_plugin(codex, root, environment)
        if expected_config_sha256 is not None:
            import copy
            expected = copy.deepcopy(before)
            expected.setdefault("plugins", {}).setdefault(plugin_id, {})["enabled"] = True
            after_raw = config.read_bytes()
            after = tomllib.loads(after_raw.decode("utf-8"))
            marketplace = after.get("marketplaces", {}).get("fanuc-mcp-server-local")
            if marketplace is not None:
                if (set(marketplace) != {"source_type", "source"} or marketplace["source_type"] != "local"
                        or not os.path.samefile(marketplace["source"], root)):
                    raise RuntimeError("Codex published an unexpected FANUC marketplace binding")
                expected.setdefault("marketplaces", {})["fanuc-mcp-server-local"] = marketplace
            if after != expected:
                raise RuntimeError("Codex plugin registration changed unexpected settings; inspect preserved preimage")
            # The CLI may normalize all physical endings while adding its own
            # marketplace row. Restore only the observed preimage's EOL style
            # from the verified current output, never from a stale config copy.
            normalized = after_raw.replace(b"\r\n", b"\n")
            if b"\r\n" in raw:
                normalized = normalized.replace(b"\n", b"\r\n")
            if normalized != after_raw:
                if tomllib.loads(normalized.decode("utf-8")) != after or config.read_bytes() != after_raw:
                    raise RuntimeError("Codex config changed before format-preserving publication")
                generated_file(config.parent, config.name, normalized)
        return result


def _register_plugin(codex, root, environment):
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
        return result
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
    parser.add_argument("--plugin-only", action="store_true", help="Publish this plugin against the exact existing stable runtime; do not install/select a backend")
    parser.add_argument("--expected-codex-config-sha256", help="Reviewed Codex config preimage for guarded CLI registration")
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
    if args.plugin_only:
        if not args.expected_bootstrap_sha256:
            raise ValueError("Plugin-only publication requires the existing bootstrap SHA-256")
        result = existing_connection(args.runtime_dir, args.expected_selection_sha256, args.expected_bootstrap_sha256)
    else:
        result = install(args.runtime_dir, wheels, wheel_manifest, args.config,
            expected_selection=args.expected_selection_sha256, registry=args.management_registry,
            plugin_version=args.installed_plugin_version or versions.PACKAGE_VERSION,
            expected_bootstrap=args.expected_bootstrap_sha256)
    connection = plugin_connection(result["connection"]["command"], None, args.runtime_dir)
    generated_file(root, "plugin/mcp.json", json.dumps(connection, indent=2).encode())
    if args.register == "plugin":
        register_plugin(codex, root, environment, args.expected_codex_config_sha256)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
