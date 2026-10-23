"""One private MakeTP/PrintTP job, atomically contained before Python executes.

This is background utility execution, never an MCP stdio transport. Bounded
file IPC avoids inheriting a client pipe or the kill-on-close job handle.
"""
from contextlib import ExitStack
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

from fanuc_workbench.bridge.storage import atomic_json

APP_ID = "fanuc-local-tools"
MAX_OUTPUT = 1024 * 1024
MAX_PROCESSES = 8
MAX_PRIVATE_BYTES = 512 * 1024 * 1024
_admission = threading.Lock()


def digest(path, maximum=64 * 1024 * 1024):
    path = Path(path)
    if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("Native tool inputs must be ordinary files")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Native tool input exceeds its byte limit")
    return hashlib.sha256(data).hexdigest()


def read_message(path, maximum=2 * 1024 * 1024):
    path = Path(path)
    if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("Native IPC must be an ordinary file")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Native IPC exceeds its byte limit")
    return json.loads(raw)


def validate_request(arguments, cwd, timeout, expected_sha256=None):
    if (not isinstance(arguments, list) or not 1 <= len(arguments) <= 32
            or any(not isinstance(v, str) or "\0" in v or len(v) > 32768 for v in arguments)
            or not Path(arguments[0]).is_absolute()
            or Path(arguments[0]).name.lower() not in {"maketp.exe", "printtp.exe"}):
        raise ValueError("Use an explicit MakeTP/PrintTP executable and bounded arguments")
    if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
        raise ValueError("Use compiler timeout 1..60 seconds")
    folder = Path(cwd).absolute()
    if folder.resolve(strict=True) != folder or not folder.is_dir():
        raise ValueError("Use an ordinary isolated compiler directory")
    actual = digest(Path(arguments[0]))
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("Supply an exact native executable SHA-256")
        if actual != expected_sha256:
            raise ValueError("Native executable differs from the reviewed SHA-256")
    result = {"arguments": arguments, "cwd": str(folder), "timeout": timeout,
              "executable_sha256": actual}
    if len(json.dumps(result).encode("utf-8")) > 60 * 1024:
        raise ValueError("Native tool request exceeds its byte limit")
    return result


def bounded_driver():
    from universal_tools.windows_resources import ProcessDriver

    class Driver(ProcessDriver):
        def create_suspended(self, argv, **options):
            import win32job
            group = super().create_suspended(argv, **options)
            try:
                limits = win32job.QueryInformationJobObject(group.job, win32job.JobObjectExtendedLimitInformation)
                limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
                limits["JobMemoryLimit"] = MAX_PRIVATE_BYTES
                win32job.SetInformationJobObject(group.job, win32job.JobObjectExtendedLimitInformation, limits)
                return group  # Still suspended; the common owner binds before resume.
            except BaseException:
                group.close()
                raise
    return Driver()


