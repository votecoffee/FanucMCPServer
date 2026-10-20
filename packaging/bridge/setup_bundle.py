"""Offline setup for a built local plugin bundle. Uses only the standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import venv
import tomllib
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


def installed_plugins(codex, environment):
    result = subprocess.run([codex, "plugin", "list", "--json"], check=True,
                            env=environment, capture_output=True, text=True, timeout=30)
    rows = json.loads(result.stdout)["installed"]
    if not isinstance(rows, list):
        raise ValueError("Unexpected Codex plugin inventory")
    return rows


def find_broker(plugins, environment, explicit=None):
    """Use the selected profile's enabled plugin, not a guessed cache folder."""
    matches = [row for row in plugins if row.get("name") == "universal-tools"
               and row.get("installed") is True and row.get("enabled") is True]
    if len(matches) != 1:
        raise ValueError("One enabled Universal Tools plugin is required in this Codex profile")
    frontend = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", matches[0].get("version", ""))
    if not frontend or not (0, 8, 0) <= tuple(map(int, frontend.groups())) < (1, 0, 0):
        raise ValueError("The enabled Universal Tools frontend must be >=0.8.0,<1.0.0")
    profile = Path(environment.get("CODEX_HOME", Path.home() / ".codex"))
    settings = profile / "config.toml"
    if settings.exists():
        policies = tomllib.loads(settings.read_text(encoding="utf-8")).get("plugins", {})
        for key in ("universal-tools", matches[0].get("pluginId")):
            policy = policies.get(key, {})
            if policy.get("enabled") is False or policy.get("mcp_servers", {}).get("universal-tools", {}).get("enabled") is False:
                raise ValueError("Universal Tools is disabled in this Codex profile")
    source = matches[0].get("source", {})
    if explicit is None and source.get("source") != "local":
        raise ValueError("Supply --broker-runtime for this Universal Tools distribution")
    pointer = explicit or Path(source["path"]) / "runtime.json"
    runtime = json.loads(pointer.read_text(encoding="utf-8"))
    python, state = Path(runtime["python"]), Path(runtime["state"])
    if not python.is_absolute() or not state.is_absolute() or not python.is_file() or not (state / "settings.json").is_file():
        raise ValueError("Universal Tools runtime is missing or uninitialized")
    version = subprocess.run([str(python), "-B", "-m", "universal_tools", "--version"], check=True,
                             env=environment, capture_output=True, text=True, timeout=15).stdout.strip()
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or not (0, 8, 0) <= tuple(map(int, match.groups())) < (1, 0, 0):
        raise ValueError("This installer supports Universal Tools >=0.8.0,<1.0.0")
    # An existing settings file is not sufficient evidence of initialization.
    # Validate through the broker's own contract without starting its daemon or
    # printing the local bearer token.
    subprocess.run([str(python), "-B", "-c",
        "import sys; from pathlib import Path; from universal_tools.models import load_settings; load_settings(Path(sys.argv[1]))",
        str(state)], check=True, env=environment, capture_output=True, text=True, timeout=15)
    return {"python": str(python), "state": str(state), "version": version}


def select_registration(requested, installation, plugins):
    if requested != "auto":
        return requested
    if "registration" in installation:
        return installation["registration"]
    # Older installers did not record a route. Never switch a discovered existing
    # integration or install a second identity alongside the legacy plugin.
    if any(row.get("name") in {"fanuc-mcp-server", "fanuc-workbench"}
           and row.get("installed") is True for row in plugins):
        return "none"
    return "plugin"


def skill_files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def install_broker_skill(root, profile, installation):
    source = root / "plugin" / "skills" / "fanuc-robot"
    target = profile / "skills" / "fanuc-robot"
    if target.resolve() != target or any(path.is_symlink() for path in target.rglob("*")):
        raise ValueError("FANUC skill path is redirected; it was preserved")
    desired = skill_files(source)
    if "SKILL.md" not in desired:
        raise ValueError("The bundled FANUC workflow skill is missing")
    previous = installation.get("broker_skill", {})
    if target.exists():
        actual = skill_files(target)
        if actual != desired and (previous.get("path") != str(target) or previous.get("files") != actual):
            raise ValueError("An unowned or edited fanuc-robot skill exists; it was preserved")
        if set(actual) != set(desired):
            raise ValueError("FANUC skill file layout changed; reconcile it before upgrading")
    for relative in desired:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / relative).read_bytes())
    return {"path": str(target), "files": desired}


def require_idle_broker(broker, environment):
    contexts = subprocess.run([broker["python"], "-B", "-m", "universal_tools", "--state", broker["state"],
                               "contexts"], check=True, env=environment, capture_output=True, text=True, timeout=15)
    if any(row.get("service_id") == "fanuc-mcp-server" for row in json.loads(contexts.stdout)["contexts"]):
        raise ValueError("FANUC broker work or recovery is recorded; reconcile it before installation")


def normalized_broker_manifests(broker, environment, *paths):
    """Validate the complete registrations, including broker-owned defaults."""
    result = subprocess.run([broker["python"], "-B", "-c",
        "import json, sys; from pathlib import Path; from universal_tools.models import load_manifest; "
        "print(json.dumps([load_manifest(Path(path)).model_dump(mode='json') for path in sys.argv[1:]]))",
        *(str(path) for path in paths)], check=True, env=environment,
        capture_output=True, text=True, timeout=15)
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or len(rows) != len(paths) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Unexpected broker registration validation result")
    return rows


