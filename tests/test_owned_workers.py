"""Inert process handles and stub kernels only; never start native/MCP software."""
import ctypes
import threading
from types import SimpleNamespace

import pytest

from fanuc_workbench.bridge import extension_process as module
from fanuc_workbench.bridge.extension_host import Generation
from fanuc_workbench.bridge.native import NativeChanges
from fanuc_workbench.bridge.owned_workers import WorkerInventory


class Kernel:
    def __init__(self):
        self.closed, self.assigned, self.terminated = [], [], []
    def OpenProcess(self, *args):
        return 123
    def IsProcessInJob(self, process, job, member):
        member._obj.value = False
        return True
    def AssignProcessToJobObject(self, job, process):
        self.assigned.append((job, process))
        return True
    def TerminateJobObject(self, job, code):
        self.terminated.append(job)
        return True
    def QueryInformationJobObject(self, job, kind, value, *args):
        value._obj.active = 0
        return True
    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


def job(kernel):
    result = object.__new__(module.WindowsJob)
    result.c, result.kernel, result.handle, result.processes = ctypes, kernel, 456, []
    return result


def test_creation_mismatch_never_attaches_or_terminates_another_process(monkeypatch):
    kernel, owned = Kernel(), None
    monkeypatch.setattr(module, "creation_time", lambda *args, **kwargs: 100)
    owned = job(kernel)
    with pytest.raises(ValueError, match="identity changed"):
        owned.attach(999, 101)
    assert kernel.closed == [123] and not kernel.assigned and not kernel.terminated
    with pytest.raises(ValueError, match="creation identity"):
        owned.attach(999, None)
    assert kernel.closed == [123]


def test_exact_handle_is_retained_until_job_exit_is_observed(monkeypatch):
    kernel = Kernel()
    owned = job(kernel)
    monkeypatch.setattr(module, "creation_time", lambda *args, **kwargs: 100)
    owned.attach(999, 100)
    assert owned.processes == [123] and kernel.closed == []
    owned.close()
    assert kernel.terminated == [456] and kernel.closed == [456, 123]
    assert owned.handle is None and owned.processes == []
    owned.close()
    assert kernel.closed == [456, 123]


def test_job_query_failure_retains_handles_and_never_claims_cleanup(monkeypatch):
    kernel = Kernel()
    kernel.QueryInformationJobObject = lambda *args: False
    owned = job(kernel)
    owned.processes = [123]
    with pytest.raises(OSError, match="exit could not be verified"):
        owned.close()
    assert owned.handle == 456 and owned.processes == [123]
    assert kernel.closed == []


def test_inventory_is_bounded_cached_and_retains_uncertainty():
    inventory = WorkerInventory(limit=1)
    owner = object()
    identifier = inventory.reserve(owner, "native-worker")
    inventory.update(identifier, "uncertain", pid=999, creation_time=100)
    with pytest.raises(RuntimeError, match="capacity"):
        inventory.reserve(object(), "feature-worker")
    snapshot = inventory.snapshot()
    assert snapshot["active"] == snapshot["uncertain"] == 1
    assert snapshot["resources"][0]["ownership"] == "exclusive-app-launch"
    snapshot["resources"][0]["state"] = "fake"
    assert inventory.snapshot()["resources"][0]["state"] == "uncertain"
    assert inventory.owners[identifier] is owner
    inventory.release(identifier)
    assert inventory.snapshot()["active"] == 0


