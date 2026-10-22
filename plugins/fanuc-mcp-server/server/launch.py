"""Launch the explicitly installed runtime; never install or connect on startup."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    settings = root / "runtime.json"
    if not settings.is_file():
        raise SystemExit("FanucMCPServer runtime is not configured. Run the bundle setup.py before installing this local plugin.")
    data = json.loads(settings.read_text(encoding="utf-8"))
    python, config = Path(data["python"]), Path(data["config"])
    if not python.is_file() or not config.is_file():
        raise SystemExit("FanucMCPServer runtime/config is missing. Repair the local setup; no controller connection was attempted.")
    arguments = [str(python), "-B", "-m", "fanuc_workbench.bridge.mcp_server", "--config", str(config),
                 "--management-runtime", str(settings)]
    # Keep the launcher alive for the actual runtime. Windows CRT exec overlay
    # can detach launcher/stub descendants from the client's tracked process.
    # An explicit non-inherited stdin writer gives the worker EOF even if this
    # launcher dies, instead of leaving an unattended engineering process.
    child = subprocess.Popen(arguments, stdin=subprocess.PIPE, close_fds=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))

    def input_pump():
        try:
            read = getattr(sys.stdin.buffer, "read1", sys.stdin.buffer.read)
            while data := read(65536):
                child.stdin.write(data)
                child.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                child.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    threading.Thread(target=input_pump, name="fanuc-stdin-owner", daemon=True).start()
    # EOF is an admission fence, not proof that native work stopped. The runtime
    # owns its normal bridge/job drain; never guess a timeout and kill its PID.
    raise SystemExit(child.wait())


if __name__ == "__main__":
    main()
