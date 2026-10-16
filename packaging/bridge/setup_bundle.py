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


def main():
    parser = argparse.ArgumentParser(description="Install a local FANUC Workbench plugin runtime from bundled wheels")
    parser.add_argument("--runtime-dir", required=True, type=Path, help="Stable dedicated directory outside the plugin cache")
    parser.add_argument("--config", type=Path, help="Existing bridge JSON; otherwise create a zero-robot read-only configuration")
    parser.add_argument("--register", choices=("none", "plugin", "mcp"), default="none")
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
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("kind") != "fanuc-workbench-runtime":
        raise ValueError("Unexpected runtime ownership marker")
    runtime.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"kind": "fanuc-workbench-runtime", "compatibility": compatibility()}, indent=2), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull,
                       PIP_DISABLE_PIP_VERSION_CHECK="1")
    venv_dir = runtime / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    wheels = root / "plugin" / "wheels"
    packages = list(wheels.glob("fanuc_workbench_bridge-*.whl"))
    if len(packages) != 1:
        raise ValueError("Bundle must contain exactly one FANUC bridge wheel")
    subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheels),
                    "--upgrade", "--force-reinstall", str(packages[0])], check=True, env=environment)
    subprocess.run([str(python), "-m", "pip", "check"], check=True, env=environment)
    config = args.config.resolve() if args.config else runtime / "robot_bridge.local.json"
    if args.config and not config.is_file():
        raise ValueError("The supplied configuration file does not exist")
    if not config.exists():
        with config.open("x", encoding="utf-8") as stream:
            json.dump({"state_dir": "evidence", "source_dir": "edits", "robots": {},
                       "allow_writes": False, "approval_mode": "elicitation", "max_file_bytes": 16777216,
                       "max_backup_files": 5000, "max_backup_bytes": 536870912}, stream, indent=2)
    settings = {"python": str(python), "config": str(config)}
    (root / "plugin" / "runtime.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    checked = subprocess.run([str(python), "-B", "-m", "fanuc_workbench.bridge.doctor", "--config", str(config)],
                             env=environment, capture_output=True, text=True, check=True, timeout=60)
    doctor = json.loads(checked.stdout)
    (runtime / "doctor.json").write_text(json.dumps(doctor, indent=2), encoding="utf-8")
    if args.register != "none":
        codex = shutil.which("codex")
        if not codex:
            raise ValueError("Codex CLI was not found; runtime is installed but registration was not attempted")
        if args.codex_home:
            profile_dir = args.codex_home.resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            environment["CODEX_HOME"] = str(profile_dir)
        if args.register == "plugin":
            subprocess.run([codex, "plugin", "marketplace", "add", str(root), "--json"], check=True, env=environment)
            subprocess.run([codex, "plugin", "add", "fanuc-workbench@fanuc-workbench-local", "--json"], check=True, env=environment)
        else:
            subprocess.run([codex, "mcp", "add", "fanuc_workbench", "--", str(python), "-B", "-m",
                            "fanuc_workbench.bridge.mcp_server", "--config", str(config)], check=True, env=environment)
    print(json.dumps({"status": "installed", "runtime": str(runtime), "config": str(config),
                      "registration": args.register, "tools": doctor["tool_count"],
                      "configured_robots": doctor["configured_robots"], "controller_contacted": False}, indent=2))


if __name__ == "__main__":
    main()