def register_broker(root, runtime, python, config, broker, environment, installation, allow_writes):
    require_idle_broker(broker, environment)
    descriptor = runtime / "broker-service.json"
    command = [str(python), "-B", "-m", "fanuc_workbench.bridge.broker_server", "--config", str(config),
               "--manifest", str(descriptor)]
    if allow_writes:
        command.append("--allow-writes")
    subprocess.run(command, check=True, env=environment, timeout=60)
    existing = Path(broker["state"]) / "services" / "fanuc-mcp-server.json"
    if existing.exists():
        # Do not replace registration/policy or reset a session/recovery barrier.
        proposed, current = normalized_broker_manifests(broker, environment, descriptor, existing)
        if current != proposed:
            raise ValueError("A different FANUC broker registration exists; reconcile active work and intentionally update it with the broker CLI")
    profile = Path(environment.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    skill = install_broker_skill(root, profile, installation)
    if not existing.exists():
        subprocess.run([broker["python"], "-B", "-m", "universal_tools", "--state", broker["state"],
                        "register", str(descriptor)], check=True, env=environment, timeout=30)
    return skill


def main():
    parser = argparse.ArgumentParser(description="Install a local FanucMCPServer plugin runtime from bundled wheels")
    parser.add_argument("--runtime-dir", required=True, type=Path, help="Stable dedicated directory outside the plugin cache")
    parser.add_argument("--config", type=Path, help="Existing bridge JSON; otherwise retain the installed selection, or create a zero-robot configuration on first install")
    parser.add_argument("--register", choices=("none", "auto", "broker", "plugin", "mcp"), default="plugin",
                        help="Install the standalone plugin by default; none installs only the runtime; auto retains a recorded route or uses standalone on fresh installs; broker explicitly requires Universal Tools")
    parser.add_argument("--codex-home", type=Path, help="Optional separate Codex profile for registration/testing")
    parser.add_argument("--broker-runtime", type=Path, help="Optional Universal Tools runtime.json pointer")
    parser.add_argument("--broker-allow-writes", action="store_true",
                        help="Enable exact non-read outer tools in a NEW broker registration; robot write/approval policy is unchanged")
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
    broker, broker_error, skill = None, None, installation.get("broker_skill")
    if args.register != "none":
        codex = shutil.which("codex")
        if not codex:
            raise ValueError("Codex CLI was not found; choose --register none to install only the runtime")
        if args.codex_home:
            profile_dir = args.codex_home.resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            environment["CODEX_HOME"] = str(profile_dir)
        plugins = []
        if args.register in {"auto", "broker"}:
            plugins = installed_plugins(codex, environment)
        selected = select_registration(args.register, installation, plugins)
        if selected == "broker":
            try:
                broker = find_broker(plugins, environment, args.broker_runtime)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                broker_error = str(exc)
        if selected not in {"none", "plugin", "mcp", "broker"}:
            raise ValueError("The recorded integration route is invalid")
        if selected == "broker":
            if broker is None:
                raise ValueError("Broker registration unavailable; prior integration preserved: " + str(broker_error))
            previous_broker = installation.get("broker", {})
            if args.register == "auto" and previous_broker and previous_broker.get("state") != broker["state"]:
                raise ValueError("The broker state changed; select the intended route explicitly after reconciliation")
            require_idle_broker(broker, environment)
    # Even runtime-only updates must not replace a retained broker's app files
    # while its context or a recovery barrier is recorded.
    if installation.get("registration") == "broker" and installation.get("broker"):
        require_idle_broker(installation["broker"], environment)
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
    checked = subprocess.run([str(python), "-B", "-m", "fanuc_workbench.bridge.doctor", "--config", str(config)],
                             env=environment, capture_output=True, text=True, check=True, timeout=60)
    doctor = json.loads(checked.stdout)
    (runtime / "doctor.json").write_text(json.dumps(doctor, indent=2), encoding="utf-8")
    (root / "plugin" / "runtime.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    marker.write_text(json.dumps({"kind": "fanuc-mcp-server-runtime", "compatibility": compatibility(),
                                 "config": str(config), **{key: installation[key] for key in
                                 ("registration", "broker_skill", "broker") if key in installation}}, indent=2), encoding="utf-8")
    if args.register != "none":
        if selected == "broker":
            # Existing explicit broker write policy survives ordinary upgrades.
            allow_writes = args.broker_allow_writes or installation.get("broker", {}).get("allow_writes", False)
            skill = register_broker(root, runtime, python, config, broker, environment, installation, allow_writes)
            broker["allow_writes"] = allow_writes
        elif selected == "plugin":
            register_plugin(codex, root, environment)
        elif selected == "mcp":
            subprocess.run([codex, "mcp", "add", "fanuc-mcp-server", "--", str(python), "-B", "-m",
                            "fanuc_workbench.bridge.mcp_server", "--config", str(config)], check=True, env=environment)
        saved = json.loads(marker.read_text(encoding="utf-8"))
        saved["registration"] = selected
        if selected == "broker":
            saved.update(broker=broker, broker_skill=skill)
        marker.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print(json.dumps({"status": "installed", "runtime": str(runtime), "config": str(config),
                      "registration": selected, "broker_unavailable": broker_error,
                      "tools": doctor["tool_count"],
                      "configured_robots": doctor["configured_robots"], "controller_contacted": False}, indent=2))


if __name__ == "__main__":
    main()
