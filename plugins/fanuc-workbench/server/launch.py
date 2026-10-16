"""Launch the explicitly installed runtime; never install or connect on startup."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    settings = root / "runtime.json"
    if not settings.is_file():
        raise SystemExit("FANUC Workbench runtime is not configured. Run the bundle setup.py before installing this local plugin.")
    data = json.loads(settings.read_text(encoding="utf-8"))
    python, config = Path(data["python"]), Path(data["config"])
    if not python.is_file() or not config.is_file():
        raise SystemExit("FANUC Workbench runtime/config is missing. Repair the local setup; no controller connection was attempted.")
    arguments = [str(python), "-B", "-m", "fanuc_workbench.bridge.mcp_server", "--config", str(config)]
    # Windows CRT execv joins arguments without adding quotes. Preserve spaces
    # and embedded quotes using the same quoting rules as subprocess/CreateProcess.
    if os.name == "nt":
        arguments = [subprocess.list2cmdline([argument]) for argument in arguments]
    os.execv(str(python), arguments)


if __name__ == "__main__":
    main()
