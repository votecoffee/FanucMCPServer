"""Build a same-platform local plugin bundle with offline dependency wheels."""
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
from pathlib import Path


def build(source: Path, output: Path):
    if output.exists():
        raise FileExistsError("Choose a new output directory; existing bundles are preserved")
    if sys.platform != "win32":
        raise ValueError("The current local plugin launcher targets Windows (py -3)")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    plugin = output / "plugin"
    shutil.copytree(source / "plugins" / "fanuc-mcp-server", plugin,
                    ignore=shutil.ignore_patterns("runtime.json", "__pycache__", "*.pyc"))
    shutil.copyfile(source / "packaging" / "bridge" / "setup_bundle.py", output / "setup.py")
    wheels = plugin / "wheels"
    wheels.mkdir()
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    with tempfile.TemporaryDirectory(prefix="fanuc_build_", dir=output.parent) as temporary:
        stage = Path(temporary).resolve()
        assert stage.is_relative_to(output.parent.resolve())
        shutil.copytree(source / "fanuc_workbench", stage / "fanuc_workbench",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(source / "packaging" / "bridge" / "pyproject.toml", stage / "pyproject.toml")
        shutil.copyfile(source / "LICENSE", stage / "LICENSE")
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels), str(stage)],
                       check=True, env=environment)
    own = next(wheels.glob("fanuc_mcp_server_bridge-*.whl"))
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--dest", str(wheels), str(own)],
                   check=True, env=environment)
    docs = output / "docs"
    docs.mkdir()
    for name in ("ROBOT_BRIDGE.md", "ROBOT_BRIDGE_COVERAGE.md", "ROBOT_PLUGIN_SETUP.md", "ROBOT_EXTENSIONS.md", "ROBOT_NATIVE_API.md", "CONTROLLER_INTERFACES.md", "HTTP_READS.md", "SNPX_READS.md", "VQLOG_ANALYSIS.md", "COMMUNITY_INTEGRATIONS.md"):
        shutil.copyfile(source / "docs" / name, docs / name)
    shutil.copytree(source / "fanuc_workbench" / "licenses", output / "fanuc_workbench" / "licenses")
    shutil.copyfile(source / "LICENSE", output / "LICENSE")
    marketplace = output / ".agents" / "plugins"
    marketplace.mkdir(parents=True)
    (marketplace / "marketplace.json").write_text(json.dumps({
        "name": "fanuc-mcp-server-local", "interface": {"displayName": "FanucMCPServer Local"},
        "plugins": [{"name": "fanuc-mcp-server", "source": {"source": "local", "path": "./plugin"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]
    }, indent=2), encoding="utf-8")
    entries = [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted(output.rglob("*")) if path.is_file()]
    manifest = {"compatibility": {"python": list(sys.version_info[:2]), "platform": sys.platform,
                                   "machine": platform.machine()}, "files": entries}
    (output / "BUNDLE_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"bundle": str(output), "files": len(entries), "wheels": len(list(wheels.glob("*.whl"))),
            "bytes": sum(row["bytes"] for row in entries)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
