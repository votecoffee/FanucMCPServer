"""Verify and retain this app's selected native frontend; never edit settings."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import tomllib

from fanuc_workbench.bridge import stable_launcher as launcher


MEMBERS = {"FanucMcpFrontend.exe", "FanucMcpFrontend.exe.config", "Backend.py", "LegacyEntry.py", "catalog.json"}


def verify_connection(root, connection):
    """Return a checked selection and policy, without executing its payload."""
    root = Path(root).absolute()
    command = Path(connection["command"])
    arguments = connection.get("args", [])
    if (not command.is_absolute() or command.name.casefold() != "fanucmcpfrontend.exe"
            or len(arguments) != 4 or arguments[0] != "--manifest" or arguments[2] != "--sha256"
            or not re.fullmatch(r"[a-f0-9]{64}", arguments[3])):
        raise ValueError("Selected FANUC native frontend has an unsupported binding; it was not replaced")
    path = Path(arguments[1])
    if not path.is_absolute() or path.name != "frontend.json":
        raise ValueError("Selected native manifest must have an absolute owned binding")
    raw = launcher.read(path, launcher.MAX_MANIFEST)
    if launcher.digest(raw) != arguments[3]:
        raise ValueError("Selected native manifest hash differs; eager Python fallback is forbidden")
    descriptor = launcher.parse(raw)
    if (descriptor.get("schema_version") != 1 or descriptor.get("app") != "fanuc-mcp-server"
            or descriptor.get("profile") != "standalone-offline"):
        raise ValueError("Selected native frontend ownership/profile differs")
    if not os.path.samefile(Path(descriptor["backend_root"]), root):
        raise ValueError("Selected native frontend belongs to a different installation")
    if not os.path.samefile(command, path.parent / "FanucMcpFrontend.exe"):
        raise ValueError("Selected frontend executable is outside its immutable closure")
    rows = descriptor["files"]
    if len(rows) != len(MEMBERS) or {row["path"] for row in rows} != MEMBERS:
        raise ValueError("Selected native frontend inventory differs")
    launcher.validate_entries(rows)
    expected = rows + [{"path": "frontend.json", "bytes": len(raw), "sha256": launcher.digest(raw)}]
    if sorted(launcher.inventory(path.parent), key=lambda row: row["path"]) != sorted(expected, key=lambda row: row["path"]):
        raise ValueError("Selected native frontend inventory/hash mismatch")
    ownership = launcher.read(root / "application.json")
    if (launcher.digest(ownership) != descriptor["application_sha256"]
            or launcher.parse(ownership) != {"schema_version": 1, "app": "fanuc-mcp-server"}):
        raise ValueError("Native backend installation ownership differs")
    selection, manifest, runtime = launcher.selected(root, pinned_snapshot=descriptor["snapshot_id"],
        launcher_path=path.parent / "LegacyEntry.py")
    if manifest["runtime"] != descriptor["runtime"]:
        raise ValueError("Native backend runtime identity differs")
    policy = launcher.parse(launcher.read(runtime / "policy.json"))
    if policy.get("robots") != {} or policy.get("allow_writes") is not False:
        raise ValueError("Native selection must retain its zero-robot read-only policy")
    catalog = launcher.parse(launcher.read(path.parent / "catalog.json", launcher.MAX_MANIFEST))
    if catalog["snapshot_id"] != selection["snapshot_id"]:
        raise ValueError("Native catalog belongs to a different backend")
    return ({"runtime_unchanged": True, "native_frontend_preserved": True,
        "snapshot_id": selection["snapshot_id"], "release": manifest.get("release"),
        "framework": manifest["framework"], "sdk": manifest["sdk"],
        "connection": {key: copy.deepcopy(connection[key]) for key in ("command", "args", "cwd", "env") if key in connection},
        "native_frontend": {"manifest": str(path), "sha256": arguments[3], "release": descriptor.get("frontend_release")}}, policy)


def selected_native(root, *, codex_home=None):
    """Read only FANUC's row in the chosen profile; no selection by newest folder."""
    root = Path(root).absolute()
    if not (root / "launcher/current.json").is_file():
        return None
    home = Path(codex_home) if codex_home is not None else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    path = home / "config.toml"
    try:
        raw = launcher.read(path, launcher.MAX_MANIFEST)
    except FileNotFoundError:
        return None
    row = tomllib.loads(raw.decode("utf-8")).get("mcp_servers", {}).get("fanuc-mcp-server", {})
    if Path(row.get("command", "")).name.casefold() != "fanucmcpfrontend.exe":
        return None
    result = verify_connection(root, row)
    current = tomllib.loads(launcher.read(path, launcher.MAX_MANIFEST).decode("utf-8"))
    if current.get("mcp_servers", {}).get("fanuc-mcp-server", {}) != row:
        raise ValueError("FANUC native selection changed during verification")
    return result


def policy_document(path):
    path = Path(path).absolute()
    document = launcher.parse(launcher.read(path))
    if document.get("robots") != {} or document.get("allow_writes") is not False:
        raise ValueError("Stable installer requires a zero-robot read-only offline policy")
    for key in ("state_dir", "source_dir"):
        value = Path(document[key])
        document[key] = str(value if value.is_absolute() else (path.parent / value).absolute())
    return document


def retained_installation(root, config, *, expected_selection, expected_bootstrap=None, codex_home=None):
    selected = selected_native(root, codex_home=codex_home)
    if selected is None:
        return None
    result, policy = selected
    pointer = Path(root) / "launcher/current.json"
    if launcher.digest(launcher.read(pointer)) != expected_selection:
        raise ValueError("Launcher selection changed before native preservation")
    if expected_bootstrap is not None and launcher.digest(launcher.read(Path(root) / "StartMcp.py", launcher.MAX_MANIFEST)) != expected_bootstrap:
        raise ValueError("Bootstrap changed before native preservation")
    if policy_document(config) != policy:
        raise ValueError("Requested policy differs from the pinned native backend; prepare a reviewed frontend/backend update")
    if launcher.digest(launcher.read(pointer)) != expected_selection:
        raise ValueError("Launcher selection changed during native preservation")
    result.update(selection_sha256=expected_selection, registration="not-published", controller_contacted=False,
        old_runtimes_retained=True, action="verified-native-selection-retained; backend not reinstalled")
    return result


def plugin_connection(root, connection):
    verified, _ = verify_connection(root, connection)
    command = Path(verified["connection"]["command"])
    if ";" in str(command.parent):
        raise ValueError("Native executable directory cannot be one Windows PATH entry")
    environment = dict(connection.get("env", {}))
    environment["PATH"] = str(command.parent)
    return {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", "mcpServers": {
        "fanuc-mcp-server": {"type": "stdio", "command": command.name, "args": connection["args"],
            "cwd": "${PLUGIN_DATA}", "env": environment}}}
