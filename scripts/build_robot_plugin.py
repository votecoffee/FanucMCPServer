"""Build a same-platform local plugin bundle with offline dependency wheels."""
from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def verify_framework_wheel(source: Path, wheel: Path):
    """Verify the reviewed distribution before resolving an offline bundle."""
    lock = json.loads((source / "packaging/framework.lock.json").read_text())
    if wheel.name != lock["wheel"] or hashlib.sha256(wheel.read_bytes()).hexdigest() != lock["sha256"]:
        raise ValueError("Shared framework wheel differs from packaging/framework.lock.json")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        if (metadata["Name"] != lock["package"] or metadata["Version"] != lock["version"]
                or sorted(row for row in metadata.get_all("Requires-Dist", []) if not re.search(r";\s*extra\s*==", row)) != sorted(lock["dependencies"])):
            raise ValueError("Shared framework metadata differs from its dependency lock")
        record = next(name for name in names if name.endswith(".dist-info/RECORD"))
        rows = list(csv.reader(archive.read(record).decode().splitlines()))
        if len(names) != len(set(names)) or {row[0] for row in rows} != set(names):
            raise ValueError("Shared framework wheel has an incomplete or duplicate file inventory")
        for name, checksum, size in rows:
            if name == record and not checksum and not size:
                continue
            payload = archive.read(name)
            expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
            if checksum != expected or str(len(payload)) != size:
                raise ValueError("Shared framework wheel RECORD verification failed")
    return lock


def build(source: Path, output: Path, framework_wheel: Path):
    verify_framework_wheel(source, framework_wheel)
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
    shutil.copyfile(framework_wheel, wheels / framework_wheel.name)
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
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--find-links", str(wheels), "--constraint", str(source / "packaging/framework-requirements.txt"), "--dest", str(wheels), str(own)],
                   check=True, env=environment)
    docs = output / "docs"
    docs.mkdir()
    shutil.copyfile(source / "docs" / "SAFEGUARDS_GUI.md", docs / "SAFEGUARDS_GUI.md")
    shutil.copyfile(source / "docs" / "APP_FRAMEWORK.md", docs / "APP_FRAMEWORK.md")
    shutil.copyfile(source / "packaging" / "framework.lock.json", docs / "framework.lock.json")
    shutil.copyfile(source / "packaging" / "framework-requirements.txt", docs / "framework-requirements.txt")
    for name in ("DESKTOP_GUIDE.md", "ROBOT_BRIDGE.md", "ROBOT_BRIDGE_COVERAGE.md", "ROBOT_PLUGIN_SETUP.md", "ROBOT_EXTENSIONS.md", "ROBOT_NATIVE_API.md", "CONTROLLER_INTERFACES.md", "HTTP_READS.md", "SNPX_READS.md", "SNPX_DECODING.md", "SNPX_EVIDENCE.md", "SNPX_IO_EVIDENCE.md", "RECORDING_ANALYSIS.md", "RECORDING_EVIDENCE.md", "RECORDING_HANDSHAKE.md", "VQLOG_ANALYSIS.md", "ALARM_ANALYSIS.md", "ALARM_COMPARISON.md", "PROGRAM_DEPENDENCIES.md", "PROGRAM_DIAGNOSTICS.md", "REQUEST_CONCURRENCY.md", "COMMUNITY_INTEGRATIONS.md"):
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
    parser.add_argument("--framework-wheel", required=True, type=Path, help="Verified shared framework wheel matching packaging/framework.lock.json")
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.output.resolve(), args.framework_wheel.resolve()), indent=2))


if __name__ == "__main__":
    main()
