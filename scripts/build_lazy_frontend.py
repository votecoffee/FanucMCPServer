"""Prepare a reviewed Windows lazy-discovery candidate; never publish settings."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(source, root, capture, output, *, csc):
    from fanuc_workbench.bridge import stable_launcher as launcher
    from fanuc_workbench.version import release_metadata
    release = release_metadata()
    if launcher.parse(launcher.read(root / "application.json")) != {"schema_version": 1, "app": "fanuc-mcp-server"}:
        raise ValueError("The existing installation is not owned by FANUC")
    selection, manifest, runtime = launcher.selected(root)
    capture_bytes = capture.read_bytes()
    values = json.loads(capture_bytes)
    if len(values["tools/list"]["tools"]) != 15:
        raise ValueError("Expected the reviewed standalone offline catalog")
    if values["initialize"]["serverInfo"]["version"] != manifest["release"]["display_version"]:
        raise ValueError("Captured catalog belongs to a different backend release")
    if output.exists():
        raise FileExistsError("Preserve existing candidates; choose a new output directory")
    output.mkdir(parents=True)
    native = source / "native/FanucMcpFrontend"
    executable = output / "FanucMcpFrontend.exe"
    with tempfile.TemporaryDirectory(prefix="frontend-build-", dir=output.parent) as temporary:
        stage = Path(temporary).resolve()
        if not stage.is_relative_to(output.parent.resolve()):
            raise ValueError("Build staging escaped its owned output directory")
        assembly = stage / "AssemblyInfo.cs"
        assembly.write_text('using System.Reflection;\n'
            '[assembly: AssemblyTitle("FanucMCPServer Discovery Frontend")]\n'
            '[assembly: AssemblyProduct("FanucMCPServer")]\n'
            f'[assembly: AssemblyVersion("{release["windows_file_version"]}")]\n'
            f'[assembly: AssemblyFileVersion("{release["windows_file_version"]}")]\n'
            f'[assembly: AssemblyInformationalVersion("{release["display_version"]}")]\n',
            encoding="utf-8", newline="\n")
        subprocess.run([str(csc), "/nologo", "/codepage:65001", "/target:winexe", "/platform:x64", "/optimize+",
            "/reference:System.Web.Extensions.dll", "/out:" + str(executable), str(assembly),
            *(str(native / name) for name in ("Json.cs", "Windows.cs", "Program.cs"))], check=True)
    (output / "FanucMcpFrontend.exe.config").write_bytes(
        b'<?xml version="1.0" encoding="utf-8"?>\n<configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" /></startup></configuration>\n')
    shutil.copyfile(source / "fanuc_workbench/bridge/stable_launcher.py", output / "Backend.py")
    shutil.copyfile(root / "StartMcp.py", output / "LegacyEntry.py")
    # Public descriptors only. No guidance contents/revisions, health, current
    # configuration, live occupancy, plan handles or engineering targets.
    catalog = {"snapshot_id": selection["snapshot_id"], "initialize": values["initialize"],
        "protocol_versions": ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
        "static": {key: values[key] for key in ("tools/list", "prompts/list", "resources/list", "resources/templates/list")}}
    (output / "catalog.json").write_bytes(launcher.encoded(catalog))
    rows = launcher.inventory(output)
    descriptor = {"schema_version": 1, "app": "fanuc-mcp-server", "profile": "standalone-offline", "frontend_release": release,
        "backend_root": str(root.absolute()), "snapshot_id": selection["snapshot_id"],
        "application_sha256": digest(root / "application.json"),
        "catalog_capture_sha256": hashlib.sha256(capture_bytes).hexdigest(),
        "runtime": manifest["runtime"], "files": rows}
    path = output / "frontend.json"
    path.write_bytes(launcher.encoded(descriptor))
    return {"candidate": str(output), "manifest_sha256": digest(path), "selection": selection,
        "connection": {"command": str(executable), "args": ["--manifest", str(path), "--sha256", digest(path)],
            "cwd": str(output)}, "published": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--catalog-capture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--csc", type=Path, default=Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"))
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.runtime_root.resolve(),
        args.catalog_capture.resolve(), args.output.resolve(), csc=args.csc), indent=2))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
