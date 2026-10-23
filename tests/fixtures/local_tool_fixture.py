"""Opt-in harmless file-IPC peer. Only Python sleepers; never executes a tool.

The test launcher supplies a reviewed pure-Python framework wheel and its hash.
This helper is not referenced by any application or MCP dispatch path.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def write(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x") as stream:
        json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--framework-wheel", type=Path, required=True)
    parser.add_argument("--framework-sha256", required=True)
    parser.add_argument("--mode", choices=("complete", "descendants"), default="complete")
    parser.add_argument("--descendant-directory", type=Path)
    args = parser.parse_args()
    if hashlib.sha256(args.framework_wheel.read_bytes()).hexdigest() != args.framework_sha256:
        raise ValueError("Fixture dependency bytes changed")
    sys.path.insert(0, str(args.framework_wheel))
    from universal_tools.windows_resources import ProcessDriver
    driver = ProcessDriver(); identity = driver.capture(os.getpid())
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "close_fds": True, "creationflags": subprocess.CREATE_NO_WINDOW}
    if args.descendant_directory:
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time; time.sleep(120)"], **options)
        write(args.descendant_directory / "descendants.json", {"child": identity,
            "grandchild": driver.capture_handle(child._handle, child.pid)})
        time.sleep(120)
        return
    raw = args.request.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.sha256: raise ValueError("Fixture request bytes changed")
    request = json.loads(raw); folder = args.request.parent
    write(folder / "boot.json", {"nonce": request["nonce"], "identity": identity, "request_sha256": args.sha256})
    deadline = time.monotonic() + 10
    while not (folder / "activate.json").is_file():
        if time.monotonic() > deadline: raise TimeoutError("Fixture activation deadline")
        time.sleep(.01)
    activation = json.loads((folder / "activate.json").read_text())
    if activation["identity"] != identity or activation["nonce"] != request["nonce"]:
        raise ValueError("Fixture activation mismatch")
    if args.mode == "descendants":
        subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
            "--framework-wheel", str(args.framework_wheel), "--framework-sha256", args.framework_sha256,
            "--descendant-directory", str(folder)], **options)
        while not (folder / "descendants.json").is_file():
            if time.monotonic() > deadline: raise TimeoutError("Fixture descendants deadline")
            time.sleep(.01)
        write(folder / "fixture-ready.json", {"identity": identity, "never_native": True})
    else:
        write(folder / "result.json", {"nonce": request["nonce"], "identity": identity,
            "request_sha256": args.sha256, "state": {"returncode": 0, "synthetic_fixture": True},
            "output_base64": base64.b64encode(b"harmless fixture only").decode()})
    time.sleep(120)  # The exact private owner job closes this inert wait.


if __name__ == "__main__":
    main()
