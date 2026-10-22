"""Owned empty-controller processes only: EOF and parent loss must drain the worker."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from universal_tools.management import ManagementClient, ManagementRegistry


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher/CRT process lifetime regression")
@pytest.mark.parametrize("parent_loss", [False, True])
def test_launcher_owns_stdin_and_actual_worker_exit(tmp_path, parent_loss):
    root = tmp_path / "Plugin With Spaces"
    (root / "server").mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    shutil.copyfile(repo / "plugins/fanuc-mcp-server/server/launch.py", root / "server/launch.py")
    config = tmp_path / "empty.json"
    config.write_text('{"state_dir":"state","source_dir":"edits","robots":{}}')
    (root / "runtime.json").write_text(json.dumps({"python": sys.executable, "config": str(config)}))
    registry = tmp_path / "private-registry"
    environment = {**os.environ, "FANUC_MCP_MANAGEMENT_REGISTRY": str(registry),
                   "FANUC_MCP_INSTANCE_ROLE": "validation", "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([sys.executable, "-B", str(root / "server/launch.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    reader = ThreadPoolExecutor(max_workers=1)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = None
    def exchange(identifier, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while True:
            line = reader.submit(process.stdout.readline).result(timeout=20)
            assert line
            result = json.loads(line)
            if result.get("id") == identifier:
                assert "error" not in result, result
                return result["result"]
    try:
        exchange(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                  "clientInfo": {"name": "owned-lifetime-fixture", "version": "1"}})
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        assert len(exchange(2, "tools/list", {})["tools"]) >= 51
        client = ManagementClient(registry)
        record = ManagementRegistry(registry).records()[0][0]
        status = client.inspect(record)["status"]
        assert status["state"] == "running" and status["active_operations"] == 0
        assert "validation stdio worker" in status["message"]
        # Retain a kernel handle to this exact owned worker, immune to PID reuse.
        handle = kernel.OpenProcess(0x100000 | 0x0001, False, record.pid)
        assert handle
        if parent_loss:
            process.terminate()  # Only this fixture launcher, never app-name/PID scans.
        else:
            process.stdin.close()
        process.wait(timeout=20)
        assert kernel.WaitForSingleObject(handle, 15000) == 0, "Owned worker survived its stdin owner"
        deadline = time.monotonic() + 5
        while True:
            current = next(row for row in client.registry.records()[0] if row.instance_id == record.instance_id)
            if current.stopped:
                break
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert client.inspect(current)["status"]["state"] == "stopped"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if handle:
            if kernel.WaitForSingleObject(handle, 0) == 258:
                kernel.TerminateProcess(handle, 99)  # Owned zero-robot fixture failure cleanup only.
                kernel.WaitForSingleObject(handle, 5000)
            kernel.CloseHandle(handle)
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()
        reader.shutdown(wait=True)
