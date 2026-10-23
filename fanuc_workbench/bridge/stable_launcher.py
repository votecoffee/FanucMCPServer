"""Installed stdlib-only entry for hash-selected, in-process offline MCP generations.

Copied as StartMcp.py beside the app-owned Python installation. No checkout,
plugin-cache, package import, subprocess or network lookup is needed to select
a generation. Publication is an explicit installer operation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid


SCHEMA = 1
MAX_SESSIONS = 8
MAX_RECORDS = 4096
MAX_MANIFEST = 8 * 1024 * 1024
_held = []


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()


def ordinary(path, *, directory=False):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Launcher paths must not contain links or reparse points")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError("Launcher path has the wrong file type")
    return path


def read(path, maximum=65536):
    ordinary(path)
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Launcher metadata exceeds its limit")
    return raw


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate launcher metadata key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def atomic(path, raw):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".fanuc-publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(path):
    """Bounded, kernel-released serialization; never delete another writer's lock."""
    from time import monotonic, sleep
    path = Path(path)
    if path.exists():
        ordinary(path)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0"); stream.flush()
        deadline = monotonic() + 5
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if monotonic() >= deadline:
                    raise RuntimeError("Launcher publication/admission is busy; reconnect later") from None
                sleep(.02)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def inventory(root):
    import stat
    ordinary(root, directory=True)
    entries = []
    root = Path(root)
    # Verify each ancestor once and every descendant before traversal/read. This
    # avoids O(files * path depth) Windows metadata calls on each cold launch.
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            path = Path(folder) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Launcher inventory must not contain links or reparse points")
            if name in directories:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("Launcher directory changed during verification")
            elif stat.S_ISREG(info.st_mode):
                entries.append({"path": path.relative_to(root).as_posix(), "sha256": digest(path.read_bytes()), "bytes": info.st_size})
            else:
                raise ValueError("Launcher inventory contains an unsupported file type")
    return sorted(entries, key=lambda entry: entry["path"].casefold())


def verify(root, entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20000:
        raise ValueError("Invalid launcher inventory")
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            raise ValueError("Invalid launcher inventory member")
        relative = entry["path"]
        if (not isinstance(relative, str) or "\\" in relative or ":" in relative
                or relative.startswith("/") or any(p in {"", ".", ".."} for p in relative.split("/"))
                or relative.casefold() in names):
            raise ValueError("Invalid launcher member path")
        names.add(relative.casefold())
    if inventory(root) != entries:
        raise ValueError("Launcher payload inventory/hash mismatch")


def selected(root):
    root = ordinary(root, directory=True)
    raw = read(root / "launcher/current.json")
    value = parse(raw)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "snapshot_id"}
            or type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA
            or not isinstance(value["snapshot_id"], str) or len(value["snapshot_id"]) != 64
            or any(c not in "0123456789abcdef" for c in value["snapshot_id"])):
        raise ValueError("Invalid launcher selection")
    snapshot = ordinary(root / "launcher/snapshots" / value["snapshot_id"], directory=True)
    manifest_raw = read(snapshot / "manifest.json", MAX_MANIFEST)
    if digest(manifest_raw) != value["snapshot_id"]:
        raise ValueError("Launcher manifest hash mismatch")
    manifest = parse(manifest_raw)
    if (manifest.get("schema_version") != SCHEMA or manifest.get("profile") != "standalone-offline"
            or manifest.get("framework") != "0.20.0" or manifest.get("sdk") != "2.3.0"):
        raise ValueError("Unsupported launcher profile or dependency identity")
    runtime_name = manifest["runtime"]
    # Three numeric, fixed release components; no path selection by newest directory.
    parts = runtime_name.split(".") if isinstance(runtime_name, str) else []
    if len(parts) != 3 or not all(p.isascii() and p.isdigit() for p in parts):
        raise ValueError("Invalid retained runtime identity")
    runtime = ordinary(root / "runtimes" / runtime_name, directory=True)
    verify(root / "python", manifest["python_files"])
    verify(runtime, manifest["runtime_files"])
    if digest(read(root / "StartMcp.py", MAX_MANIFEST)) != manifest["launcher_sha256"]:
        raise ValueError("Stable entry hash mismatch")
    return value, manifest, runtime


