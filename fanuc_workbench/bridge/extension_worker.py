"""Small standard-library worker bootstrap. Feature code loads only after activation."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path


MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--host-root", required=True, type=Path)
    args = parser.parse_args()
    wire = sys.stdout.buffer

    def emit(value):
        payload = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        if len(payload) > MAX_RESPONSE:
            raise ValueError("Worker response exceeds its bound")
        wire.write(payload + b"\n")
        wire.flush()

    def receive():
        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if not line:
            return None
        if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
            raise ValueError("Worker request exceeds its bound")
        return json.loads(line)

    emit({"protocol": 1, "boot_pid": os.getpid()})
    if receive() != {"command": "activate"}:
        return
    root = args.release.resolve()
    raw = (root / "extension.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.manifest_sha256:
        raise ValueError("Worker manifest changed")
    manifest = json.loads(raw)
    for relative, digest in manifest["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Worker support bytes changed")
    sys.path.insert(0, str(args.host_root.resolve()))
    module_name = "_fanuc_feature_" + args.manifest_sha256
    spec = importlib.util.spec_from_file_location(module_name, root / manifest["entrypoint"],
                                                 submodule_search_locations=[str(root)])
    if spec is None or spec.loader is None:
        raise ValueError("No feature loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
        if not callable(getattr(module, "handle", None)):
            raise ValueError("Feature needs a handle function")
        if hasattr(module, "self_test") and module.self_test() is False:
            raise ValueError("Feature self-test failed")
    operations = {row["id"] for row in manifest["operations"]}
    emit({"protocol": 1, "ready": True, "manifest_sha256": args.manifest_sha256,
          "operations": sorted(operations), "pid": os.getpid()})
    while True:
        request = receive()
        if request is None or request.get("command") == "shutdown":
            return
        identifier = request.get("request_id")
        try:
            if request.get("operation_id") not in operations:
                raise ValueError("Unknown feature operation")
            with contextlib.redirect_stdout(sys.stderr):
                result = module.handle(request["operation_id"], request["arguments"], request["context"])
            emit({"request_id": identifier, "result": result})
        except Exception as exc:
            kind = type(exc).__name__
            emit({"request_id": identifier, "error_type": kind if re.fullmatch(r"[A-Za-z0-9_]{1,64}", kind) else "WorkerError"})


if __name__ == "__main__":
    main()
