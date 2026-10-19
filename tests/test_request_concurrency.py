import asyncio
from concurrent.futures import CancelledError
import contextvars
import json
import threading

import pytest

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge.requests import RequestDispatcher, RequestQueueFull
from fanuc_workbench.bridge.service import RobotBridge


def test_parallel_workers_bounded_queue_and_reserved_status_lane():
    dispatcher = RequestDispatcher(workers=2, pending=1)
    entered = threading.Barrier(3)
    release = threading.Event()
    def blocked():
        entered.wait(timeout=3)
        assert release.wait(5)
        return "finished"
    try:
        first, second = dispatcher.submit(blocked), dispatcher.submit(blocked)
        entered.wait(timeout=3)
        queued = dispatcher.submit(lambda: pytest.fail("Cancelled request ran"))
        with pytest.raises(RequestQueueFull):
            dispatcher.submit(lambda: None)
        assert queued.cancel()
        # Cancellation must not free a physical executor queue slot early.
        with pytest.raises(RequestQueueFull):
            dispatcher.submit(lambda: None)
        assert dispatcher.submit(lambda: "responsive", control=True).result(timeout=1) == "responsive"
        release.set()
        assert first.result(timeout=3) == second.result(timeout=3) == "finished"
        with pytest.raises(CancelledError):
            queued.result()
    finally:
        release.set()
        dispatcher.close()
    assert not dispatcher.status()["active"]
    assert any(row["status"] == "cancelled" for row in dispatcher.status()["recent"])
    with pytest.raises(RuntimeError, match="closed"):
        dispatcher.submit(lambda: None)


def test_request_context_is_propagated_without_recording_arguments():
    dispatcher = RequestDispatcher()
    context = contextvars.ContextVar("test_request_context")
    context.set("test-context")
    try:
        def operation(secret):
            return context.get(), dispatcher.current_request_id, dispatcher.lock_timeout
        future = dispatcher.submit(operation, "private-argument")
        assert future.result(timeout=3) == ("test-context", future.request_id, 15)
        assert "private-argument" not in json.dumps(dispatcher.status())
        assert dispatcher.current_request_id is None and dispatcher.lock_timeout == 0
    finally:
        dispatcher.close()


def test_async_cancellation_keeps_running_operation_and_capacity_until_readback():
    dispatcher = RequestDispatcher(workers=1, pending=0)
    release, completed = threading.Event(), threading.Event()
    async def scenario():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        def write_and_readback():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            completed.set()
            return "verified"
        task = asyncio.create_task(dispatcher.run(write_and_readback))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RequestQueueFull):
            dispatcher.submit(lambda: None)
        release.set()
        assert await asyncio.to_thread(completed.wait, 2)
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        dispatcher.close()
    recent = dispatcher.status()["recent"]
    assert len(recent) == 1 and recent[0]["status"] == "complete" and recent[0]["cancel_requested"]


def test_sync_mcp_tool_does_not_block_other_requests_or_the_event_loop(tmp_path, monkeypatch):
    bridge = RobotBridge(BridgeConfig(tmp_path/'state', tmp_path/'edits'))
    entered, release = threading.Event(), threading.Event()
    def slow_read(robot_id, path):
        entered.set()
        assert release.wait(5)
        return {"status": "read"}
    monkeypatch.setattr(bridge, 'read_file', slow_read)
    server = create_server(bridge)
    async def scenario():
        slow = asyncio.create_task(server.call_tool('read_file', {'robot_id':'test','path':'md:MAIN.LS'}))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(server.call_tool('list_robots', {}), 1)
            await asyncio.wait_for(server.call_tool('stage_file', {
                'filename':'TEST.TXT','data_base64':'dGVzdA==',
                'expected_sha256':'9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'}), 2)
        finally:
            release.set()
            await slow
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        bridge.close()


@pytest.mark.parametrize('field,value',[
    ('max_concurrent_requests',0),('max_concurrent_requests',True),('max_concurrent_requests',17),
    ('max_pending_requests',-1),('max_pending_requests',True),('max_pending_requests',129),
    ('robot_lock_timeout_seconds',-1),('robot_lock_timeout_seconds',True),('robot_lock_timeout_seconds',61),
    ('robot_lock_timeout_seconds',float('nan')),
])
def test_invalid_request_capacity_is_rejected_before_runtime_creation(tmp_path,field,value):
    path=tmp_path/'policy.json'
    path.write_text(json.dumps({'state_dir':'state','source_dir':'edits',field:value}))
    with pytest.raises(ValueError, match=field):
        BridgeConfig.load(path)


def test_request_settings_are_preserved_by_config_load(tmp_path):
    path=tmp_path/'policy.json'
    path.write_text(json.dumps({'state_dir':'state','source_dir':'edits','max_concurrent_requests':2,
        'max_pending_requests':3,'robot_lock_timeout_seconds':.5}))
    config=BridgeConfig.load(path)
    assert (config.max_concurrent_requests,config.max_pending_requests,config.robot_lock_timeout_seconds)==(2,3,.5)
