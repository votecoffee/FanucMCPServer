"""Bounded real Windows frontend controls with an inert synthetic backend."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import queue

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows original-handle frontend")
SOURCE = Path(__file__).resolve().parents[1]
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
CONFIG = b'<configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" /></startup></configuration>'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(folder):
    return [{"path": p.relative_to(folder).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)}
            for p in sorted(folder.rglob("*")) if p.is_file()]


@pytest.fixture(scope="module")
def executables(tmp_path_factory):
    if not CSC.is_file():
        pytest.skip("Windows C# compiler is unavailable")
    folder = tmp_path_factory.mktemp("native-frontends")
    frontend, backend = folder / "FanucMcpFrontend.exe", folder / "pythonw.exe"
    native = SOURCE / "native/FanucMcpFrontend"
    subprocess.run([str(CSC), "/nologo", "/codepage:65001", "/target:winexe", "/platform:x64", "/optimize+",
        "/reference:System.Web.Extensions.dll", "/out:" + str(frontend),
        *(str(native / p) for p in ("Json.cs", "Windows.cs", "Program.cs", "Idle.cs"))], check=True)
    subprocess.run([str(CSC), "/nologo", "/codepage:65001", "/target:winexe", "/platform:x64", "/out:" + str(backend),
        str(native / "Json.cs"), str(SOURCE / "tests/native/FrontendEchoBackend.cs")], check=True)
    return frontend, backend


@pytest.fixture
def candidate(tmp_path, executables):
    import shutil
    root, folder = tmp_path / "installation", tmp_path / "candidate"
    (root / "python").mkdir(parents=True)
    runtime = root / "runtimes/1.1.1"
    runtime.mkdir(parents=True)
    (root / "application.json").write_bytes(encoded({"schema_version": 1, "app": "fanuc-mcp-server"}))
    (runtime / "policy.json").write_bytes(encoded({"robots": {}, "allow_writes": False}))
    folder.mkdir()
    shutil.copyfile(executables[0], folder / "FanucMcpFrontend.exe")
    shutil.copyfile(executables[1], root / "python/pythonw.exe")
    for path in (root / "python/pythonw.exe.config", folder / "FanucMcpFrontend.exe.config"):
        path.write_bytes(CONFIG)
    (folder / "Backend.py").write_bytes(b"# inert fixture; the echo executable does not execute Python\n")
    (folder / "LegacyEntry.py").write_bytes(b"# retained synthetic entry\n")
    backend = {"profile": "standalone-offline", "runtime": "1.1.1", "sdk": "2.3.0", "framework": "26.1103.174507",
        "launcher_sha256": digest(folder / "LegacyEntry.py"), "python_files": inventory(root / "python"), "runtime_files": inventory(runtime)}
    data = encoded(backend)
    snapshot = hashlib.sha256(data).hexdigest()
    selected = root / "launcher/snapshots" / snapshot
    selected.mkdir(parents=True)
    (selected / "manifest.json").write_bytes(data)
    (root / "launcher/current.json").write_bytes(encoded({"snapshot_id": snapshot}))
    initialize = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "synthetic", "version": "1"}}
    catalog = {"snapshot_id": snapshot, "initialize": initialize, "protocol_versions": ["2025-11-25"],
        "static": {"tools/list": {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}, "prompts/list": {"prompts": []},
                   "resources/list": {"resources": []}, "resources/templates/list": {"resourceTemplates": []}}}
    (folder / "catalog.json").write_bytes(encoded(catalog))
    manifest = {"schema_version": 1, "app": "fanuc-mcp-server", "profile": "standalone-offline", "backend_root": str(root),
        "application_sha256": digest(root / "application.json"),
        "snapshot_id": snapshot, "runtime": "1.1.1", "files": inventory(folder)}
    (folder / "frontend.json").write_bytes(encoded(manifest))
    return root, folder


class Client:
    def __init__(self, folder):
        self.folder = folder
        self.errors = (folder / "stderr.txt").open("wb")
        try:
            self.process = subprocess.Popen([str(folder / "FanucMcpFrontend.exe"), "--manifest", str(folder / "frontend.json"),
                "--sha256", digest(folder / "frontend.json"), "--diagnostics", str(folder / "events.jsonl")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
        except BaseException:
            self.errors.close()
            raise
        self.responses = queue.Queue()
        def reader():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)
        threading.Thread(target=reader, daemon=True).start()

    def send(self, value):
        self.process.stdin.write(encoded(value))
        self.process.stdin.flush()

    def response(self, identifier):
        while True:
            raw = self.responses.get(timeout=12)
            assert raw is not None, (self.folder / "stderr.txt").read_text(errors="strict")
            value = json.loads(raw)
            if value.get("id") == identifier:
                return value, raw

    def initialize(self):
        self.send({"jsonrpc": "2.0", "id": "init Ω", "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "owned fixture", "version": "1"}}})
        self.response("init Ω")
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self):
        self.process.stdin.close()
        code = self.process.wait(timeout=12)
        self.errors.close()
        assert code == 0, (self.folder / "stderr.txt").read_text()
        self.process.stdout.close()


def events(folder):
    return [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("parameters", [None, {}, {"cursor": None}, {"cursor": None, "_meta": {}},
    {"_meta": {"progressToken": 0}}, {"cursor": None, "_meta": {"progressToken": "进度 Ω"}}])
def test_discovery_eof_has_no_backend_and_keeps_opaque_ids(candidate, parameters):
    root, folder = candidate
    client = Client(folder)
    try:
        client.initialize()
        identifier = 123456789012345678901234567890
        for method in ("tools/list", "prompts/list", "resources/list", "resources/templates/list"):
            client.send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": parameters})
            value, raw = client.response(identifier)
            assert str(identifier).encode() in raw and "result" in value
    finally:
        client.close()
    assert not any(row["event"] == "backend_started" for row in events(folder))
    rows = [json.loads(p.read_bytes()) for p in (root / "sessions").glob("*.json")]
    assert len(rows) == 1 and rows[0]["state"] == "closed" and rows[0]["app_created_children"] == []


def test_one_activation_preserves_unicode_media_metadata_and_large_active_frames(candidate):
    _, folder = candidate
    client = Client(folder)
    try:
        client.initialize()
        for identifier, payload in [("activate", "first"), (123456789012345678901234567890, "机器人 Ω" * 200000)]:
            client.send({"jsonrpc": "2.0", "id": identifier, "method": "fixture/echo", "params": {"payload": payload}})
            value, raw = client.response(identifier)
            assert value["result"]["echo"]["payload"] == payload
            assert value["result"]["_meta"]["unicode"] == "机器人 Ω"
            assert value["result"]["content"][0]["data"] == "YmFzZTY0"
            assert "机器人".encode() in raw
    finally:
        client.close()
    assert sum(row["event"] == "backend_started" for row in events(folder)) == 1
    assert [row["details"]["exit_code"] for row in events(folder) if row["event"] == "backend_exited"] == [0]


def test_previous_frontend_reads_live_idle_writer_checkpoint_without_changing_peer(candidate, tmp_path):
    """Use an explicitly supplied sealed previous executable; no installed owner runs here."""
    import shutil
    previous = os.environ.get("FANUC_PREVIOUS_FRONTEND_EXE")
    expected = os.environ.get("FANUC_PREVIOUS_FRONTEND_SHA256")
    if not previous or not expected:
        pytest.skip("Supply an exact reviewed previous frontend for this compatibility control")
    previous = Path(previous)
    assert digest(previous) == expected
    root, folder = candidate
    peer = Client(folder)
    old = None
    try:
        peer.initialize()
        peer.send({"jsonrpc": "2.0", "id": "peer", "method": "fixture/echo", "params": {"payload": "complete"}})
        peer.response("peer")
        rows = list((root / "sessions").glob("*.json"))
        assert len(rows) == 1
        receipt = json.loads(rows[0].read_bytes())
        assert receipt["identity"]["pid"] == peer.process.pid and len(receipt["app_created_children"]) == 1
        # An owned synthetic live pair represents the exact public receipt shape
        # after the backend writer yields, before original-child natural drain.
        receipt.update(state="closing", idle_epoch=1, idle_attempt="d" * 32)
        checkpoint = encoded(receipt)
        rows[0].write_bytes(checkpoint)
        old_folder = tmp_path / "previous-candidate"
        old_folder.mkdir()
        descriptor = json.loads((folder / "frontend.json").read_bytes())
        for entry in descriptor["files"]:
            shutil.copyfile(folder / entry["path"], old_folder / entry["path"])
        shutil.copyfile(previous, old_folder / "FanucMcpFrontend.exe")
        descriptor["files"] = inventory(old_folder)
        (old_folder / "frontend.json").write_bytes(encoded(descriptor))
        old = Client(old_folder)
        old.initialize()
        old.send({"jsonrpc": "2.0", "id": "metadata", "method": "tools/list", "params": {}})
        value, _ = old.response("metadata")
        assert len(value["result"]["tools"]) == 1
        old.close()
        old = None
        assert rows[0].read_bytes() == checkpoint and peer.process.poll() is None
    finally:
        if old is not None:
            old.close()
        peer.close()


def test_cancel_before_dispatch_does_not_launch_backend(candidate):
    _, folder = candidate
    client = Client(folder)
    try:
        client.initialize()
        client.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "future Ω"}})
        client.send({"jsonrpc": "2.0", "id": "future Ω", "method": "fixture/echo"})
        result, _ = client.response("future Ω")
        assert result["error"]["code"] == -32800
    finally:
        client.close()
    assert not any(row["event"] == "backend_started" for row in events(folder))


@pytest.mark.parametrize("parameters", [{"cursor": "next"}, {"_meta": {"unsupported": True}}])
def test_semantic_or_unknown_metadata_activates_and_forwards_once(candidate, parameters):
    _, folder = candidate
    client = Client(folder)
    try:
        client.initialize()
        client.send({"jsonrpc": "2.0", "id": "uncached", "method": "resources/list", "params": parameters})
        response, _ = client.response("uncached")
        assert response["result"]["echo"] == parameters
    finally:
        client.close()
    assert sum(row["event"] == "backend_started" for row in events(folder)) == 1


@pytest.mark.parametrize("member", ["catalog.json", "Backend.py", "LegacyEntry.py", "FanucMcpFrontend.exe.config"])
def test_payload_drift_refuses_without_launch_or_admission(candidate, member):
    root, folder = candidate
    (folder / member).write_bytes(b"changed")
    if member == "FanucMcpFrontend.exe.config":
        with pytest.raises(OSError) as error:
            Client(folder)
        assert error.value.winerror == 14001  # OS rejects invalid CLR config before entry.
        assert not (root / "sessions").exists()
        return
    client = Client(folder)
    client.process.stdin.close()
    assert client.process.wait(timeout=10) == 1
    client.errors.close()
    client.process.stdout.close()
    assert not (root / "sessions").exists()


def test_parent_death_ends_original_actors_while_another_writer_stays_open(candidate):
    import ctypes
    import msvcrt
    import sys
    import time
    from ctypes import wintypes
    from universal_tools.windows_resources import ProcessDriver
    _, folder = candidate
    helper = folder / "parent.py"
    helper.write_text('''import json,msvcrt,os,subprocess,sys
stream=os.fdopen(msvcrt.open_osfhandle(int(sys.argv[2]),os.O_RDONLY),'rb')
with open(sys.argv[3],'wb') as output,open(sys.argv[4],'wb') as errors:
 child=subprocess.Popen(json.loads(sys.argv[1]),stdin=stream,stdout=output,stderr=errors)
 print(child.pid,flush=True)
 sys.stdin.buffer.read(1)
 os._exit(0)
''', encoding="utf-8", newline="\n")
    args = [str(folder / "FanucMcpFrontend.exe"), "--manifest", str(folder / "frontend.json"), "--sha256", digest(folder / "frontend.json"), "--diagnostics", str(folder / "events.jsonl")]
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {"handle_list": [msvcrt.get_osfhandle(read_fd)]}
    parent = subprocess.Popen([sys._base_executable, "-I", "-B", str(helper), json.dumps(args), str(msvcrt.get_osfhandle(read_fd)), str(folder / "wire.txt"), str(folder / "stderr.txt")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startup, close_fds=True)
    os.close(read_fd)
    writer = os.fdopen(write_fd, "wb", buffering=0)
    handles = []
    try:
        int(parent.stdout.readline())
        driver = ProcessDriver()
        writer.write(encoded({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "owned parent fixture", "version": "1"}}}))
        writer.write(encoded({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        writer.write(encoded({"jsonrpc": "2.0", "id": 2, "method": "fixture/echo"}))
        deadline = time.monotonic() + 10
        observed = []
        while time.monotonic() < deadline:
            if (folder / "events.jsonl").exists():
                try:
                    observed = events(folder)
                except json.JSONDecodeError:
                    pass
                if any(row["event"] == "backend_ready" for row in observed):
                    break
            time.sleep(.01)
        assert any(row["event"] == "backend_ready" for row in observed)
        front = next(row["details"] for row in observed if row["event"] == "frontend_ready")
        backend = next(row["details"] for row in observed if row["event"] == "backend_started")
        assert front["parent"] == driver.capture(parent.pid)
        for identity in (front["identity"], backend["identity"]):
            handle = driver._open(identity["pid"], terminate=False)
            handles.append(handle)
            assert handle.identity == identity and handle.alive()
        parent.stdin.write(b"x")
        parent.stdin.flush()
        assert parent.wait(timeout=5) == 0
        assert not writer.closed  # The robot frontend has not received pipe EOF.
        for handle in handles:
            assert handle.wait(3000), "Original contained actor outlived its original parent"
        code = wintypes.DWORD()
        driver.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        assert driver.kernel.GetExitCodeProcess(handles[0].handle, ctypes.byref(code))
        assert code.value == 74
    finally:
        writer.close()
        for handle in handles:
            handle.close()
        if parent.poll() is None:
            parent.stdin.close()
            parent.wait(timeout=5)
        for stream in (parent.stdin, parent.stdout, parent.stderr):
            stream.close()