class LocalToolJob:
    def __init__(self, registry=None):
        from universal_tools.resources import ResourceLedger
        self.ledger = ResourceLedger(registry, driver=bounded_driver(), max_active_per_app=1)
        self.owner = None
        self.launch = None
        self.inventory_id = None

    def run(self, arguments, cwd, timeout, *, expected_sha256=None):
        request = validate_request(arguments, cwd, timeout, expected_sha256)
        if os.name != "nt":
            raise RuntimeError("Contained local FANUC utilities currently require Windows")
        if not _admission.acquire(blocking=False):
            raise RuntimeError("A local native utility is already active")
        folder = None
        try:
            from universal_tools.instance_lock import InstanceLock
            from universal_tools.management import private_directory
            from fanuc_workbench.bridge.owned_workers import workers
            private_directory(self.ledger.directory)
            lock = InstanceLock(self.ledger.directory / "fanuc-local-tools.lock")
            with lock:
                if any(row.app_id == APP_ID and row.state not in {"released", "terminated", "failed"}
                       for row in self.ledger.records()):
                    raise RuntimeError("A retained native tool outcome requires inspection; no automatic replay")
                self.owner = self.ledger.owner(APP_ID)
                self.inventory_id = workers.reserve(self, "native-tool-worker")
                folder = Path(cwd) / ("native-tool-" + uuid.uuid4().hex)
                folder.mkdir()
                private_directory(folder)
                request.update(nonce=uuid.uuid4().hex, protocol=1)
                atomic_json(folder / "request.json", request)
                request_sha = digest(folder / "request.json", 64 * 1024)
                atomic_json(folder / "receipt.json", {"state": "launching", "request_sha256": request_sha})
                environment = {k: v for k, v in os.environ.items() if k.upper() in {
                    "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LOCALAPPDATA",
                    "APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "COMSPEC"}}
                environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONUTF8="1")
                self.launch = self.owner.launch(self.worker_command(folder, request_sha), "FANUC local utility " + folder.name,
                    allow_termination=True, max_processes=MAX_PROCESSES, show_window=False,
                    cwd=str(folder), env=environment)
                deadline = time.monotonic() + 10
                self._await(folder / "boot.json", deadline)
                boot = read_message(folder / "boot.json", 65536)
                if boot.get("nonce") != request["nonce"] or boot.get("request_sha256") != request_sha:
                    raise ValueError("Native worker bootstrap does not match the request")
                identity = boot["identity"]
                with ExitStack() as handles:
                    retained = handles.enter_context(self.ledger.driver.open(identity))
                    record = self.launch.refresh()
                    if identity not in [member.model_dump(mode="json") for member in record.members]:
                        raise ValueError("Native worker is outside its exact private job")
                    expected_images = {os.path.normcase(str(Path(sys.executable).resolve())),
                                       os.path.normcase(str(Path(sys._base_executable).resolve()))}
                    if identity["executable"] not in expected_images:
                        raise ValueError("Unexpected native worker executable")
                    workers.update(self.inventory_id, "running", pid=identity["pid"],
                        creation_time=int(identity["created"]), ledger_resources=[record.resource_id])
                    atomic_json(folder / "activate.json", {"nonce": request["nonce"], "identity": identity,
                        "resource_id": record.resource_id, "owner_id": record.owner_id,
                        "owner": record.owner.model_dump(mode="json")})
                    self._await(folder / "result.json", time.monotonic() + timeout + 10)
                    response = read_message(folder / "result.json")
                    if (response.get("nonce") != request["nonce"] or response.get("identity") != identity
                            or response.get("request_sha256") != request_sha):
                        raise ValueError("Native utility response identity does not match")
                    if "error_type" in response:
                        raise RuntimeError("Native utility failed; inspect the retained local receipt")
                    output = base64.b64decode(response["output_base64"], validate=True)
                    if len(output) > MAX_OUTPUT:
                        raise ValueError("Native tool output exceeds its byte limit")
                    result = response["state"], output
                cleanup = self.launch.close()
                if cleanup["outcome"] not in {"terminated", "already_exited"}:
                    raise RuntimeError("Native utility cleanup is uncertain; inspect without retry")
                atomic_json(folder / "receipt.json", {"state": "completed", "request_sha256": request_sha,
                    "resource_id": self.launch.resource_id, "cleanup": cleanup, "result": response["state"]})
                workers.release(self.inventory_id)
                self.inventory_id = None
                self.owner.close()
                return result
        except BaseException:
            cleanup = None
            if self.launch is not None:
                try:
                    cleanup = self.launch.close()  # Exact owned job only; no PID-tree guessing.
                except Exception as exc:
                    cleanup = {"outcome": "uncertain", "error_type": type(exc).__name__}
            if folder is not None:
                atomic_json(folder / "receipt.json", {"state": "uncertain", "never_replay": True,
                    "resource_id": self.launch.resource_id if self.launch else None, "cleanup": cleanup})
            if self.inventory_id is not None:
                if cleanup and cleanup["outcome"] in {"terminated", "already_exited"}:
                    workers.release(self.inventory_id)
                    self.inventory_id = None
                else:
                    workers.update(self.inventory_id, "uncertain")
            raise
        finally:
            _admission.release()

    def _await(self, path, deadline):
        while not path.is_file():
            if time.monotonic() >= deadline:
                raise TimeoutError("Native utility deadline exceeded; no operation was retried")
            handle = self.launch.group.info.process
            state = self.ledger.driver.kernel.WaitForSingleObject(handle, 0)
            if state == 0:
                raise RuntimeError("Native utility owner exited without its receipt")
            if state != 258:
                raise OSError("Native utility root handle status is unknown")
            time.sleep(.02)

    def worker_command(self, folder, request_sha):
        return [sys.executable, "-I", "-B", "-m", "fanuc_workbench.bridge.local_tool_worker",
                "--request", str(folder / "request.json"), "--sha256", request_sha]


def run_tool(arguments, cwd, timeout, *, expected_sha256=None, registry=None):
    return LocalToolJob(registry).run(arguments, cwd, timeout, expected_sha256=expected_sha256)
