"""Bounded worker IPC and process cleanup, independent of MCP and robot services."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fanuc_workbench.bridge.extension_release import MAX_MESSAGE, MAX_REQUEST, json_bytes


class WindowsJob:
    """Close kills only the worker processes explicitly attached to this job."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        class Basic(c.Structure):
            _fields_ = [("user", c.c_longlong), ("job_user", c.c_longlong), ("flags", w.DWORD),
                        ("minimum", c.c_size_t), ("maximum", c.c_size_t), ("active", w.DWORD),
                        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(key, c.c_ulonglong) for key in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(c.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", c.c_size_t),
                        ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
        for name, restype, argtypes in [
            ("CreateJobObjectW", w.HANDLE, [c.c_void_p, w.LPCWSTR]),
            ("SetInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]),
            ("OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
            ("AssignProcessToJobObject", w.BOOL, [w.HANDLE, w.HANDLE]),
            ("IsProcessInJob", w.BOOL, [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]),
            ("CloseHandle", w.BOOL, [w.HANDLE]),
        ]:
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = restype, argtypes
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Worker job could not be created")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            self.close()
            raise OSError("Worker cleanup limit could not be set")

    def attach(self, pid):
        from ctypes import wintypes as w
        process = self.kernel.OpenProcess(0x1101, False, pid)  # query, set quota, terminate
        if not process:
            raise OSError("Worker process could not be attached")
        try:
            member = w.BOOL()
            if not self.kernel.IsProcessInJob(process, self.handle, self.c.byref(member)):
                raise OSError("Worker job membership is unavailable")
            if not member.value and not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise OSError(self.c.get_last_error(), "Worker process cleanup could not be attached")
        finally:
            self.kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class FeatureWorker:
    def __init__(self, release: Path, manifest: dict, checksum: str, python: str,
                 *, startup_timeout=15.0, operation_timeout=30.0):
        self.manifest, self.checksum = manifest, checksum
        self.lock = threading.Lock()
        self.inbox = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        self.broken = False
        self.job = None
        self.process = None
        self.pid = None
        self.operation_timeout = operation_timeout
        # Worker code is trusted local code; avoid accidentally forwarding secrets.
        allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LOCALAPPDATA",
                   "APPDATA", "USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH", "COMSPEC", "LANG", "LC_ALL"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONUTF8="1")
        bootstrap = Path(__file__).with_name("extension_worker.py")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            self.process = subprocess.Popen([python, "-B", str(bootstrap), "--release", str(release),
                "--manifest-sha256", checksum, "--host-root", str(Path(__file__).resolve().parents[2])],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=environment, cwd=release, **options)
            self.reader = threading.Thread(target=self._read, daemon=True, name="fanuc-feature-ipc")
            self.reader.start()
            boot = self._receive(startup_timeout)
            self.pid = boot.get("boot_pid")
            if boot.get("protocol") != 1 or type(self.pid) is not int or self.pid < 1:
                raise ValueError("Worker bootstrap protocol failed")
            if os.name == "nt":
                self.job = WindowsJob()
                # A Windows venv launcher may have a second Python process.
                # Attach the real bootstrap, before it can load feature code;
                # nesting both launcher and child can be denied by Windows.
                self.job.attach(self.pid)
            elif os.getpgid(self.pid) != self.process.pid:
                raise ValueError("Worker is outside its owned process group")
            self._send({"command": "activate"})
            ready = self._receive(startup_timeout)
            if (ready.get("protocol") != 1 or ready.get("ready") is not True or ready.get("pid") != self.pid
                    or ready.get("manifest_sha256") != checksum
                    or ready.get("operations") != sorted(row["id"] for row in manifest["operations"])):
                raise ValueError("Worker feature handshake failed")
        except Exception:
            self.close()
            raise RuntimeError("Extension worker failed its startup or self-test; active features were not replaced") from None

    @property
    def healthy(self):
        return not self.broken and not self.stopped.is_set() and self.process is not None and self.process.poll() is None

    def _read(self):
        try:
            while not self.stopped.is_set():
                line = self.process.stdout.readline(MAX_MESSAGE + 1)
                if not line or len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                    break
                value = json.loads(line)
                while not self.stopped.is_set():
                    try:
                        self.inbox.put(value, timeout=.1)
                        break
                    except queue.Full:
                        continue
        except Exception:
            pass
        finally:
            self.broken = True
            try:
                self.inbox.put_nowait(None)
            except queue.Full:
                pass

    def _send(self, value):
        payload = json_bytes(value)
        if len(payload) >= MAX_REQUEST:
            raise ValueError("Worker request exceeds its bound")
        self.process.stdin.write(payload + b"\n")
        self.process.stdin.flush()

    def _receive(self, timeout):
        try:
            value = self.inbox.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError("Extension worker timed out") from None
        if not isinstance(value, dict):
            raise RuntimeError("Extension worker stopped or returned an invalid response")
        return value

    def call(self, operation_id, arguments, context):
        with self.lock:
            if not self.healthy:
                raise RuntimeError("Extension worker is unavailable; reload extensions before another call")
            request_id = uuid.uuid4().hex
            try:
                sent, failures = threading.Event(), []
                def write():
                    try:
                        self._send({"request_id": request_id, "operation_id": operation_id,
                                    "arguments": arguments, "context": context})
                    except Exception:
                        failures.append(True)
                    finally:
                        sent.set()
                deadline = time.monotonic() + self.operation_timeout
                threading.Thread(target=write, daemon=True, name="fanuc-feature-send").start()
                if not sent.wait(self.operation_timeout) or failures:
                    raise RuntimeError("Worker input could not be delivered")
                response = self._receive(max(.001, deadline - time.monotonic()))
                if response.get("request_id") != request_id:
                    raise RuntimeError("Worker response did not match the operation")
            except Exception:
                self._stop()
                raise RuntimeError("Extension worker failed or timed out; no call was retried") from None
            if "error_type" in response:
                # Extension exception messages may contain source data; do not return them.
                raise ValueError("Extension operation failed; inspect the selected feature and input")
            if set(response) != {"request_id", "result"}:
                self._stop()
                raise RuntimeError("Extension worker returned an invalid envelope")
            return response["result"]

    def _stop(self):
        self.broken = True
        self.stopped.set()
        if self.process is None:
            return
        # Kill the owned tree before closing buffered pipes: a blocked writer
        # must be released before Python can acquire that pipe's buffer lock.
        try:
            if self.job is not None:
                self.job.close()
            elif os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif self.process.poll() is None:
                # Startup failed before activation/job attachment; only the owned bootstrap runs.
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        finally:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            if self.process.stdout:
                self.process.stdout.close()

    def close(self):
        with self.lock:
            if not self.stopped.is_set():
                if self.process is not None and self.healthy:
                    try:
                        self._send({"command": "shutdown"})
                    except OSError:
                        pass
                    try:
                        self.process.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        pass
                self._stop()
