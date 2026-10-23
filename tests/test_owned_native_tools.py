"""Owned compiler routing and capture with inert process/RPC objects only."""
import base64
import io
import subprocess
from types import SimpleNamespace

import pytest

from fanuc_workbench.services import native_validation as module


@pytest.mark.parametrize("failure", [False, True])
def test_compile_routes_through_owned_worker_and_always_closes(monkeypatch, tmp_path, failure):
    from fanuc_workbench.bridge import local_tool_job, resource_context
    events = []
    def run(arguments, cwd, timeout, *, registry):
        assert arguments == ["inert"] and cwd == tmp_path and timeout == 10
        assert registry == tmp_path / "registry"
        events.append("atomic-owned-job")
        if failure: raise RuntimeError("Inert unverified tool outcome")
        return {"returncode": 0}, b"retained log"
    monkeypatch.setattr(local_tool_job, "run_tool", run)
    monkeypatch.setattr(resource_context, "resource_context", lambda: SimpleNamespace(
        ledger=SimpleNamespace(directory=tmp_path / "registry/resources")))
    if failure:
        with pytest.raises(RuntimeError):
            module._run(["inert"], tmp_path, 10)
    else:
        state, output = module._run(["inert"], tmp_path, 10)
        assert state["returncode"] == 0 and output == b"retained log"
    assert events == ["atomic-owned-job"]


@pytest.mark.parametrize("timed_out,truncated", [(False, False), (True, False), (False, True)])
def test_compiler_intent_precedes_create_and_output_is_retained_bounded(tmp_path, monkeypatch, timed_out, truncated):
    from fanuc_workbench.bridge import resource_context, process_identity
    executable = tmp_path / "maketp.exe"
    executable.write_bytes(b"Inert compiler fixture; never executed")
    events = []
    class Claim:
        def bind(self, pid, created):
            assert (pid, created) == (123, 456)
            events.append("bind")
        def exited(self, evidence):
            events.append("observed-exit")
        def uncertain(self, reason):
            events.append("uncertain")
    def begin(kind):
        assert kind == "compiler-process"
        events.append("durable-intent")
        return Claim()
    monkeypatch.setattr(resource_context, "resource_context", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(process_identity, "creation_time", lambda handle: 456)
    data = b"x" * (1200000 if truncated else 20)
    class Process:
        pid, _handle = 123, object()
        returncode = 0
        def __init__(self):
            self.stdout = io.BytesIO(data)
            self.waits = 0
        def wait(self, **kwargs):
            self.waits += 1
            if timed_out and self.waits == 1:
                raise subprocess.TimeoutExpired("inert", 1)
            return self.returncode
        def kill(self):
            events.append("owned-handle-kill")
            self.returncode = 1
    def create(arguments, **kwargs):
        assert events == ["durable-intent"]
        assert kwargs["close_fds"] and not kwargs.get("shell")
        events.append("create")
        return Process()
    monkeypatch.setattr(module.subprocess, "Popen", create)
    state, output = module._run_process([str(executable)], tmp_path, 1, contained=True)
    assert state["timed_out"] is timed_out and state["output_truncated"] is truncated
    assert len(output) == min(len(data), 1024 * 1024)
    assert (tmp_path / state["output_log"]).read_bytes() == output
    assert events[:3] == ["durable-intent", "create", "bind"]
    assert events[-1] == "observed-exit"
    assert ("owned-handle-kill" in events) == (timed_out or truncated)


def test_uncontained_or_unsupported_compiler_is_refused_before_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No process should launch"))
    with pytest.raises(RuntimeError, match="owned offline tool worker"):
        module._run_process(["inert"], tmp_path, 1)
    with pytest.raises(ValueError, match="explicit MakeTP/PrintTP"):
        module._run_process(["inert"], tmp_path, 1, contained=True)
