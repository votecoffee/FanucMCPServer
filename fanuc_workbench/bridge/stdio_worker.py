"""Inert stdio bootstrap: no MCP/native work before the owner's activation."""
import json
import os
from pathlib import Path
import runpy
import sys


def read_activation(read=None):
    # Do not prefetch MCP bytes into sys.stdin.buffer: the SDK may duplicate
    # fd 0 into a new stream when it claims stdio after this bootstrap.
    read = read or os.read
    value = bytearray()
    while len(value) < 4096:
        chunk = read(0, 1)
        if not chunk:
            return False
        value.extend(chunk)
        if chunk == b"\n":
            return bytes(value) == b'{"activate":"fanuc-stdio-v1"}\n'
    return False


def main():
    from fanuc_workbench.bridge.process_identity import current_creation_time
    boot = {"fanuc_stdio_bootstrap": 1, "pid": os.getpid(), "creation_time": current_creation_time()}
    sys.stdout.buffer.write(json.dumps(boot).encode() + b"\n")
    sys.stdout.buffer.flush()
    if not read_activation():
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--entry":
        entry = Path(sys.argv[2]).resolve(strict=True)
        # A verified app-host deployment must import its own pinned package,
        # rather than retain this installed bootstrap's package namespace.
        for name in tuple(sys.modules):
            if name == "fanuc_workbench" or name.startswith("fanuc_workbench."):
                sys.modules.pop(name)
        sys.argv = [str(entry)]
        runpy.run_path(str(entry), run_name="__main__")
    else:
        from fanuc_workbench.bridge.mcp_server import main as run_mcp
        run_mcp()


if __name__ == "__main__":
    main()