def publish(root, manifest, *, expected_selection):
    """CAS publication with exact prior-byte backups. Payloads are never replaced."""
    root = Path(root)
    state = root / "launcher"
    state.mkdir(parents=True, exist_ok=True)
    ordinary(state, directory=True)
    payload = encoded(manifest)
    snapshot_id = digest(payload)
    snapshot = state / "snapshots" / snapshot_id
    snapshot.mkdir(parents=True, exist_ok=True)
    ordinary(snapshot, directory=True)
    path = snapshot / "manifest.json"
    if path.exists():
        if read(path, MAX_MANIFEST) != payload:
            raise ValueError("Retained launcher manifest differs")
    else:
        with path.open("xb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    selection = state / "current.json"
    with locked(state / "publication.lock"):
        before = read(selection) if selection.exists() else None
        observed = digest(before) if before is not None else "absent"
        if observed != expected_selection:
            raise ValueError("Launcher selection changed; publication refused")
        proposed = encoded({"schema_version": SCHEMA, "snapshot_id": snapshot_id})
        if before == proposed:
            return snapshot_id
        if before is not None:
            backups = state / "backups"
            backups.mkdir(exist_ok=True)
            backup = backups / (observed + ".json")
            if backup.exists() and read(backup) != before:
                raise ValueError("Launcher backup differs")
            if not backup.exists():
                with backup.open("xb") as stream:
                    stream.write(before); stream.flush(); os.fsync(stream.fileno())
        # Validate without changing the current selection. A failed candidate never
        # restores a stale global pointer over an intervening installation.
        verify(root / "python", manifest["python_files"])
        verify(root / "runtimes" / manifest["runtime"], manifest["runtime_files"])
        if digest(read(root / "StartMcp.py", MAX_MANIFEST)) != manifest["launcher_sha256"]:
            raise ValueError("Stable entry hash mismatch")
        if (read(selection) if selection.exists() else None) != before:
            raise ValueError("Launcher selection changed during validation")
        atomic(selection, proposed)
        if read(selection) != proposed:
            raise RuntimeError("Launcher selection readback differs; inspect retained evidence")
    return snapshot_id


class OfflineGeneration:
    """Actual surviving process owns its own offline admission/parent-loss receipt.

    No descendant or attached application is discovered/adopted. SDK redirector
    and console helpers remain the SDK client's inventory, not app-created actors.
    Kernel identities, including physical Store paths, come only from handles.
    """
    def __init__(self, root, selection, driver=None):
        if driver is None:
            from universal_tools.windows_resources import ProcessDriver
            driver = ProcessDriver()
        self.root, self.driver = Path(root), driver
        self.directory = self.root / "sessions"
        self.directory.mkdir(exist_ok=True)
        ordinary(self.directory, directory=True)
        self.identity = driver.capture(os.getpid())
        self.parent = driver.capture(os.getppid())
        if int(self.parent["created"]) > int(self.identity["created"]):
            raise ValueError("Client parent generation changed before admission")
        self.parent_handle = driver.open(self.parent)
        self.path = self.directory / (uuid.uuid4().hex + ".json")
        self.record = {"schema_version": SCHEMA, "profile": "standalone-offline", "snapshot": selection,
            "identity": self.identity, "parent": self.parent, "state": "starting",
            "app_created_children": [], "native_intents": False, "reason": "Admitted offline frontdoor"}
        self.stop = threading.Event()
        self.guard = threading.RLock()
        self.thread = None
        with locked(self.directory / "admission.lock"):
            paths = list(self.directory.glob("*.json"))
            if len(paths) >= MAX_RECORDS:
                raise RuntimeError("Offline generation receipt capacity reached; retain and archive receipts before reconnecting")
            active = 0
            for path in paths:
                prior = parse(read(path))
                if prior.get("state") in {"closed", "parent-exited", "reconciled-dead", "failed"}:
                    continue
                if (prior.get("profile") != "standalone-offline" or prior.get("native_intents") is not False
                        or prior.get("app_created_children") != []):
                    raise RuntimeError("Uncertain generation needs app-owned inspection")
                state = driver.state(prior["identity"])
                if state in {"absent", "different"}:
                    atomic(path, encoded({**prior, "state": "reconciled-dead", "reason": "Exact prior generation no longer exists; no children or native intents; no replay"}))
                elif state == "alive":
                    active += 1
                else:
                    raise RuntimeError("Prior generation identity is unverifiable; reconnect refused")
            if active >= MAX_SESSIONS:
                raise RuntimeError("Offline frontdoor capacity reached; close an owning client before reconnecting")
            atomic(self.path, encoded(self.record))

    def write(self, state, reason):
        with self.guard:
            self.record.update(state=state, reason=reason)
            atomic(self.path, encoded(self.record))

    def start(self):
        self.write("active", "Running selected offline generation; no app-created children")
        def watch():
            while not self.stop.wait(.1):
                if self.parent_handle.wait(0):
                    try:
                        self.write("parent-exited", "Exact retained client/SDK parent exited; offline generation stopped; no native replay")
                    finally:
                        os._exit(74)
        self.thread = threading.Thread(target=watch, name="fanuc-client-lifetime", daemon=True)
        self.thread.start()

    def close(self, *, failed=False):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
        self.write("failed" if failed else "closed", "Offline process leaving its owned generation; no native jobs were admitted")
        self.parent_handle.close()


def contain_current():
    """One offline server actor, 512 MiB commit, no app-created child capability.

    Retain the private KILL_ON_JOB_CLOSE job through process exit. Closing this
    handle early would terminate the owning process itself. SDK/client jobs and
    their console helpers are neither adopted nor terminated by this app.
    """
    import ctypes as c
    from ctypes import wintypes as w
    class Basic(c.Structure):
        _fields_ = [("user", c.c_longlong), ("job_user", c.c_longlong), ("flags", w.DWORD),
            ("minimum", c.c_size_t), ("maximum", c.c_size_t), ("active", w.DWORD),
            ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(c.Structure):
        _fields_ = [(name, c.c_ulonglong) for name in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")]
    class Limits(c.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
            ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    for name, result, arguments in (
        ("CreateJobObjectW", w.HANDLE, [c.c_void_p, w.LPCWSTR]),
        ("SetInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]),
        ("GetCurrentProcess", w.HANDLE, []),
        ("AssignProcessToJobObject", w.BOOL, [w.HANDLE, w.HANDLE]),
        ("IsProcessInJob", w.BOOL, [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]),
        ("CloseHandle", w.BOOL, [w.HANDLE]),
    ):
        function = getattr(kernel, name)
        function.restype, function.argtypes = result, arguments
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError("Offline containment job could not be created")
    limits = Limits()
    limits.basic.flags = 0x2000 | 0x8 | 0x200  # KILL_ON_JOB_CLOSE, ACTIVE_PROCESS, JOB_MEMORY
    limits.basic.active = 1
    limits.job_memory = 512 * 1024 * 1024
    if not kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)):
        kernel.CloseHandle(job)
        raise OSError("Offline containment limits could not be set")
    process = kernel.GetCurrentProcess()
    if not kernel.AssignProcessToJobObject(job, process):
        kernel.CloseHandle(job)
        raise OSError("Offline process could not enter its private job")
    _held.append((kernel, job))  # OS closes the job only at actual owner death.
    member = w.BOOL()
    if not kernel.IsProcessInJob(process, job, c.byref(member)) or not member.value:
        raise OSError("Offline containment membership is unverified")


def configure_packages(runtime):
    """Explicit reviewed import/DLL roots; never execute arbitrary .pth files."""
    packages = Path(runtime) / "site-packages"
    sys.path.insert(0, str(packages))
    for relative in ("win32", "win32/lib"):
        path = ordinary(packages / relative, directory=True)
        sys.path.append(str(path))
    _held.append(os.add_dll_directory(str(ordinary(packages / "pywin32_system32", directory=True))))


def main(root=None):
    root = Path(root) if root is not None else Path(__file__).absolute().parent
    generation = None
    try:
        selection, manifest, runtime = selected(root)
        # Keep the original wire handles and SDK 2.3 stream-claim behavior. This
        # windowless base interpreter is the surviving server, not a short wrapper.
        configure_packages(runtime)
        os.chdir(root)
        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("PYTHONHOME", None)
        os.environ.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONUTF8="1",
            FANUC_MCP_STABLE_ROOT=str(root), FANUC_MCP_SNAPSHOT_ID=selection["snapshot_id"])
        from importlib.metadata import version
        for package, expected in (("fanuc-mcp-server-bridge", manifest["package_version"]),
                                  ("mcp", "2.3.0"), ("universal-codex-tools", "0.20.0")):
            if version(package) != expected:
                raise ValueError("Selected package metadata differs from reviewed identity")
        from fanuc_workbench.bridge.config import BridgeConfig
        from fanuc_workbench.bridge.offline_profile import validate
        validate(BridgeConfig.load(runtime / "policy.json"))
        generation = OfflineGeneration(root, selection)
        _held.append(generation)
        if not os.path.samefile(generation.identity["executable"], root / "python/pythonw.exe"):
            raise ValueError("Actual kernel executable differs from the retained windowless interpreter")
        contain_current()
        generation.record["containment"] = {"actor_limit": 1, "commit_bytes": 536870912, "kill_on_owner_exit": True}
        generation.start()
        sys.argv = [str(root / "StartMcp.py"), "--offline", "--config", str(runtime / "policy.json"),
            "--management-runtime", str(runtime / "runtime.json"),
            "--management-registry", manifest["management_registry"], "--instance-role", "per-chat"]
        from fanuc_workbench.bridge.mcp_server import main as serve
        serve()
    except (Exception, SystemExit) as exc:
        if generation:
            generation.close(failed=True)
        # Protocol stdout is never a log destination, including early failure.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError, SystemExit)) else type(exc).__name__
        sys.stderr.write("FANUC stable launcher refused: " + message + "\n")
        return 1
    else:
        generation.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
