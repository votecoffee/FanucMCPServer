"""Background infrastructure must not keep an embedding caller's context alive."""
import asyncio
from contextlib import asynccontextmanager
import contextvars
import gc
import json
import threading
from types import SimpleNamespace
import weakref

import pytest

from fanuc_workbench.bridge import artifact_resources, guidance, job_tasks


PAYLOAD = contextvars.ContextVar("fanuc_test_caller_payload", default=None)
TRACE = contextvars.ContextVar("fanuc_test_caller_trace", default=None)


class Payload:
    pass


def test_per_request_trace_still_changes_between_calls():
    from fanuc_workbench.bridge.requests import RequestDispatcher
    dispatcher = RequestDispatcher(workers=1)
    first = TRACE.set("first-request")
    try:
        assert dispatcher.submit(TRACE.get).result(timeout=2) == "first-request"
        second = TRACE.set("second-request")
        try:
            assert dispatcher.submit(TRACE.get).result(timeout=2) == "second-request"
        finally:
            TRACE.reset(second)
    finally:
        TRACE.reset(first)
        dispatcher.close()


@pytest.mark.parametrize("kind", ["guidance", "artifacts", "jobs"])
def test_background_lifetime_releases_caller_context_and_drains(kind, monkeypatch):
    async def scenario():
        observed = asyncio.Event()
        values, wake_contexts = [], []
        def observe():
            values.append((PAYLOAD.get() is not None, TRACE.get()))
            observed.set()
        activate = lambda: None
        server = SimpleNamespace(_subscriptions=SimpleNamespace())
        listeners = None
        if kind == "guidance":
            service = object.__new__(guidance.WorkflowGuidance)
            async def refresh():
                observe()
            service.refresh = refresh
        elif kind == "artifacts":
            class Catalog:
                def glob(self, pattern):
                    return iter(())
                def stat(self):
                    observe()
                    return SimpleNamespace(st_mtime_ns=0)
            monkeypatch.setattr(artifact_resources, "Context", lambda **kwargs: SimpleNamespace())
            bridge = SimpleNamespace(operations=SimpleNamespace(store=SimpleNamespace(catalog=Catalog())))
            service = artifact_resources.ArtifactResources(bridge)
        else:
            @asynccontextmanager
            async def base_lifespan(self, server):
                yield  # Isolate the app publisher from framework infrastructure.
            monkeypatch.setattr(job_tasks.TaskExtension, "lifespan", base_lifespan)
            listeners = set()
            jobs = SimpleNamespace(lock=threading.Lock(), listeners=listeners)
            service = object.__new__(job_tasks.JobTasks)
            service.bridge = SimpleNamespace(operations=SimpleNamespace(jobs=jobs))
            service.principal = "a" * 32
            async def changed(identifier):
                assert identifier == "synthetic-task"
                observe()
            service.changed = changed
            loop = asyncio.get_running_loop()
            original = loop.call_soon_threadsafe
            def schedule(callback, *args, **kwargs):
                context = kwargs.get("context")
                wake_contexts.append(context is not None and context.get(PAYLOAD) is None and context.get(TRACE) is None)
                return original(callback, *args, **kwargs)
            monkeypatch.setattr(loop, "call_soon_threadsafe", schedule)
            def activate():
                item = {"task_binding": {"owner_hash": job_tasks.owner_hash(service.principal), "task_id": "synthetic-task"}}
                for listener in tuple(listeners):
                    listener(item)
        before = asyncio.all_tasks()
        payload = Payload()
        reference = weakref.ref(payload)
        token = PAYLOAD.set(payload)
        trace_token = TRACE.set("caller-specific-trace")
        async with service.lifespan(server):
            spawned = asyncio.all_tasks() - before
            activate()
            PAYLOAD.reset(token)
            TRACE.reset(trace_token)
            del payload
            await asyncio.wait_for(observed.wait(), 1)
            gc.collect()
            assert reference() is None, "Infrastructure retained its embedding caller's payload"
            assert values == [(False, None)]
            if kind == "jobs":
                assert wake_contexts == [True]
        assert spawned and all(task.done() for task in spawned)
        if listeners is not None:
            assert not listeners
    asyncio.run(scenario())


def test_source_offline_ls_call_and_lifespan_cleanup(tmp_path):
    from mcp import Client
    from fanuc_workbench.bridge.config import BridgeConfig
    from fanuc_workbench.bridge.mcp_server import create_server
    from fanuc_workbench.bridge.offline_service import OfflineBridge
    source = tmp_path / "inputs"
    source.mkdir()
    fixture = source / "synthetic"
    fixture.mkdir()
    (fixture / "CONTEXT.LS").write_bytes(b'/PROG CONTEXT\n/ATTR\n/MN\n   1: WAIT DI[7:READY]=ON ;\n/POS\n/END\n')
    bridge = OfflineBridge(BridgeConfig(tmp_path / "state", source))
    async def scenario():
        server = create_server(bridge, offline=True)
        async with Client(server, mode="legacy", cache=None) as client:
            imported = await client.call_tool("import_workspace", {"source_folder": "synthetic"})
            value = imported.structured_content or json.loads(imported.content[0].text)
            result = await client.call_tool("inspect_program", {"artifact_id": value["artifact_id"], "path": "CONTEXT.LS"})
            result = result.structured_content or json.loads(result.content[0].text)
            assert result["validation"] == [] and [row[1] for row in result["flow"]] == ["WAIT"]
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    try:
        asyncio.run(scenario())
    finally:
        bridge.close()
    assert bridge._closed and not bridge.requests.status()["active"]
