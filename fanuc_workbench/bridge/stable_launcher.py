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
import zipfile


SCHEMA = 1
# Transport capacity is independent of scarce data execution: 32 x 128 MiB
# replaces 8 x 512 MiB steady-state memory budget. Existing legacy generations
# are preserved and explicitly counted during the bounded transition.
MAX_SESSIONS = 32
FRONTDOOR_BYTES = 128 * 1024 * 1024
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


def member_path(relative):
    if (not isinstance(relative, str) or "\\" in relative or ":" in relative
            or relative.startswith("/") or any(p in {"", ".", ".."} for p in relative.split("/"))
            or any(ord(c) < 32 or c in '<>"|?*' for c in relative)
            or any(p.endswith((".", " ")) for p in relative.split("/"))):
        raise ValueError("Invalid launcher member path")
    return relative


def validate_entries(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20000:
        raise ValueError("Invalid launcher inventory")
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            raise ValueError("Invalid launcher inventory member")
        relative = member_path(entry["path"])
        if (relative.casefold() in names or type(entry["bytes"]) is not int or entry["bytes"] < 0
                or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in entry["sha256"])):
            raise ValueError("Invalid launcher member path")
        names.add(relative.casefold())


def verify(root, entries):
    validate_entries(entries)
    if inventory(root) != entries:
        raise ValueError("Launcher payload inventory/hash mismatch")


def verify_bootstrap_python(root, entries):
    """Verify the selected bootstrap/native subset; retained legacy Lib is unused
    after switching to the hash-bound stdlib archive. Extra native DLLs refuse.
    """
    validate_entries(entries)
    ordinary(root, directory=True)
    bootstrap_names(root, entries)
    for row in entries:
        path = ordinary(root / row["path"])
        if path.stat().st_size != row["bytes"] or digest(path.read_bytes()) != row["sha256"]:
            raise ValueError("Bootstrap interpreter payload mismatch")
    declared = {row["path"] for row in entries if row["path"].startswith("DLLs/")}
    actual = {p.relative_to(root).as_posix() for p in (root / "DLLs").rglob("*") if p.is_file()}
    if declared != actual:
        raise ValueError("Bootstrap native-library inventory differs")


