"""Activated, fixed metadata worker. No MCP host or native software is started."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import threading


SAFE_ERRORS = frozenset({
    "Select the settings.json for this app-host runtime",
    "Reconcile retained work or recovery before changing app-host settings",
    "The policy changed since loading; reload it before saving",
    "Only app-host request limit, port and token are editable",
    "Stop the host before changing its registered policy",
    "Host policy validation/save failed; inspect retained work before saving",
})
_input_lock = threading.Lock()


def run_policy(request):
    from fanuc_workbench.bridge.policy_editor import _HOST_CODE
    output = io.StringIO()
    with _input_lock:
        original = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(request, allow_nan=False))
            with contextlib.redirect_stdout(output):
                exec(_HOST_CODE, {"__name__": "_fanuc_fixed_policy"})
            return {"ok": True, "value": json.loads(output.getvalue())}
        except Exception as exc:
            # Never echo a validation payload, token, path or arbitrary exception.
            message = str(exc)
            if type(exc) is ValueError and message in SAFE_ERRORS:
                pass
            elif isinstance(exc, RuntimeError) and ("already running" in message or "already held" in message):
                message = "Stop the host before changing its registered policy"
            else:
                message = "Host policy validation/save failed; inspect retained work before saving"
            return {"ok": False, "message": message}
        finally:
            sys.stdin = original


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from fanuc_workbench.bridge.process_identity import current_creation_time
    def send(value):
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) >= 4 * 1024 * 1024:
            raise ValueError("Policy result exceeds its byte limit")
        sys.stdout.buffer.write(raw + b"\n")
        sys.stdout.buffer.flush()
    def receive():
        raw = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not raw:
            return None
        if len(raw) > 1024 * 1024 or not raw.endswith(b"\n"):
            raise ValueError("Policy request exceeds its byte limit")
        return json.loads(raw)
    send({"protocol": 1, "boot_pid": os.getpid(), "creation_time": current_creation_time()})
    if receive() != {"command": "activate"}:
        return
    send({"protocol": 1, "ready": True, "pid": os.getpid(), "provider": "host-policy"})
    while True:
        request = receive()
        if request is None or request.get("command") == "shutdown":
            return
        result = run_policy(request["arguments"]) if request["operation_id"] == "policy" else {
            "ok": False, "message": "Host policy validation/save failed; inspect retained work before saving"}
        send({"request_id": request["request_id"], "result": result})


if __name__ == "__main__":
    main()
