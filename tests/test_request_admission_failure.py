"""Failed worker startup must not leave replayable executor work or capacity."""
import asyncio
import gc
import json
import threading
import weakref

import pytest

from fanuc_workbench.bridge.offline_service import owned_offline_bridge
from fanuc_workbench.bridge.requests import RequestAdmissionFailed, RequestDispatcher
from fanuc_workbench.bridge.storage import RobotLocks


def fail_next_worker(monkeypatch):
    original = threading.Thread.start
    failed = threading.Event()
    def start(thread, *args, **kwargs):
        if thread.name.startswith("fanuc-request") and not failed.is_set():
            failed.set()
            raise RuntimeError("Synthetic worker startup failure")
        return original(thread, *args, **kwargs)
    monkeypatch.setattr(threading.Thread, "start", start)
    return failed


def test_rejected_request_never_runs_later_and_releases_payload(monkeypatch):
    class Payload:
        pass
    dispatcher = RequestDispatcher(workers=1, pending=1)
    payload = Payload()
    reference = weakref.ref(payload)
    called = []
    fail_next_worker(monkeypatch)
    try:
        with pytest.raises(RequestAdmissionFailed):
            dispatcher.submit(lambda value: called.append("rejected"), payload)
        del payload
        for _ in range(3):
            with pytest.raises(RequestAdmissionFailed):
                dispatcher.submit(lambda: called.append("later"))
        gc.collect()
        assert reference() is None and not called
        status = dispatcher.status()
        assert status["admission_failed"] and status["closed"] and not status["active"]
        assert len(status["recent"]) == 1
        assert status["recent"][0]["admission_error_type"] == "RuntimeError"
    finally:
        dispatcher.close()


def test_submission_failure_drains_existing_work_without_dispatching_rejected_body(monkeypatch):
    dispatcher = RequestDispatcher(workers=2)
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    errors, workers, called = [], [], []
    def blocked():
        workers.append(threading.current_thread())
        entered.set()
        assert release.wait(5)
        return "verified completion"
    first = dispatcher.submit(blocked)
    assert entered.wait(2)
    failed = fail_next_worker(monkeypatch)
    def submit():
        try:
            dispatcher.submit(lambda: called.append("rejected"))
        except RequestAdmissionFailed as exc:
            errors.append(type(exc).__name__)
        finally:
            returned.set()
    caller = threading.Thread(target=submit)
    caller.start()
    try:
        assert failed.wait(2)
        assert not returned.wait(.05)
        assert not first.done()
    finally:
        release.set()
        caller.join(timeout=3)
        dispatcher.close()
    assert not caller.is_alive() and errors == ["RequestAdmissionFailed"]
    assert first.result(timeout=2) == "verified completion" and not called
    assert all(not worker.is_alive() for worker in workers)
    assert not dispatcher.status()["active"]


def policy(tmp_path):
    path = tmp_path / "offline.json"
    path.write_text(json.dumps({"state_dir": "state", "source_dir": "inputs", "robots": {}, "allow_writes": False}))
    return path


def test_cancelled_factory_releases_its_exact_metadata_slot(tmp_path):
    path = policy(tmp_path)
    def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        with owned_offline_bridge(path, factory=cancelled):
            pytest.fail("Cancelled factory was admitted")
    locks = RobotLocks(tmp_path / "state/mcp-owner/metadata-clients")
    with locks.acquire("metadata-client-0"):
        pass


def test_offline_submission_failure_releases_lease_and_peer_remains_useful(tmp_path, monkeypatch):
    from fanuc_workbench.bridge.mcp_server import create_server
    from mcp.server.mcpserver.exceptions import ToolError
    path = policy(tmp_path)
    folder = tmp_path / "inputs/synthetic"
    folder.mkdir(parents=True)
    (folder / "CHECK.LS").write_bytes(b'/PROG CHECK\n/ATTR\n/MN\n   1: WAIT DI[7]=ON ;\n/POS\n/END\n')
    with owned_offline_bridge(path) as failed, owned_offline_bridge(path) as peer:
        fail_next_worker(monkeypatch)
        async def rejected():
            with pytest.raises(ToolError, match="Reconnect"):
                await create_server(failed, offline=True).call_tool("import_workspace", {"source_folder": "synthetic"})
        asyncio.run(rejected())
        assert not failed.requests.status()["active"]
        assert peer.operations.store.list()["total"] == 0
        imported = peer.requests.submit(peer.run_data, peer.operations.import_workspace, "synthetic").result(timeout=2)
        result = peer.requests.submit(peer.run_data, peer.operations.inspect_program, imported["artifact_id"], "CHECK.LS").result(timeout=2)
        assert result["validation"] == [] and [row[1] for row in result["flow"]] == ["WAIT"]
    locks = RobotLocks(tmp_path / "state/mcp-owner/metadata-clients")
    with locks.acquire("metadata-client-0"), locks.acquire("metadata-client-1"):
        pass
