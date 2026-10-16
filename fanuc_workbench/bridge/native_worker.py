"""Core vendor worker. Fixed dispatch only; never imports operator extensions."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    args = parser.parse_args()
    wire = sys.stdout.buffer
    def send(value):
        data = json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode()
        if len(data) >= 4 * 1024 * 1024:
            raise ValueError("Native result exceeds its bound")
        wire.write(data + b"\n")
        wire.flush()
    def receive():
        data = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not data:
            return None
        if len(data) > 1024 * 1024 or not data.endswith(b"\n"):
            raise ValueError("Native request exceeds its bound")
        return json.loads(data)
    send({"protocol": 1, "boot_pid": os.getpid()})
    if receive() != {"command": "activate"}:
        return
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from fanuc_workbench.services.frrobot import FrRobot
    import pythoncom
    pythoncom.CoInitialize()
    robot = None
    send({"protocol": 1, "ready": True, "pid": os.getpid(), "provider": "frrobot"})
    try:
        while True:
            request = receive()
            if request is None or request.get("command") == "shutdown":
                return
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    robot = robot or FrRobot(args.host)
                    operations = {"status": robot.status, "sample": robot.sample, "read_register": robot.read_register,
                                  "programs": robot.programs, "write_register": robot.write_register, "load_program": robot.load_program}
                    operation = operations.get(request["operation_id"])
                    if operation is None:
                        raise ValueError("Unsupported core vendor operation")
                    result = operation(**request["arguments"])
                result = {"ok": True, "result": result}
            except Exception as exc:
                result = {"ok": False, "error_type": type(exc).__name__, "hresult": getattr(exc, "hresult", None)}
            send({"request_id": request["request_id"], "result": result})
    finally:
        robot = None
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
