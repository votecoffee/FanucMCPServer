"""Prepare/select an app-owned offline MCP runtime without editing Codex settings."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fanuc_workbench.services.stable_install import main

if __name__ == "__main__":
    raise SystemExit(main())
