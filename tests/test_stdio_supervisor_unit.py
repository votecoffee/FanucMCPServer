"""Opaque byte relay and lifetime bookkeeping with inert subprocess objects."""
import importlib.util
import io
import json
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from fanuc_workbench.bridge import stdio_supervisor as module
from fanuc_workbench.bridge.stdio_worker import read_activation


def test_activation_never_prefetches_or_consumes_mcp_initialize_bytes():
    initialization = b'{"jsonrpc":"2.0","method":"initialize","id":1}\n'
    source = io.BytesIO(module.ACTIVATE + initialization)
    assert read_activation(lambda fd, count: source.read(count))
    assert source.read() == initialization
    assert not read_activation(lambda fd, count: b"")


@pytest.mark.parametrize("broken_output", [False, True])
def test_opaque_stdio_bytes_eof_and_exact_job_cleanup_without_process_start(broken_output):
    events, wire = [], []
    ended = threading.Event()
    source_bytes = b'{"jsonrpc":"2.0","method":"initialize","id":1}\n'
    output_bytes = b'{"jsonrpc":"2.0","id":1,"result":{"synthetic":true}}\n'
    boot = json.dumps({"fanuc_stdio_bootstrap": 1, "pid": 124, "creation_time": 201}).encode() + b"\n"
    class Input:
        def write(self, value):
            wire.append(bytes(value))
        def flush(self):
            pass
        def close(self):
            events.append("client-eof")
            ended.set()
    class Process:
        pid, _handle = 123, "retained-root-handle"
        stdin, stdout = Input(), io.BytesIO(boot + output_bytes)
        def wait(self, **kwargs):
            assert ended.wait(2)
            events.append("child-exited")
            return 0
    class Claim:
        def bind(self, pid, created):
            assert (pid, created) == (123, 200)
            events.append("bind-root")
        def bind_bootstrap(self, pid, created):
            assert (pid, created) == (124, 201)
            events.append("bind-actual-worker")
        def exited(self):
            events.append("reconcile-exit")
        def uncertain(self, reason):
            pytest.fail("Inert clean exit must not be reported uncertain")
    def begin(kind):
        assert kind == "stdio-worker"
        events.append("durable-intent")
        return Claim()
    def create(*args, **kwargs):
        assert events == ["durable-intent"] and kwargs["close_fds"]
        events.append("create-owned")
        return Process()
    class Job:
        def attach(self, pid, created):
            assert (pid, created) == (124, 201)
            events.append("contained")
        def close(self):
            events.append("close-exact-job")
    destination = io.BytesIO()
    if broken_output:
        class ClosedClient:
            def write(self, value):
                raise BrokenPipeError()
        destination = ClosedClient()
    result = module.supervise(["inert-command"], SimpleNamespace(begin=begin), io.BytesIO(source_bytes),
        destination, popen=create, job_factory=Job, identity=lambda handle: 200)
    assert result == 0 and b"".join(wire) == module.ACTIVATE + source_bytes
    assert events.index("bind-actual-worker") < events.index("contained") < events.index("client-eof")
    assert events[-2:] == ["close-exact-job", "reconcile-exit"]
    if not broken_output:
        assert destination.getvalue() == output_bytes