def inert_rpc(monkeypatch, *, failure=False, no_job=False):
    calls = []
    inventory = WorkerInventory()
    monkeypatch.setattr(module, "workers", inventory)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    def forbidden(*args, **kwargs):
        pytest.fail("No subprocess may start in pure ownership tests")
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    class Pipe:
        def close(self):
            calls.append("pipe-close")
    class Process:
        stdin, stdout = Pipe(), Pipe()
        def poll(self):
            return None
        def kill(self):
            calls.append("exact-root-handle-kill")
        def wait(self, **kwargs):
            calls.append("wait-exact-root")
            return 0
    class OwnedJob:
        def close(self):
            calls.append("exact-job-close")
            if failure:
                raise OSError("inert job close failure")
    rpc = object.__new__(module.RpcProcess)
    rpc.lock, rpc.stopped, rpc.broken = threading.Lock(), threading.Event(), False
    rpc.process, rpc.reader = Process(), None
    rpc.job, rpc.cleanup_complete = None if no_job else OwnedJob(), False
    rpc.resource_id = inventory.reserve(rpc, "feature-worker")
    return rpc, inventory, calls


def test_teardown_stops_owned_tree_before_closing_its_pipes(monkeypatch):
    rpc, inventory, calls = inert_rpc(monkeypatch)
    rpc._stop()
    assert calls == ["exact-job-close", "wait-exact-root", "pipe-close", "pipe-close"]
    assert rpc.cleanup_complete and inventory.snapshot()["active"] == 0
    rpc.close()
    assert len(calls) == 4


def test_failed_teardown_retains_owner_and_refuses_automatic_retry(monkeypatch):
    rpc, inventory, calls = inert_rpc(monkeypatch, failure=True)
    with pytest.raises(OSError):
        rpc._stop()
    assert calls == ["exact-job-close"] and not rpc.cleanup_complete
    assert inventory.snapshot()["uncertain"] == 1 and inventory.owners[rpc.resource_id] is rpc
    with pytest.raises(RuntimeError, match="Previous worker cleanup"):
        rpc.close()
    assert calls == ["exact-job-close"]


def test_unactivated_startup_uses_own_root_handle_without_taskkill(monkeypatch):
    rpc, inventory, calls = inert_rpc(monkeypatch, no_job=True)
    rpc._stop()
    assert calls[0] == "exact-root-handle-kill"
    assert inventory.snapshot()["active"] == 0


def test_cancelled_construction_releases_admission_without_starting_any_worker(monkeypatch):
    inventory = WorkerInventory()
    monkeypatch.setattr(module, "workers", inventory)
    monkeypatch.setattr(module, "resource_context", lambda: SimpleNamespace(begin=lambda kind: None))
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("inert cancelled startup")
    monkeypatch.setattr(module.subprocess, "Popen", interrupted)
    with pytest.raises(KeyboardInterrupt):
        module.RpcProcess(["inert-command"], {})
    assert inventory.snapshot()["active"] == 0


def test_cleanup_attempts_all_workers_and_keeps_failed_owners():
    calls = []
    class Inert:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail
        def close(self):
            calls.append(self.name)
            if self.fail:
                raise OSError("inert failure")
    failed, healthy = Inert("failed", True), Inert("healthy")
    generation = Generation("test", workers={"bad": failed, "good": healthy})
    with pytest.raises(RuntimeError):
        generation.close()
    assert calls == ["failed", "healthy"] and generation.workers == {"bad": failed}
    native = object.__new__(NativeChanges)
    native.client_lock = threading.RLock()
    native.clients = {"bad": failed, "good": healthy}
    with pytest.raises(RuntimeError):
        native.close()
    assert calls == ["failed", "healthy", "failed", "healthy"]
    assert native.clients == {"bad": failed}


def test_management_reports_cached_uncertain_resource_count_without_starting_endpoint(tmp_path):
    from fanuc_workbench.bridge.management import ServiceReporting
    bridge = SimpleNamespace(management_activity=lambda: {
        "active_operations": 0, "review_plans": 0,
        "owned_resources": {"active": 1, "limit": 16, "uncertain": 1},
    })
    reporting = ServiceReporting(bridge, registry=tmp_path / "unused-registry")
    reporting.refresh()
    status = reporting.status()
    assert status.state == "degraded" and not status.restart_allowed
    assert "1/16 owned workers, 1 uncertain" in status.message
    assert reporting.agent.server is None and reporting.thread is None
