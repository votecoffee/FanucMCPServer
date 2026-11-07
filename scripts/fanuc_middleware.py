"""On-demand FANUC analysis and review packages for saved LS listings."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from tempfile import TemporaryDirectory


CLI_VERSION = "1.0.1"
MAX_INPUT_BYTES = 1024 * 1024
MAX_PAGE_SIZE = 500


class MiddlewareError(Exception):
    def __init__(self, code: str, message: str, exit_code: int):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise MiddlewareError("usage", message, 64)


def validate_local_path(path: Path, *, directory=False) -> None:
    if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or any(part == ".." or ":" in part for part in path.parts[1:])):
        raise MiddlewareError("invalid_path", "Supply an absolute local path.", 64)

    drive_type = ctypes.WinDLL("kernel32").GetDriveTypeW
    drive_type.argtypes = (wintypes.LPCWSTR,)
    drive_type.restype = wintypes.UINT
    if drive_type(path.anchor) not in (2, 3, 6):  # removable, fixed, RAM disk
        raise MiddlewareError("invalid_path", "Use a path on a local drive.", 64)
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise MiddlewareError("invalid_path", "Redirected paths are unsupported.", 64)
    matches_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not matches_type(info.st_mode):
        raise MiddlewareError("invalid_path", "Use an ordinary file or directory as requested.", 64)


def read_listing(path: Path) -> bytes:
    if path.suffix.casefold() != ".ls":
        raise MiddlewareError("invalid_input", "Supply a saved .LS file.", 64)
    validate_local_path(path)

    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise MiddlewareError("invalid_input", "Use an ordinary .LS file.", 64)
        if before.st_size > MAX_INPUT_BYTES:
            raise MiddlewareError("input_too_large", "The .LS file exceeds 1 MiB.", 65)
        data = stream.read(MAX_INPUT_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_INPUT_BYTES:
        raise MiddlewareError("input_too_large", "The .LS file exceeds 1 MiB.", 65)
    if len(data) != before.st_size or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise MiddlewareError("input_changed", "The .LS file changed while it was read.", 65)
    return data


def positions(path: Path, offset: int, limit: int) -> dict:
    from fanuc_workbench.parsers.ls import decode_listing, parse_positions_text

    raw = read_listing(path)
    rows = parse_positions_text(decode_listing(raw), path.stem)
    stop = offset + limit
    return {
        "ok": True,
        "operation": "positions",
        "input": {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        "result": {
            "analysis": "positions", "total": len(rows), "offset": offset,
            "next_offset": stop if stop < len(rows) else None,
            "items": [asdict(row) for row in rows[offset:stop]], "evidence_only": True,
        },
    }


def publish_package(args) -> dict:
    from fanuc_workbench.services.program_packages import build_change_package

    destination = args.destination
    validate_local_path(destination.parent, directory=True)
    is_reserved = getattr(os.path, "isreserved", None)
    reserved = is_reserved(destination) if is_reserved else destination.is_reserved()
    if reserved or not destination.name or destination.name.endswith((".", " ")) or ":" in destination.name:
        raise MiddlewareError("invalid_path", "Supply an ordinary new directory name.", 64)
    try:
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        with TemporaryDirectory(prefix=".fanuc-review-", dir=destination.parent) as staging:
            staged = Path(staging) / "package"
            result = build_change_package(
                args.workspace, args.path, staged, entry_programs=args.entry,
                inventory_path=args.inventory, allow_incomplete=args.allow_incomplete,
            )
            manifest = staged / "package.json"
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            # Windows rename fails if the requested name already exists.
            staged.rename(destination)
    except FileExistsError:
        raise MiddlewareError("destination_exists", "Choose a new package destination.", 73) from None
    except OSError:
        raise MiddlewareError("package_io_error", "Could not build or publish the package.", 74) from None
    return {
        **result, "path": str(destination),
        "manifest": str(destination / manifest.name), "manifest_sha256": digest,
        "deployment_authorized": False, "native_validation_required": True,
    }


def analyze_programs(args) -> dict:
    from fanuc_workbench.services.program_analysis import dependency_review, semantic_compare, trace_logic

    validate_local_path(args.workspace, directory=True)
    paths = [args.before, args.after] if args.command == "compare" else args.path
    if getattr(args, "inventory", None):
        paths = [*paths, args.inventory]
    for value in paths:
        relative = PurePosixPath(value)
        if (not relative.parts or relative.is_absolute() or relative.as_posix() != value
                or ".." in relative.parts or any(char in value for char in "\\:")):
            raise MiddlewareError("invalid_path", "Use explicit relative paths with forward slashes.", 64)
        validate_local_path(args.workspace / relative)

    if args.command == "package":
        return publish_package(args)
    if args.command == "compare":
        return semantic_compare(args.workspace, args.before, args.after, offset=args.offset, limit=args.limit)
    if args.command == "trace":
        return trace_logic(args.workspace, args.path, view=args.view, offset=args.offset, limit=args.limit)
    return dependency_review(
        args.workspace, args.path, entry_programs=args.entry, inventory_path=args.inventory,
        view=args.view, offset=args.offset, limit=args.limit,
    )


def invoke(argv: list[str]) -> tuple[int, str]:
    """Run the public CLI dispatch and return its exact JSON response and exit code."""
    parser = Parser(prog="RunMiddleware.cmd", description=__doc__,
                    epilog="Use catalog, describe OPERATION or run OPERATION --request PATH for the full workbench.")
    parser.add_argument("--version", action="version", version=json.dumps({"version": CLI_VERSION}))
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("positions", help="Read positions from one local LS file")
    listing.add_argument("--input", required=True, type=Path, help="Absolute path to a saved .LS file")
    trace = commands.add_parser("trace", help="Trace logic, I/O and register access in saved programs")
    compare = commands.add_parser("compare", help="Compare two LS revisions by change category")
    deps = commands.add_parser("dependencies", help="Review explicit program and resource dependencies")
    package = commands.add_parser("package", help="Publish a dependency-reviewed local artifact")
    for command in (trace, compare, deps, package):
        command.add_argument("--workspace", required=True, type=Path, help="Absolute saved workspace path")
    for command in (trace, deps, package):
        command.add_argument("--path", required=True, action="append", help="Relative LS file (repeatable)")
    compare.add_argument("--before", required=True, help="Relative path to the earlier LS revision")
    compare.add_argument("--after", required=True, help="Relative path to the later LS revision")
    trace.add_argument("--view", default="instructions", choices=("instructions", "accesses", "signals", "edges", "issues"))
    deps.add_argument("--view", default="requirements", choices=("requirements", "missing", "issues"))
    for command in (deps, package):
        command.add_argument("--entry", required=True, action="append", help="Entry program name (repeatable)")
        command.add_argument("--inventory", help="Relative path to explicit dependency inventory JSON")
    package.add_argument("--destination", required=True, type=Path, help="Absolute path to a new local directory")
    package.add_argument("--allow-incomplete", action="store_true", help="Publish an incomplete review-only package")
    for command in (listing, trace, compare, deps):
        command.add_argument("--offset", type=int, default=0)
        command.add_argument("--limit", type=int, default=100)

    try:
        args = parser.parse_args(argv)
        if sys.version_info < (3, 11):
            raise MiddlewareError("runtime_unsupported", "Python 3.11 or newer is required.", 69)
        if os.name != "nt":
            raise MiddlewareError("unsupported_platform", "This launcher requires Windows.", 69)
        max_page_size = MAX_PAGE_SIZE if args.command == "positions" else 100
        if args.command != "package" and (args.offset < 0 or not 1 <= args.limit <= max_page_size):
            raise MiddlewareError("usage", f"Use offset >= 0 and limit between 1 and {max_page_size}.", 64)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        try:
            if args.command == "positions":
                result = positions(args.input, args.offset, args.limit)
            else:
                result = {"ok": True, "operation": args.command, "result": analyze_programs(args)}
            return 0, json.dumps(result, allow_nan=False)
        finally:
            sys.path.pop(0)
    except MiddlewareError as error:
        code, message, status = error.code, str(error), error.exit_code
    except OSError:
        code, message, status = "input_unavailable", "Could not read the specified local input.", 66
    except (ValueError, ArithmeticError) as error:
        code, message, status = "invalid_evidence", str(error), 65
    except KeyboardInterrupt:
        code, message, status = "cancelled", "The local operation was interrupted.", 130
    return status, json.dumps({"ok": False, "error": {"code": code, "message": message}})


def main() -> int:
    if sys.argv[1:2] and sys.argv[1] in {"catalog", "describe", "run"}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from fanuc_workbench.local_cli import main as run_catalog
        return run_catalog(sys.argv[1:])
    status, output = invoke(sys.argv[1:])
    print(output)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