def test_generated_plugin_connection_uses_installed_python_and_no_conditional_plugin(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("fanuc_bundle_setup_unit", root / "packaging/bridge/setup_bundle.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    runtime = tmp_path / "Runtime With Spaces"
    python = runtime / "python/pythonw.exe"
    pointer = tmp_path / "Plugin With Spaces/runtime.json"
    connection = setup.plugin_connection(python, pointer, runtime)
    assert set(connection["mcpServers"]) == {"fanuc-mcp-server"}
    server = connection["mcpServers"]["fanuc-mcp-server"]
    assert server["command"] == str(python)
    assert server["args"] == ["-I", "-B", str(runtime / "StartMcp.py")]
    assert server["cwd"] == str(runtime)


def test_bundle_recipe_remains_verifiable_after_generating_runtime_connection(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("fanuc_recipe_unit", root / "packaging/bridge/setup_bundle.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    recipe = (root / "plugins/fanuc-mcp-server/mcp.json").read_bytes()
    (plugin / "mcp.template.json").write_bytes(recipe)
    (tmp_path / "BUNDLE_MANIFEST.json").write_text(json.dumps({"release": setup.versions.release_metadata(), "compatibility": setup.compatibility(), "files": [
        {"path": "plugin/mcp.template.json", "sha256": hashlib.sha256(recipe).hexdigest(), "bytes": len(recipe)}]}))
    setup.verify_bundle(tmp_path)
    connection = setup.plugin_connection(tmp_path / "python/pythonw.exe", plugin / "runtime.json", tmp_path)
    setup.generated_file(tmp_path, "plugin/mcp.json", json.dumps(connection).encode())
    setup.generated_file(tmp_path, "plugin/runtime.json", b'{}')
    setup.verify_bundle(tmp_path)  # Reinstall checks the immutable recipe again.
    (plugin / "mcp.template.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        setup.verify_bundle(tmp_path)


def test_failed_bootstrap_never_activates_and_retains_recovery_claim(monkeypatch):
    monkeypatch.setattr(module, "_uncertain", [])
    events = []
    class Input:
        def write(self, data):
            pytest.fail("Failed bootstrap must never receive activation/MCP input")
        def close(self):
            events.append("close-unactivated-input")
    class Process:
        pid, _handle = 123, object()
        stdin, stdout = Input(), io.BytesIO(b"invalid-bootstrap\n")
        def wait(self, **kwargs):
            events.append("wait-owned-root")
            return 1
    class Claim:
        def bind(self, pid, created):
            events.append("bound-launch")
        def uncertain(self, reason):
            events.append("durable-recovery-barrier")
    context = SimpleNamespace(begin=lambda kind: Claim())
    with pytest.raises(ValueError, match="bootstrap identity"):
        module.supervise(["inert"], context, io.BytesIO(b"queued MCP input"), io.BytesIO(),
            popen=lambda *args, **kwargs: Process(), identity=lambda handle: 200)
    assert events == ["bound-launch", "durable-recovery-barrier", "close-unactivated-input", "wait-owned-root"]
    assert len(module._uncertain) == 1


def test_host_entrypoint_refuses_unmanaged_publications_and_propagates_private_registry(tmp_path, monkeypatch):
    from fanuc_workbench.bridge.framework_host import validate_owned_entrypoint
    from fanuc_workbench.bridge.resource_context import registry_environment
    key = "FANUC_MCP_MANAGEMENT_REGISTRY"
    old = str(tmp_path / "previous")
    monkeypatch.setenv(key, old)
    transport = SimpleNamespace(type="stdio", args=["-B", "worker.py"], env_vars=[])
    with pytest.raises(ValueError, match="Republish"):
        validate_owned_entrypoint(SimpleNamespace(transport=transport))
    transport.args = ["-B", "-m", "fanuc_workbench.bridge.stdio_supervisor", "--entry", "worker.py"]
    transport.env_vars = [key]
    validate_owned_entrypoint(SimpleNamespace(transport=transport))
    import os
    with registry_environment(tmp_path / "selected") as selected:
        assert os.environ[key] == str(selected) and selected == (tmp_path / "selected").absolute()
    assert os.environ[key] == old


@pytest.mark.parametrize("failure", ["identity", "cleanup", "barrier"])
def test_identity_refusal_precedes_containment_and_uncertain_owners_are_retained(monkeypatch, failure):
    monkeypatch.setattr(module, "_uncertain", [])
    events = []
    ended = threading.Event()
    boot = json.dumps({"fanuc_stdio_bootstrap": 1, "pid": 123, "creation_time": 200}).encode() + b"\n"
    class Input:
        def write(self, data):
            assert data == module.ACTIVATE
            events.append("activated")
        def flush(self):
            pass
        def close(self):
            ended.set()
    class Process:
        pid, _handle = 123, object()
        stdin, stdout = Input(), io.BytesIO(boot)
        def wait(self, **kwargs):
            assert ended.wait(2)
            return 0
    class Claim:
        def bind(self, pid, created):
            pass
        def bind_bootstrap(self, pid, created):
            events.append("identity-check")
            if failure in {"identity", "barrier"}:
                raise ValueError("Inert foreign process claim")
        def uncertain(self, reason):
            assert len(module._uncertain) == 1  # Strong refs survive failed ledger writes.
            events.append("barrier")
            if failure == "barrier":
                raise OSError("Inert ledger write failure")
        def exited(self):
            pytest.fail("An uncertain cleanup cannot attest completion")
    class Job:
        def __init__(self):
            events.append("job-created")
        def attach(self, pid, created):
            events.append("contained")
        def close(self):
            events.append("job-close")
            raise OSError("Inert tree exit could not be verified")
    expected = ValueError if failure == "identity" else OSError
    with pytest.raises(expected):
        module.supervise(["inert"], SimpleNamespace(begin=lambda kind: Claim()), io.BytesIO(), io.BytesIO(),
            popen=lambda *args, **kwargs: Process(), identity=lambda handle: 200, job_factory=Job)
    assert len(module._uncertain) == 1 and events[-1] == "barrier"
    module._uncertain[0][3].join(timeout=2)
    assert not module._uncertain[0][3].is_alive()
    if failure == "cleanup":
        assert events == ["identity-check", "job-created", "contained", "activated", "job-close", "barrier"]
        assert module._uncertain[0][2] is not None
    else:
        assert events == ["identity-check", "barrier"]
