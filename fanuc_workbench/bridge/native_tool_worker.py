"""Private offline tool IPC; compiler code loads only after owner activation."""
import base64
import json
import os
from pathlib import Path
import sys


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from fanuc_workbench.bridge.process_identity import current_creation_time

    def send(value):
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) >= 4 * 1024 * 1024:
            raise ValueError("Native tool response exceeds its byte bound")
        sys.stdout.buffer.write(raw + b"\n")
        sys.stdout.buffer.flush()

    def receive():
        raw = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not raw:
            return None
        if len(raw) > 1024 * 1024 or not raw.endswith(b"\n"):
            raise ValueError("Native tool request exceeds its byte bound")
        return json.loads(raw)

    send({"protocol": 1, "boot_pid": os.getpid(), "creation_time": current_creation_time()})
    if receive() != {"command": "activate"}:
        return
    from fanuc_workbench.services.native_validation import _run_process
    from fanuc_workbench.bridge.resource_context import close_resource_context
    send({"protocol": 1, "ready": True, "pid": os.getpid(), "provider": "native-tools"})
    try:
        while True:
            request = receive()
            if request is None or request.get("command") == "shutdown":
                return
            try:
                if request["operation_id"] != "run" or set(request["arguments"]) != {"arguments", "cwd", "timeout"}:
                    raise ValueError("Unsupported offline tool operation")
                options = request["arguments"]
                state, output = _run_process(options["arguments"], options["cwd"], options["timeout"], contained=True)
                send({"request_id": request["request_id"], "result": {
                    "state": state, "output_base64": base64.b64encode(output).decode("ascii")}})
            except Exception as exc:
                send({"request_id": request["request_id"], "error_type": type(exc).__name__})
    finally:
        close_resource_context()


if __name__ == "__main__":
    main()
