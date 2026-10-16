"""Validated, hash-pinned local feature releases. No controller or MCP imports."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from fanuc_workbench.bridge.storage import RobotLocks, atomic_json


HOST_API = 1
MAX_MESSAGE = 4 * 1024 * 1024
MAX_REQUEST = 1024 * 1024
ID = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?")
HASH = re.compile(r"[a-f0-9]{64}")
PROPOSALS = {"file_replace", "file_create", "comment_update"}


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def read_json(path: Path, maximum: int = 262144, *, with_bytes=False):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value
    def constant(_):
        raise ValueError("Nonfinite JSON constant")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Extension metadata exceeds its size limit")
    value = json.loads(data, object_pairs_hook=unique, parse_constant=constant)
    return (value, data) if with_bytes else value


def identifier(value):
    if not isinstance(value, str) or len(value) > 64 or not ID.fullmatch(value) or value == "core":
        raise ValueError("Invalid or reserved extension ID")
    return value


def version(value):
    if not isinstance(value, str) or len(value) > 64 or not VERSION.fullmatch(value):
        raise ValueError("Extension version must be major.minor.patch with an optional suffix")
    return value


def digest(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError("An exact lowercase SHA-256 is required")
    return value


def relative_file(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 240 or "\\" in value:
        raise ValueError("Release files require simple relative POSIX paths")
    for part in value.split("/"):
        if (not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", part) or part.endswith(".")
                or part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
            raise ValueError("Invalid release file component")
    result = root.joinpath(*value.split("/"))
    if not result.resolve().is_relative_to(root.resolve()) or result.resolve() != result.absolute():
        raise ValueError("Release path is outside its directory")
    return result


def plain_tree(root: Path):
    for index, path in enumerate(root.rglob("*")):
        if index > 1024:
            raise ValueError("Release directory contains too many entries")
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError("Extension releases cannot contain links or redirected paths")


def check_schema(schema):
    from jsonschema import Draft202012Validator
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("Operation schemas must describe JSON objects")
    budget = [0]
    def walk(value, depth=0):
        budget[0] += 1
        if depth > 24 or budget[0] > 8192:
            raise ValueError("Operation schema is too complex")
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(item, str) or not item.startswith("#/")):
                    raise ValueError("Only local schema references are supported")
                if key == "$id":
                    raise ValueError("External schema identities are unsupported")
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
    walk(schema)
    Draft202012Validator.check_schema(schema)


def validate_data(schema, value, *, maximum=MAX_REQUEST):
    from jsonschema import Draft202012Validator
    if len(json_bytes(value)) > maximum:
        raise ValueError("Operation arguments exceed the size limit")
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error:
        # Never echo caller values, file contents or credentials in validation errors.
        raise ValueError("Operation data does not match its declared schema")


def load_release(root: Path, expected: str | None = None, *, packaged=False) -> tuple[dict, str]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Extension release directory is missing")
    plain_tree(root)
    metadata = root / "extension.json"
    manifest, raw = read_json(metadata, with_bytes=True)
    actual = hashlib.sha256(raw).hexdigest()
    if expected is not None and actual != digest(expected):
        raise ValueError("Extension manifest hash changed")
    required = {"schema_version", "id", "version", "host_api", "entrypoint", "files", "operations"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError("Extension manifest fields do not match host protocol 1")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or type(manifest["host_api"]) is not int or manifest["host_api"] != HOST_API:
        raise ValueError("Extension requires an unsupported host protocol")
    name = identifier(manifest["id"])
    version(manifest["version"])
    files = manifest["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= 128 or "extension.json" in files:
        raise ValueError("Release must hash between 1 and 128 support files")
    entrypoint = manifest["entrypoint"]
    if entrypoint not in files or not isinstance(entrypoint, str) or not entrypoint.endswith(".py"):
        raise ValueError("The Python entrypoint must be manifest-listed")
    expected_files = {"extension.json", *files}
    # Pip may compile bundled Python data. These caches are never captured or
    # executed: this exception applies only while seeding the trusted host bundle.
    actual_files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                    and not (packaged and p.parent.name == "__pycache__" and p.suffix == ".pyc")}
    if expected_files != actual_files:
        raise ValueError("Release contains missing or unlisted files")
    total = 0
    for relative, checksum in files.items():
        path = relative_file(root, relative)
        with path.open("rb") as stream:
            payload = stream.read(4 * 1024 * 1024 + 1)
        total += len(payload)
        if len(payload) > 4 * 1024 * 1024 or total > 16 * 1024 * 1024:
            raise ValueError("Extension support files exceed release size limits")
        if hashlib.sha256(payload).hexdigest() != digest(checksum):
            raise ValueError("Extension support file hash changed")
    operations = manifest["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 128:
        raise ValueError("Extension must declare between 1 and 128 operations")
    names = set()
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) - {"id", "description", "effect", "input_schema", "output_schema", "contexts", "proposal_kind"}:
            raise ValueError("Invalid operation descriptor")
        op_id = operation.get("id")
        if (not isinstance(op_id, str) or len(op_id) > 160 or not op_id.startswith(name + ".")
                or not ID.fullmatch(op_id) or op_id in names):
            raise ValueError("Operation IDs must be unique and belong to their extension")
        names.add(op_id)
        description = operation.get("description")
        if not isinstance(description, str) or not 1 <= len(description) <= 2048:
            raise ValueError("Operation needs a bounded description")
        effect = operation.get("effect")
        if effect not in {"read", "prepare"}:
            raise ValueError("Extensions can only read evidence or compute a prepared proposal")
        if (effect == "prepare" and operation.get("proposal_kind") not in PROPOSALS
                or effect == "read" and "proposal_kind" in operation):
            raise ValueError("Unsupported extension proposal contract")
        check_schema(operation.get("input_schema"))
        check_schema(operation.get("output_schema"))
        contexts = operation.get("contexts", [])
        if not isinstance(contexts, list) or len(contexts) > 8:
            raise ValueError("At most eight saved-artifact contexts are supported")
        context_names = set()
        for context in contexts:
            if (not isinstance(context, dict) or set(context) - {"name", "argument", "kind", "file_argument"}
                    or context.get("kind") not in {"any", "workspace", "recording", "snapshot"}
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", context.get("name", ""))
                    or context["name"] in context_names or context["name"] == "original"):
                raise ValueError("Unsupported artifact context binding")
            context_names.add(context["name"])
            for field in [context.get("argument"), *([context["file_argument"]] if "file_argument" in context else [])]:
                if (not isinstance(field, str) or field not in operation["input_schema"].get("required", [])
                        or operation["input_schema"].get("properties", {}).get(field, {}).get("type") != "string"):
                    raise ValueError("Artifact bindings require explicit required string arguments")
    return manifest, actual


def release_path(root: Path, name: str, release_version: str) -> Path:
    return relative_file(root, f"releases/{identifier(name)}/{version(release_version)}")


def load_registry(root: Path) -> list[dict]:
    data = read_json(root / "registry.json")
    if (not isinstance(data, dict) or set(data) != {"schema_version", "extensions"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        raise ValueError("Unsupported extension registry")
    rows = data["extensions"]
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError("At most sixteen selected extensions are supported")
    names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"id", "version", "manifest_sha256", "python"}:
            raise ValueError("Invalid selected release")
        identifier(row.get("id"))
        version(row.get("version"))
        digest(row.get("manifest_sha256"))
        if row["id"] in names:
            raise ValueError("Registry contains a duplicate extension ID")
        names.add(row["id"])
        interpreter = row.get("python", sys.executable)
        if not isinstance(interpreter, str) or not Path(interpreter).is_absolute() or not Path(interpreter).is_file():
            raise ValueError("An extension interpreter must be an existing absolute local executable")
    return rows


def install_release(root: Path, source: Path, python: Path | None = None, *, bootstrap=False, packaged=False) -> dict:
    """Operator-only staging. No code execution, overwrite, activation or robot contact."""
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve() != root:
        raise ValueError("Extension state cannot be a redirected directory")
    manifest, checksum = load_release(source, packaged=packaged)
    interpreter = (python or Path(sys.executable)).absolute()
    if not interpreter.is_file():
        raise ValueError("Extension interpreter is missing")
    with RobotLocks(root / "installer-locks").acquire("registry"):
        if bootstrap and (root / "registry.json").exists():
            return {"status": "existing_registry", "controller_contacted": False}
        target = release_path(root, manifest["id"], manifest["version"])
        if target.exists():
            load_release(target, checksum)
        else:
            target.mkdir(parents=True)
            for relative in [*manifest["files"], "extension.json"]:
                destination = relative_file(target, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                maximum = 262144 if relative == "extension.json" else 4 * 1024 * 1024
                with relative_file(source, relative).open("rb") as original:
                    payload = original.read(maximum + 1)
                if len(payload) > maximum:
                    raise ValueError("Source grew during staging")
                with destination.open("xb") as stream:
                    stream.write(payload)
            load_release(target, checksum)
        rows = load_registry(root) if (root / "registry.json").exists() else []
        selected = {"id": manifest["id"], "version": manifest["version"],
                    "manifest_sha256": checksum, "python": str(interpreter)}
        rows = [row for row in rows if row["id"] != manifest["id"]] + [selected]
        if len(rows) > 16:
            raise ValueError("Too many selected extensions")
        atomic_json(root / "registry.json", {"schema_version": 1, "extensions": rows})
        return {**selected, "status": "staged_only", "controller_contacted": False}


def snapshot_release(source: Path, target: Path, expected: str) -> dict:
    manifest, _ = load_release(source, expected)
    target.mkdir(parents=True)
    for relative in [*manifest["files"], "extension.json"]:
        maximum = 262144 if relative == "extension.json" else 4 * 1024 * 1024
        with relative_file(source, relative).open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Source grew while taking an extension snapshot")
        path = relative_file(target, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    copied, _ = load_release(target, expected)
    return copied