def bootstrap_names(root, entries):
    root = Path(root)
    names = {row["path"].casefold() for row in entries}
    top = {p.name.casefold() for p in root.iterdir() if p.is_file()}
    if ({p for p in names if "/" not in p} != top or not {"python.exe", "pythonw.exe"}.issubset(top)):
        raise ValueError("Bootstrap executable/native inventory differs")
    for module in tuple(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if not file:
            continue
        try:
            relative = Path(file).relative_to(root).as_posix().casefold()
        except ValueError:
            continue
        if relative not in names:
            raise ValueError("Loaded bootstrap import is outside the verified closure")


def pinned_tree(root, entries, *, exact=True):
    """Keep verified Windows bytes open without write/delete sharing until exit.

    Hash the very same handles that prevent subsequent replacement. Importers
    use only the two pinned archives and declared extension files, never a
    directory-wide module search. Directory handles also fence ancestor renames.
    """
    validate_entries(entries)
    if not exact:
        bootstrap_names(root, entries)
    if os.name != "nt":
        if exact:
            verify(root, entries)
        else:
            verify_bootstrap_python(root, entries)
        return
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt
    kernel = c.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    create.restype = w.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [w.HANDLE], w.BOOL
    attributes = kernel.GetFileInformationByHandleEx
    attributes.argtypes, attributes.restype = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL
    class AttributeTag(c.Structure):
        _fields_ = [("attributes", w.DWORD), ("tag", w.DWORD)]
    handles, streams, directories = [], [], set()
    root = ordinary(root, directory=True)
    def pin(path, directory=False):
        # Ancestors must permit sibling publication while denying directory
        # deletion/rename. Only immutable payload files deny write sharing.
        handle = create(str(path), 0x80000000, 3 if directory else 1, None, 3, 0x02200000 if directory else 0x00200000, None)
        if handle == c.c_void_p(-1).value:
            raise OSError(c.get_last_error(), "Verified payload could not be pinned")
        tag = AttributeTag()
        if not attributes(handle, 9, c.byref(tag), c.sizeof(tag)) or tag.attributes & 0x400:
            close(handle)
            raise ValueError("Pinned launcher payload is a reparse point or unverifiable")
        if bool(tag.attributes & 0x10) != directory:
            close(handle)
            raise ValueError("Pinned launcher payload has wrong file type")
        if directory:
            handles.append(handle)
        else:
            stream = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")
            streams.append(stream)
            return stream
    try:
        for parent in reversed((root, *root.parents)):
            pin(parent, True)
            directories.add(parent)
        for row in entries:
            path = root / row["path"]
            for parent in reversed(path.parents):
                if parent not in directories:
                    pin(parent, True)
                    directories.add(parent)
            stream = pin(path)
            if os.fstat(stream.fileno()).st_size != row["bytes"]:
                raise ValueError("Pinned launcher payload inventory/hash mismatch")
            checksum = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(block)
            if checksum.hexdigest() != row["sha256"]:
                raise ValueError("Pinned launcher payload inventory/hash mismatch")
        # Inventory names only; bytes were already hashed from retained handles.
        wanted = {row["path"] for row in entries}
        actual = set()
        scan = root if exact else root / "DLLs"
        for folder, dirs, files in os.walk(scan, followlinks=False):
            for name in dirs:
                path = Path(folder) / name
                info = path.lstat()
                if getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Pinned payload scan contains a reparse point")
            # Every declared file was verified from its retained deny-write/
            # delete handle. Any unlisted file (including a new link) changes
            # this name set and refuses; no second path-based stat is needed.
            actual.update((Path(folder) / name).relative_to(root).as_posix() for name in files)
        if actual != (wanted if exact else {n for n in wanted if n.startswith("DLLs/")}):
            raise ValueError("Pinned payload inventory names differ")
    except BaseException:
        for stream in streams:
            stream.close()
        for handle in handles:
            close(handle)
        raise
    _held.extend(streams)
    _held.extend((kernel, handle) for handle in handles)


def verify_archives(runtime, *, eager_bytecode=True):
    from importlib.util import MAGIC_NUMBER, source_hash
    if parse(read(runtime / "layout.json")) != {"schema_version": 1, "layout": "archive-v1"}:
        raise ValueError("Unsupported archive layout")
    for name in ("dependencies.zip", "stdlib.zip"):
        with zipfile.ZipFile(runtime / name) as archive:
            seen, total = set(), 0
            for row in archive.infolist():
                path = member_path(row.orig_filename.rstrip("/"))
                total += row.file_size
                if (path.casefold() in seen or row.flag_bits & 1 or row.is_dir()
                        or (row.external_attr >> 16) & 0o170000 == 0o120000
                        or row.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or row.file_size > 64 * 1024 * 1024 or total > 256 * 1024 * 1024
                        or Path(path).suffix.lower() in {".pyd", ".dll", ".exe", ".pyo"}):
                    raise ValueError("Unsafe or ambiguous archive member")
                seen.add(path.casefold())
            if not seen or len(seen) > 20000:
                raise ValueError("Invalid archive inventory")
            # Publication validates every pair. A launcher has already hashed
            # and pinned the entire archive; its importer validates a pair
            # again immediately before executing that module. Avoid decoding
            # thousands of unused source files in every idle frontdoor.
            if not eager_bytecode:
                continue
            source_names = set(archive.namelist())
            for row in archive.infolist():
                if row.filename.endswith(".pyc"):
                    source = row.filename[:-1]
                    if source not in source_names:
                        raise ValueError("Archive bytecode lacks paired source")
                    with archive.open(row) as stream:
                        header = stream.read(16)
                    if (header[:4] != MAGIC_NUMBER or header[4:8] != b"\x03\x00\x00\x00"
                            or header[8:16] != source_hash(archive.read(source))):
                        raise ValueError("Archive bytecode source hash or format differs")


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
    if manifest.get("layout") == "archive-v1":
        pinned_tree(root / "python", manifest["python_files"], exact=False)
        pinned_tree(runtime, manifest["runtime_files"])
        verify_archives(runtime, eager_bytecode=False)
    else:
        verify(root / "python", manifest["python_files"])
        verify(runtime, manifest["runtime_files"])
    if digest(read(root / "StartMcp.py", MAX_MANIFEST)) != manifest["launcher_sha256"]:
        raise ValueError("Stable entry hash mismatch")
    return value, manifest, runtime


def publish(root, manifest, *, expected_selection, bootstrap_bytes=None, expected_bootstrap=None):
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
        if before is not None:
            backups = state / "backups"
            backups.mkdir(exist_ok=True)
            ordinary(backups, directory=True)
            backup = backups / (observed + ".json")
            if backup.exists() and read(backup) != before:
                raise ValueError("Launcher backup differs")
            if not backup.exists():
                with backup.open("xb") as stream:
                    stream.write(before); stream.flush(); os.fsync(stream.fileno())
        # Validate without changing the current selection. A failed candidate never
        # restores a stale global pointer over an intervening installation.
        if manifest.get("layout") == "archive-v1":
            verify_bootstrap_python(root / "python", manifest["python_files"])
        else:
            verify(root / "python", manifest["python_files"])
        verify(root / "runtimes" / manifest["runtime"], manifest["runtime_files"])
        if manifest.get("layout") == "archive-v1":
            verify_archives(root / "runtimes" / manifest["runtime"])
        entry = root / "StartMcp.py"
        entry_before = read(entry, MAX_MANIFEST)
        if bootstrap_bytes is not None:
            if digest(entry_before) != expected_bootstrap or digest(bootstrap_bytes) != manifest["launcher_sha256"]:
                raise ValueError("Reviewed bootstrap migration hashes differ")
        elif digest(entry_before) != manifest["launcher_sha256"]:
            raise ValueError("Stable entry hash mismatch")
        if (read(selection) if selection.exists() else None) != before:
            raise ValueError("Launcher selection changed during validation")
        if bootstrap_bytes is not None and entry_before != bootstrap_bytes:
            backups = state / "bootstrap-backups"
            backups.mkdir(exist_ok=True)
            ordinary(backups, directory=True)
            backup = backups / (digest(entry_before) + ".py")
            if backup.exists() and read(backup, MAX_MANIFEST) != entry_before:
                raise ValueError("Retained bootstrap backup differs")
            if not backup.exists():
                with backup.open("xb") as stream:
                    stream.write(entry_before); stream.flush(); os.fsync(stream.fileno())
            atomic(state / "bootstrap-migration.json", encoded({"state": "committing",
                "prior_selection_sha256": observed, "prior_bootstrap_sha256": digest(entry_before),
                "new_snapshot_id": snapshot_id, "new_bootstrap_sha256": digest(bootstrap_bytes)}))
            atomic(entry, bootstrap_bytes)
        atomic(selection, proposed)
        if bootstrap_bytes is not None:
            atomic(state / "bootstrap-migration.json", encoded({"state": "committed", "snapshot_id": snapshot_id}))
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
        # The pinned driver's public open() includes termination permission.
        # Parent watching needs query/synchronization only; never obtain control
        # authority over the SDK/Codex parent merely to observe its lifetime.
        self.parent_handle = driver._open(self.parent["pid"], terminate=False)
        if self.parent_handle.identity != self.parent or not self.parent_handle.alive():
            self.parent_handle.close()
            raise ValueError("Client parent generation changed before its query handle was retained")
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
            active = legacy = 0
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
                    if prior.get("containment", {}).get("commit_bytes", 536870912) > FRONTDOOR_BYTES:
                        legacy += 1
                else:
                    raise RuntimeError("Prior generation identity is unverifiable; reconnect refused")
            if active >= MAX_SESSIONS:
                raise RuntimeError("Offline metadata connection capacity reached (32); close an owning client before reconnecting")
            if legacy > 8:
                raise RuntimeError("Legacy process budget is unverifiable; new connection refused without changing peers")
            self.record["budget"] = {"connections": 32, "new_process_bytes": FRONTDOOR_BYTES,
                "legacy_generations": legacy, "steady_max_bytes": 4294967296,
                "transition_max_bytes": 7516192768}
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
    """One offline server actor, 128 MiB commit, no app-created child capability.

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
    limits.job_memory = FRONTDOOR_BYTES
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


def configure_packages(runtime, manifest=None):
    """Explicit reviewed import/DLL roots; never execute arbitrary .pth files."""
    runtime = Path(runtime)
    if (runtime / "layout.json").is_file():
        if manifest is None:
            raise ValueError("Archive imports require the verified manifest")
        return configure_archives(runtime, manifest)
    packages = runtime / "site-packages"
    sys.path.insert(0, str(packages))
    for relative in ("win32", "win32/lib"):
        path = ordinary(packages / relative, directory=True)
        sys.path.append(str(path))
    _held.append(os.add_dll_directory(str(ordinary(packages / "pywin32_system32", directory=True))))


def configure_archives(runtime, manifest):
    from importlib.machinery import PathFinder, ExtensionFileLoader, EXTENSION_SUFFIXES, ModuleSpec
    from importlib.util import spec_from_file_location
    import zipimport
    import marshal
    import _imp
    native = runtime / "native"
    dependencies = str(runtime / "dependencies.zip")
    standard = str(runtime / "stdlib.zip")
    python = runtime.parents[1] / "python"
    # Standard zipimport reopens the same archive for each source/bytecode read.
    # Reuse two retained readers while keeping its spec/package/resource APIs.
    readers = {name: zipfile.ZipFile(name) for name in (dependencies, standard)}
    _held.extend(readers.values())
    member_names = {name: set(reader.namelist()) for name, reader in readers.items()}
    namespace_dirs = {archive: {"/".join(name.split("/")[:i]) + "/" for name in names
        for i in range(1, len(name.split("/")))} for archive, names in member_names.items()}
    class PinnedZipImporter(zipimport.zipimporter):
        def get_data(self, pathname):
            path = pathname.replace("\\", "/")
            prefix = self.archive.replace("\\", "/") + "/"
            if path.startswith(prefix):
                path = path[len(prefix):]
            if path not in member_names[self.archive]:
                raise OSError("Resource is outside the selected archive") from None
            return readers[self.archive].read(path)

        def get_filename(self, fullname):
            tail = fullname.rpartition(".")[2]
            relative = self.prefix.replace("\\", "/") + tail
            relative += "/__init__.py" if self.is_package(fullname) else ".py"
            return self.archive + "/" + relative

        def get_source(self, fullname):
            from importlib._bootstrap_external import decode_source
            return decode_source(self.get_data(self.get_filename(fullname)))

        def get_code(self, fullname):
            from importlib.util import MAGIC_NUMBER, source_hash
            filename = self.get_filename(fullname)
            source = self.get_data(filename)
            key = filename[len(self.archive) + 1:] + "c"
            if key not in member_names[self.archive]:
                return compile(source, filename, "exec", dont_inherit=True)
            raw = self.get_data(filename + "c")
            if (raw[:4] != MAGIC_NUMBER or raw[4:8] != b"\x03\x00\x00\x00"
                    or raw[8:16] != source_hash(source)):
                raise ImportError("Selected archive bytecode differs from its source")
            code = marshal.loads(raw[16:])
            _imp._fix_co_filename(code, filename)
            return code

        def find_spec(self, fullname, target=None):
            spec = super().find_spec(fullname, target)
            if spec is None:
                prefix = self.prefix.replace("\\", "/") + fullname.rpartition(".")[2] + "/"
                if prefix in namespace_dirs[self.archive]:
                    spec = ModuleSpec(fullname, None, is_package=True)
                    spec.submodule_search_locations = [self.archive + "/" + prefix.rstrip("/")]
            return spec

    def archive_hook(path):
        if any(path == name or path.startswith(name + "/") or path.startswith(name + "\\") for name in readers):
            return PinnedZipImporter(path)
        raise ImportError("Not a selected archive")
    sys.path_hooks.insert(0, archive_hook)
    for path in tuple(sys.path_importer_cache):
        if any(path == name or path.startswith(name + "/") or path.startswith(name + "\\") for name in readers):
            sys.path_importer_cache.pop(path, None)
    sys.path[:] = [dependencies, standard, dependencies + "/win32", dependencies + "/win32/lib"]
    extensions = {}
    for base, rows, prefix in ((runtime, manifest["runtime_files"], "native/"),
                               (python, manifest["python_files"], "DLLs/")):
        for row in rows:
            relative = member_path(row["path"])
            if not relative.startswith(prefix):
                continue
            # Bypass pywin32's Python shims: their fallback LoadLibrary search
            # can consult an unselected executable directory or PATH. Import
            # these two Python extension DLLs directly from their pinned files.
            if base == runtime:
                for module in ("pywintypes", "pythoncom"):
                    expected = f"native/pywin32_system32/{module}{sys.version_info.major}{sys.version_info.minor}.dll"
                    if relative == expected:
                        extensions[module] = str(base / relative)
            for suffix in EXTENSION_SUFFIXES:
                if relative.endswith(suffix):
                    name = relative[len(prefix):-len(suffix)].replace("/", ".")
                    if name.startswith(("win32.", "pywin32_system32.")):
                        name = name.split(".", 1)[1]
                    if name in extensions:
                        raise ValueError("Ambiguous native extension import")
                    extensions[name] = str(base / relative)
                    break
    # Packages imported by the stdlib bootstrap must resolve future submodules
    # through the selected archive, not the retained old filesystem Lib tree.
    library = python / "Lib"
    for module in tuple(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if file and hasattr(module, "__path__"):
            try:
                relative = Path(file).relative_to(library).parent.as_posix()
            except ValueError:
                continue
            module.__path__ = [standard + "/" + relative]
            if module.__spec__:
                module.__spec__.submodule_search_locations = module.__path__
    class NativePackageFinder:
        @staticmethod
        def find_spec(fullname, path=None, target=None):
            if fullname in extensions:
                file = extensions[fullname]
                return spec_from_file_location(fullname, file, loader=ExtensionFileLoader(fullname, file))
            # pywin32's loader uses this declared namespace to find its pinned
            # DLLs. Its directory is never searched for arbitrary Python code.
            if fullname == "pywin32_system32":
                spec = ModuleSpec(fullname, None, is_package=True)
                spec.submodule_search_locations = [str(native / "pywin32_system32")]
                return spec
            allowed = [p for p in (sys.path if path is None else path)
                       if p == dependencies or p.startswith(dependencies + "/") or p.startswith(dependencies + "\\")
                       or p == standard or p.startswith(standard + "/") or p.startswith(standard + "\\")]
            return PathFinder.find_spec(fullname, allowed, target)

        find_distributions = staticmethod(PathFinder.find_distributions)
    sys.meta_path[:] = [NativePackageFinder() if item is PathFinder else item for item in sys.meta_path]
    _held.append(os.add_dll_directory(str(native / "pywin32_system32")))
    os.environ["FANUC_MCP_GUIDANCE_ROOT"] = str(runtime / "guidance")


def main(root=None):
    root = Path(root) if root is not None else Path(__file__).absolute().parent
    generation = None
    try:
        contain_current()  # Bound preflight itself; no app children may execute.
        migration = root / "launcher/bootstrap-migration.json"
        if migration.exists() and parse(read(migration)).get("state") != "committed":
            raise RuntimeError("Reviewed launcher publication is incomplete; metadata-only publication recovery is required")
        selection, manifest, runtime = selected(root)
        # Keep the original wire handles and SDK 2.3 stream-claim behavior. This
        # windowless base interpreter is the surviving server, not a short wrapper.
        configure_packages(runtime, manifest)
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
        generation.record["containment"] = {"actor_limit": 1, "commit_bytes": FRONTDOOR_BYTES, "kill_on_owner_exit": True}
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
