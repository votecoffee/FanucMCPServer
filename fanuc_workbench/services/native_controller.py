"""Process-isolated native API client shared by GUI and bridge workflows."""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

from fanuc_workbench.bridge.extension_process import RpcProcess


class NativeApiError(RuntimeError):
    def __init__(self, operation, response):
        self.details = {"operation": operation, **{key: response[key] for key in
            ("error_type", "hresult", "reason", "vendor_call") if key in response}}
        super().__init__(f"Native API operation failed ({self.details}); inspect controller state before retrying a change")


class NativeControllerService:
    def __init__(self, profile):
        if sys.platform != "win32":
            raise RuntimeError("The FRRobot provider requires Windows and a licensed FANUC Robot Object installation")
        if importlib.util.find_spec("pythoncom") is None:
            raise RuntimeError("Native FANUC access needs pywin32; install this version's FanucMCPServer dependencies")
        bootstrap = Path(__file__).resolve().parents[1] / "bridge" / "native_worker.py"
        self.worker = RpcProcess([sys.executable, "-B", str(bootstrap), "--host", profile.host],
            {"provider": "frrobot"}, operation_timeout=min(120, max(10, profile.timeout * 3)))

    def call(self, operation, **arguments):
        try:
            result = self.worker.call(operation, arguments, {})
        except RuntimeError:
            raise NativeApiError(operation, {"error_type": "WorkerFailure",
                "reason": "Core native worker failed or timed out; no call was retried"}) from None
        if result.get("ok") is not True:
            raise NativeApiError(operation, result)
        return result["result"]

    def close(self):
        self.worker.close()
