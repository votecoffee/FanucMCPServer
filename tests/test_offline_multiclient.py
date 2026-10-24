"""Shared saved evidence never transfers native/job ownership between chats."""
import asyncio
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from fanuc_workbench.bridge import offline_service
from fanuc_workbench.bridge.instance_owner import owned_mcp_bridge
from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge.offline_service import owned_offline_bridge


def policy(tmp_path):
    path = tmp_path / "offline.json"
    path.write_text(json.dumps({"state_dir": "evidence", "source_dir": "inputs", "robots": {},
        "allow_writes": False, "max_concurrent_requests": 2, "max_pending_requests": 4}))
    source = tmp_path / "inputs/synthetic"
    source.mkdir(parents=True)
    (source / "SAMPLE.LS").write_bytes(b"/PROG SAMPLE\n/MN\n 1: R[1]=2 ;\n/POS\n/END\n")
    return path


def test_offline_peers_share_imports_reads_and_drain_independently(tmp_path):
    path = policy(tmp_path)
    with owned_offline_bridge(path) as first, owned_offline_bridge(path) as second:
        item = first.operations.import_workspace("synthetic")
        assert second.operations.store.read(item["artifact_id"], "SAMPLE.LS")["bytes"] > 0
        assert second.operations.store.list()["total"] == 1
        hold, started = threading.Event(), threading.Event()
        def blocked():
            started.set(); assert hold.wait(5)
        future = first.requests.submit(blocked)
        assert started.wait(2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(first.close)
            try:
                assert second.requests.submit(second.operations.audit).result(timeout=2)["total"] == 1
                assert not closing.done()
            finally:
                hold.set()
            closing.result(timeout=2)
        future.result(timeout=2)
        assert second.operations.store.read(item["artifact_id"], "SAMPLE.LS")["bytes"] > 0
        assert not second.requests.closed


def test_bound_is_eight_and_native_owner_is_preserved(tmp_path):
    path = policy(tmp_path)
    class InertNativeOwner:
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
    with owned_mcp_bridge(path, factory=InertNativeOwner):
        with ExitStack() as stack:
            peers = [stack.enter_context(owned_offline_bridge(path)) for _ in range(8)]
            assert len(peers) == 8
            with pytest.raises(SystemExit, match="capacity reached"):
                with owned_offline_bridge(path):
                    pytest.fail("Ninth client was admitted")
            with pytest.raises(SystemExit, match="already has an MCP worker"):
                with owned_mcp_bridge(path, factory=InertNativeOwner):
                    pytest.fail("Offline admission altered the native owner's lease")
    with owned_offline_bridge(path):
        pass


def test_no_native_jobs_extensions_or_uncertain_journal_recovery(tmp_path, monkeypatch):
    path = policy(tmp_path)
    state = tmp_path / "evidence"
    state.mkdir()
    retained = {}
    for name in ("jobs/" + "a" * 32 + ".json", "native-uncertain.json", "extensions/registry.json"):
        file = state / name
        file.parent.mkdir(parents=True, exist_ok=True)
        retained[file] = b'{"status":"running","intent":"uncertain","retain":true}'
        file.write_bytes(retained[file])
    def forbidden(*args, **kwargs):
        pytest.fail("An offline chat may not construct an online/native/job owner")
    from fanuc_workbench.bridge.service import RobotBridge
    from fanuc_workbench.bridge.operations import WorkbenchOperations
    from fanuc_workbench.bridge.jobs import JobManager
    monkeypatch.setattr(RobotBridge, "__init__", forbidden)
    monkeypatch.setattr(WorkbenchOperations, "__init__", forbidden)
    monkeypatch.setattr(JobManager, "__init__", forbidden)
    with owned_offline_bridge(path) as first, owned_offline_bridge(path) as second:
        assert not hasattr(first, "native") and not hasattr(first.operations, "jobs")
        item = first.operations.import_workspace("synthetic")
        assert second.operations.store.verify(item["artifact_id"]).is_dir()
        async def check():
            assert len((await create_server(first, offline=True).list_tools())) == 15
        asyncio.run(check())
    assert all(file.read_bytes() == raw for file, raw in retained.items())


def test_invalid_policy_refuses_before_any_state_or_factory(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"state_dir":"evidence","source_dir":"inputs","robots":{},"allow_writes":true}')
    with pytest.raises(ValueError, match="zero robots"):
        with owned_offline_bridge(path): pass
    assert not (tmp_path / "evidence").exists()


def test_uncertain_local_drain_retains_only_its_slot(tmp_path):
    path = policy(tmp_path)
    class Broken:
        def __init__(self, *args, **kwargs): pass
        def close(self): raise RuntimeError("inert drain uncertainty")
    count = len(offline_service._uncertain_leases)
    try:
        with pytest.raises(RuntimeError, match="uncertainty"):
            with owned_offline_bridge(path, factory=Broken): pass
        with ExitStack() as stack:
            for _ in range(7): stack.enter_context(owned_offline_bridge(path))
            with pytest.raises(SystemExit, match="capacity reached"):
                with owned_offline_bridge(path): pass
    finally:
        # Inert fixture leases only. Production uncertainty lives until OS exit.
        while len(offline_service._uncertain_leases) > count:
            offline_service._uncertain_leases.pop().__exit__(None, None, None)
