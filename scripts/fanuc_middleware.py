"""On-demand read-only FANUC middleware for a saved LS listing."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


JOB_MUTEX = r"Local\OpenAIEngineeringMiddleware.Job.v1"
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


@contextmanager
def admit_job():
    if os.name != "nt":
        raise MiddlewareError("unsupported_platform", "Windows is required for shared admission.", 69)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.ReleaseMutex.restype = kernel.CloseHandle.restype = wintypes.BOOL

    handle = kernel.CreateMutexW(None, False, JOB_MUTEX)
    if not handle:
        raise MiddlewareError("admission_failed", "Could not open the shared job mutex.", 70)
    owns_mutex = False
    try:
        wait = kernel.WaitForSingleObject(handle, 0)
        owns_mutex = wait in (0, 0x80)  # WAIT_OBJECT_0 or WAIT_ABANDONED
        if wait == 0x102:  # WAIT_TIMEOUT
            raise MiddlewareError("busy", "An engineering middleware job is already active.", 75)
        if wait == 0x80:
            raise MiddlewareError("admission_abandoned", "The prior job ended without releasing admission.", 70)
        if wait != 0:
            raise MiddlewareError("admission_failed", "Could not check the shared job mutex.", 70)
        yield
    finally:
        try:
            if owns_mutex and not kernel.ReleaseMutex(handle):
                raise MiddlewareError("admission_failed", "Could not release the shared job mutex.", 70)
        finally:
            if not kernel.CloseHandle(handle):
                raise MiddlewareError("admission_failed", "Could not close the shared job mutex handle.", 70)


def read_listing(path: Path) -> bytes:
    if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or path.suffix.casefold() != ".ls"
            or any(part == ".." or ":" in part for part in path.parts[1:])):
        raise MiddlewareError("invalid_input", "Supply an absolute local .LS file path.", 64)

    drive_type = ctypes.WinDLL("kernel32").GetDriveTypeW
    drive_type.argtypes = (wintypes.LPCWSTR,)
    drive_type.restype = wintypes.UINT
    if drive_type(path.anchor) not in (2, 3, 6):  # removable, fixed, RAM disk
        raise MiddlewareError("invalid_input", "Use a saved .LS file on a local drive.", 64)
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise MiddlewareError("invalid_input", "Use an ordinary .LS file and directory path.", 64)
    if not stat.S_ISREG(info.st_mode):
        raise MiddlewareError("invalid_input", "Use an ordinary .LS file.", 64)

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
    raw = read_listing(path)
    # Both entry points use the checkout containing this CLI. Load the parser
    # only inside the admitted operation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from fanuc_workbench.parsers.ls import decode_listing, parse_positions_text
    finally:
        sys.path.pop(0)
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


def invoke(argv: list[str]) -> tuple[int, str]:
    """Run the public CLI dispatch and return its exact JSON response and exit code."""
    parser = Parser(prog="RunMiddleware.cmd", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("positions", help="Read positions from one local LS file")
    listing.add_argument("--input", required=True, type=Path, help="Absolute path to a saved .LS file")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=100)

    try:
        args = parser.parse_args(argv)
        if sys.version_info < (3, 11):
            raise MiddlewareError("runtime_unsupported", "Python 3.11 or newer is required.", 69)
        if args.offset < 0 or not 1 <= args.limit <= MAX_PAGE_SIZE:
            raise MiddlewareError("usage", "Use offset >= 0 and limit between 1 and 500.", 64)
        with admit_job():
            result = positions(args.input, args.offset, args.limit)
            return 0, json.dumps(result, allow_nan=False)
    except MiddlewareError as error:
        code, message, status = error.code, str(error), error.exit_code
    except OSError:
        code, message, status = "input_unavailable", "Could not read the specified .LS file.", 66
    except (ValueError, OverflowError):
        code, message, status = "invalid_listing", "The .LS positions could not be represented as JSON.", 65
    return status, json.dumps({"ok": False, "error": {"code": code, "message": message}})


def main() -> int:
    status, output = invoke(sys.argv[1:])
    print(output)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
