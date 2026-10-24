"""Idle chats consume no data slots; bounded work remains cancelable and honest."""
import asyncio
from contextlib import ExitStack
import threading

import pytest

from fanuc_workbench.bridge import offline_admission as admission
from fanuc_workbench.bridge.offline_service import owned_offline_bridge
from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge.storage import RequestCancelled
from test_offline_multiclient import policy


def fill(state, stack, kind, count):
    for _ in range(count):
        lease = admission.OfflineWorkGate(state).take(kind, count)
        assert lease is not None
        stack.callback(lease.__exit__, None, None, None)


def test_metadata_remains_usable_when_work_and_queue_are_full(tmp_path):
    path = policy(tmp_path)
    with owned_offline_bridge(path) as bridge, ExitStack() as stack:
        fill(bridge.config.state_dir, stack, "active", 8)
        fill(bridge.config.state_dir, stack, "waiting", 16)
        server = create_server(bridge, offline=True)
        async def check():
            assert len(await server.list_tools()) == 15
            value = await server.call_tool("capabilities", {})
            assert not value.is_error
            from mcp.server.mcpserver.exceptions import ToolError
            with pytest.raises(ToolError, match="8 executing, 16 waiting"):
                await server.call_tool("list_artifacts", {})
            assert not (await server.call_tool("list_workflows", {})).is_error
        asyncio.run(check())


def test_data_queue_cancel_and_timeout_never_execute_body(tmp_path, monkeypatch):
    state = tmp_path / "state"
    event = threading.Event(); event.set()
    with ExitStack() as stack:
        fill(state, stack, "active", 8)
        def forbidden(): pytest.fail("Busy/cancelled work was dispatched")
        with pytest.raises(RequestCancelled):
            admission.OfflineWorkGate(state).run(forbidden, cancel_event=event)
        monkeypatch.setattr(admission, "WAIT_SECONDS", .04)
        with pytest.raises(admission.OfflineWorkBusy, match="no data operation started"):
            admission.OfflineWorkGate(state).run(forbidden)
    assert admission.OfflineWorkGate(state).run(lambda: "available") == "available"


def test_failed_body_and_start_release_only_owned_work_slot(tmp_path):
    state = tmp_path / "state"
    gate = admission.OfflineWorkGate(state)
    def failed(): raise ValueError("synthetic data failure")
    with pytest.raises(ValueError): gate.run(failed)
    with ExitStack() as stack: fill(state, stack, "active", 8)
