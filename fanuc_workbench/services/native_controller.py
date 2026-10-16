"""Process-isolated native API client shared by GUI and bridge workflows."""
from __future__ import annotations

import sys
from pathlib import Path

from fanuc_workbench.bridge.extension_process import RpcProcess


class NativeControllerService:
    def __init__(self, profile):
        if sys.platform != "win32":
            raise RuntimeError("The FRRobot provider requires Windows and a licensed FANUC Robot Object installation")
        bootstrap = Path(__file__).resolve().parents[1] / "bridge" / "native_worker.py"
        self.worker = RpcProcess([sys.executable, "-B", str(bootstrap), "--host", profile.host],
            {"provider": "frrobot"}, operation_timeout=min(120, max(10, profile.timeout * 3)))

    def call(self, operation, **arguments):
        result = self.worker.call(operation, arguments, {})
        if result.get("ok") is not True:
            kind = result.get("error_type", "Unavailable")
            code = result.get("hresult")
            suffix = f"; HRESULT {code}" if type(code) is int else ""
            reason = result.get("reason", "") if kind == "ValueError" else ""
            raise RuntimeError(f"Native API operation failed ({kind}{suffix}). {reason} Inspect controller state before retrying a change")
        return result["result"]

    def close(self):
        self.worker.close()
