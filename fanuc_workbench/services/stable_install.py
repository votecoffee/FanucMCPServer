"""Explicit offline MCP installation; immutable runtimes, atomic selection, no registration.

The caller supplies a reviewed wheel inventory. This service never reads Codex
settings, edits caches, fetches dependencies, runs native tools or retires peers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile
from importlib.util import source_hash, MAGIC_NUMBER
from importlib._bootstrap_external import _code_to_hash_pyc

from fanuc_workbench.bridge import stable_launcher as launcher
from fanuc_workbench.version import release_metadata


FRAMEWORK_SHA256 = "a1b3777f8e8c1900e136a514d89ddb485a6bf4d69a525e3d24e1d04cd040efb3"


def archive_runtime(runtime, python, entry_bytes):
    """Seal pure code in archives, keep native extensions/guidance explicit.

    Retain the newly created unpacked installation outside the selected runtime;
    never rewrite a previous runtime or the common interpreter's Lib tree.
    """
    site = runtime / "site-packages"
    target_magic = subprocess.run([str(python / "python.exe"), "-I", "-B", "-c",
        "from importlib.util import MAGIC_NUMBER;print(MAGIC_NUMBER.hex())"], check=True, capture_output=True, text=True).stdout.strip()
    if target_magic != MAGIC_NUMBER.hex():
        raise ValueError("Archive compiler differs from retained interpreter bytecode format")
    def write_code(archive, path, relative):
        data = path.read_bytes()
        archive.writestr(relative, data)
        if path.suffix == ".py":
            code = compile(data, "fanuc-runtime/" + relative, "exec", dont_inherit=True, optimize=0)
            archive.writestr(relative + "c", bytes(_code_to_hash_pyc(code, source_hash(data), checked=True)))
    native = runtime / "native"
    native.mkdir()
    shutil.copytree(site / "fanuc_workbench/guidance", runtime / "guidance")
    with zipfile.ZipFile(runtime / "dependencies.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(site.rglob("*")):
            if not path.is_file():
                continue
            launcher.ordinary(path)
            relative = path.relative_to(site)
            if path.suffix.lower() in {".pyd", ".dll", ".exe"}:
                target = native / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            else:
                write_code(archive, path, relative.as_posix())
    with zipfile.ZipFile(runtime / "stdlib.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((python / "Lib").rglob("*")):
            if path.is_file():
                launcher.ordinary(path)
                relative = path.relative_to(python / "Lib")
                if path.suffix.lower() in {".pyd", ".dll", ".exe"}:
                    target = native / "python-stdlib" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                else:
                    write_code(archive, path, relative.as_posix())
    launcher.atomic(runtime / "layout.json", launcher.encoded({"schema_version": 1, "layout": "archive-v1"}))
    probe = runtime / "bootstrap-probe.py"
    probe.write_bytes(entry_bytes)
    # Record the actual stdlib bootstrap import closure plus all native libraries.
    # Future stdlib imports use stdlib.zip after bootstrap verification.
    code = ("import sys,runpy,ctypes,json,msvcrt,encodings.cp437;from ctypes import wintypes;from pathlib import Path;"
        "runpy.run_path(sys.argv[1],run_name='fanuc_probe');p=Path(sys.base_prefix);"
        "v={Path(m.__file__).relative_to(p).as_posix() for m in tuple(sys.modules.values()) "
        "if getattr(m,'__file__',None) and Path(m.__file__).is_relative_to(p) and Path(m.__file__).is_file()};"
        "print(json.dumps(sorted(v)))")
    result = subprocess.run([str(python / "python.exe"), "-I", "-B", "-c", code, str(probe)],
        check=True, capture_output=True, text=True)
    selected = set(json.loads(result.stdout))
    selected.update(p.relative_to(python).as_posix() for p in (python / "DLLs").rglob("*") if p.is_file())
    selected.update(p.name for p in python.iterdir() if p.is_file())
    rows = [{"path": name, "bytes": (python / name).stat().st_size,
        "sha256": launcher.digest((python / name).read_bytes())} for name in sorted(selected, key=str.casefold)]
    retained = runtime.parents[1] / "installer-staging" / uuid.uuid4().hex
    retained.mkdir(parents=True)
    if not site.resolve().is_relative_to(runtime.resolve()) or not retained.resolve().is_relative_to(runtime.parents[1].resolve()):
        raise ValueError("New installer staging paths escaped their app-owned root")
    os.replace(site, retained / "site-packages")
    return rows


def copy_python(destination, source=None):
    """Retain the selected base interpreter/stdlib, excluding installed packages.

    It is a base executable, not a venv redirector. Actual running identities
    still come from kernel handles, never this logical installation path.
    """
    source = launcher.ordinary(source or sys.base_prefix, directory=True)
    destination = Path(destination)
    destination.mkdir()
    for path in source.iterdir():
        if path.is_file() and (path.suffix.lower() == ".dll" or path.name in {"python.exe", "pythonw.exe", "LICENSE.txt"}):
            launcher.ordinary(path)
            shutil.copyfile(path, destination / path.name)
    def ignore(folder, names):
        return [name for name in names if name in {"site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"}
                or name.endswith((".pyc", ".pyo"))]
    for name in ("Lib", "DLLs"):
        shutil.copytree(source / name, destination / name, ignore=ignore)
    # Hard deterministic import roots; no registry, current directory, user site,
    # editable installs or env injection before verification of selected packages.
    (destination / f"python{sys.version_info.major}{sys.version_info.minor}._pth").write_bytes(b".\nDLLs\nLib\n")
    if not (destination / "pythonw.exe").is_file():
        raise ValueError("A reviewed Windows windowless base Python is required")


def install(root, wheelhouse, wheel_manifest, config, *, expected_selection, registry, plugin_version, python_source=None,
            expected_bootstrap=None):
    if os.name != "nt":
        raise ValueError("Stable FANUC launcher installation currently requires Windows")
    root, wheelhouse, config = Path(root).absolute(), Path(wheelhouse).absolute(), Path(config).absolute()
    # Source/build/cache roots cannot become the durable application entry.
    rejected = {".git", ".local", ".robot-bridge", "build", "dist", "cache", "stages", "node_modules"}
    if any(part.casefold() in rejected for part in root.parts):
        raise ValueError("Choose an app-owned installation outside checkouts, caches and build output")
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise ValueError("Stable launcher must not be installed in a development checkout")
    rows = launcher.parse(launcher.read(wheel_manifest, launcher.MAX_MANIFEST))
    launcher.verify(wheelhouse, rows)
    wheels = [wheelhouse / row["path"] for row in rows]
    if any(path.suffix != ".whl" or path.parent != wheelhouse for path in wheels):
        raise ValueError("Reviewed wheelhouse must contain only flat wheel files")
    framework = [row for row in rows if row["path"] == "universal_codex_tools-0.20.0-py3-none-any.whl"]
    if len(framework) != 1 or framework[0]["sha256"] != FRAMEWORK_SHA256:
        raise ValueError("Reviewed framework 0.20.0 wheel hash differs")
    release = release_metadata()
    package_name = f"fanuc_mcp_server_bridge-{release['package_version']}-py3-none-any.whl"
    if not (wheelhouse / package_name).is_file():
        raise ValueError("Wheelhouse lacks this exact FANUC release")
    document = launcher.parse(launcher.read(config))
    if document.get("robots") != {} or document.get("allow_writes") is not False:
        raise ValueError("Stable installer currently accepts only zero-robot read-only offline policies")
    # Resolve data references against the original policy; preserve existing
    # evidence and sources without copying, deleting or relocating a live store.
    for key in ("state_dir", "source_dir"):
        value = Path(document[key])
        document[key] = str(value if value.is_absolute() else (config.parent / value).absolute())
    root.mkdir(parents=True, exist_ok=True)
    launcher.ordinary(root, directory=True)
    entry = root / "StartMcp.py"
    entry_bytes = Path(launcher.__file__).read_bytes()
    (root / "launcher").mkdir(exist_ok=True)
    with launcher.locked(root / "launcher/install.lock"):
        marker = root / "application.json"
        if marker.exists():
            if launcher.parse(launcher.read(marker)) != {"schema_version": 1, "app": "fanuc-mcp-server"}:
                raise ValueError("Installation root is not owned by FANUC")
        elif any(path.name not in {"launcher"} for path in root.iterdir()):
            raise ValueError("Installation root is not empty or owned by FANUC")
        else:
            launcher.atomic(marker, launcher.encoded({"schema_version": 1, "app": "fanuc-mcp-server"}))
        pointer = root / "launcher/current.json"
        before = launcher.digest(launcher.read(pointer)) if pointer.exists() else "absent"
        if before != expected_selection:
            raise ValueError("Launcher selection changed before installation")
        if pointer.exists():
            launcher.selected(root)  # Never bless a tampered existing bootstrap/runtime.
        elif (root / "python").exists():
            raise ValueError("Incomplete installation has retained interpreter bytes; inspect them before another attempt")
        migrate = entry.exists() and launcher.read(entry, launcher.MAX_MANIFEST) != entry_bytes
        if migrate and launcher.digest(launcher.read(entry, launcher.MAX_MANIFEST)) != expected_bootstrap:
            raise ValueError("Bootstrap migration requires its exact reviewed prior SHA-256; old entry retained")
        if not entry.exists():
            with entry.open("xb") as stream:
                stream.write(entry_bytes)
        python = root / "python"
        if not python.exists():
            copy_python(python, python_source)
        runtimes = root / "runtimes"
        runtimes.mkdir(exist_ok=True)
        runtime = runtimes / release["package_version"]
        if runtime.exists():
            raise ValueError("Immutable runtime already exists; retain it and choose a new fixed release")
        runtime.mkdir()
        environment = {k: v for k, v in os.environ.items() if k.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
        environment.update(PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1")
        subprocess.run([sys.executable, "-I", "-B", "-m", "pip", "install", "--no-compile", "--no-index",
            "--find-links", str(wheelhouse), "--target", str(runtime / "site-packages"), str(wheelhouse / package_name)],
            env=environment, check=True, stdout=sys.stderr)
        launcher.verify(wheelhouse, rows)
        launcher.atomic(runtime / "policy.json", launcher.encoded(document))
        plugin = {"name": "fanuc-mcp-server", "version": plugin_version}
        launcher.atomic(runtime / "plugin.json", launcher.encoded(plugin))
        launcher.atomic(runtime / "runtime.json", launcher.encoded({"plugin_id": "fanuc-mcp-server@fanuc-mcp-server-local",
            "release": release, "profile": "standalone-offline", "stable_root": str(root)}))
        python_files = archive_runtime(runtime, python, entry_bytes)
        probe_imports = {"python_files": python_files, "runtime_files": launcher.inventory(runtime)}
        launcher.atomic(runtime / "probe-imports.json", launcher.encoded(probe_imports))
        manifest = {"schema_version": 1, "layout": "archive-v1", "profile": "standalone-offline", "runtime": release["package_version"],
            "release": release, "package_version": release["package_version"], "framework": "0.20.0", "sdk": "2.3.0",
            "management_registry": str(Path(registry).absolute()), "installed_plugin_version": plugin_version,
            "launcher_sha256": launcher.digest(entry_bytes),
            "python_files": python_files, "runtime_files": launcher.inventory(runtime), "wheels": rows}
        # Verify installed distribution metadata using the retained base executable.
        code = ("import sys,runpy,importlib.metadata as m;"
            "n=runpy.run_path(sys.argv[1],run_name='fanuc_installer_probe');"
            "n['configure_packages'](sys.argv[2],n['parse'](n['read'](n['Path'](sys.argv[2])/'probe-imports.json',n['MAX_MANIFEST'])));"
            "assert [m.version(p) for p in ('fanuc-mcp-server-bridge','mcp','universal-codex-tools')]==sys.argv[3:];"
            "from mcp.server.mcpserver import MCPServer;from universal_tools.windows_resources import ProcessDriver")
        probe_entry = runtime / "bootstrap-probe.py"
        subprocess.run([str(python / "python.exe"), "-I", "-B", "-c", code, str(probe_entry), str(runtime),
            release["package_version"], "2.3.0", "0.20.0"], check=True, env=environment)
        snapshot_id = launcher.publish(root, manifest, expected_selection=expected_selection,
            bootstrap_bytes=entry_bytes if migrate else None, expected_bootstrap=expected_bootstrap)
        checked, _, _ = launcher.selected(root)
        assert checked["snapshot_id"] == snapshot_id
        connection = {"command": str(python / "pythonw.exe"), "args": ["-I", "-B", str(entry)], "cwd": str(root),
            "env": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"}}
        receipt = {"schema_version": 1, "release": release, "selection_sha256": launcher.digest(launcher.read(pointer)),
            "snapshot_id": snapshot_id, "connection": connection, "framework": "0.20.0", "sdk": "2.3.0",
            "registration": "not-published", "controller_contacted": False, "old_runtimes_retained": True}
        receipts = root / "receipts"
        receipts.mkdir(exist_ok=True)
        launcher.atomic(receipts / (uuid.uuid4().hex + ".json"), launcher.encoded(receipt))
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--wheel-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--expected-selection-sha256", required=True, help="Exact current.json SHA-256, or absent for first installation")
    parser.add_argument("--management-registry", required=True, type=Path)
    parser.add_argument("--installed-plugin-version", required=True, help="Observed standalone plugin version; this installer does not publish plugins")
    parser.add_argument("--expected-bootstrap-sha256", help="Exact prior entry hash for an explicitly reviewed bootstrap migration")
    args = parser.parse_args(argv)
    try:
        result = install(args.app_root, args.wheelhouse, args.wheel_manifest, args.config,
            expected_selection=args.expected_selection_sha256, registry=args.management_registry, plugin_version=args.installed_plugin_version,
            expected_bootstrap=args.expected_bootstrap_sha256)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("FANUC stable installation refused: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
