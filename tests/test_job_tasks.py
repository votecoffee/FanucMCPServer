"""Actual app jobs with synthetic file transports; no native controller contact."""
import asyncio
import json
import threading

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from universal_tools.tasks import TaskClient

from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge.job_tasks import job_id
from test_bridge_operations import bridge_case


OWNER = "a" * 32
OTHER = "b" * 32


async def terminal(tasks, client, identifier):
    async with asyncio.timeout(10):
        while True:
            result = await tasks.get(client.session, identifier)
            if result["status"] in {"completed", "cancelled", "failed"}:
                return result
            await asyncio.sleep(.02)


def test_task_backup_deduplicates_actual_download_and_owner_denial(bridge_case, monkeypatch):
    bridge, robot, _ = bridge_case
    downloads = []
    original = robot.download_bounded
    def downloaded(remote, local, maximum):
        downloads.append(remote)
        return original(remote, local, maximum)
    monkeypatch.setattr(robot, "download_bounded", downloaded)
    async def scenario():
        server = create_server(bridge, frontend_identity=OWNER, protocol_preview=True)
        tasks, foreign_tasks = TaskClient(), TaskClient()
        async with Client(server, mode="2026-07-28", extensions=[tasks], cache=None) as client, Client(
                create_server(bridge, frontend_identity=OTHER, protocol_preview=True), mode="2026-07-28", extensions=[foreign_tasks], cache=None) as foreign:
            args = {"robot_id": "r1", "request_id": "one-backup-operation", "paths": ["md:MAIN.LS"], "attempt_ascii": False}
            first = await tasks.create(client.session, "start_backup_task", args)
            duplicate = await tasks.create(client.session, "start_backup_task", args)
            assert first["taskId"] == duplicate["taskId"]
            complete = await terminal(tasks, client, first["taskId"])
            assert complete["status"] == "completed" and not complete["result"]["isError"]
            with pytest.raises(MCPError):
                await foreign_tasks.get(foreign.session, first["taskId"])
            assert (await foreign.call_tool("job_status", {"job_id": job_id(first["taskId"])})).is_error
            assert (await foreign.call_tool("cancel_job", {"job_id": job_id(first["taskId"])})).is_error
            visible = await foreign.call_tool("list_jobs", {})
            assert (visible.structured_content or json.loads(visible.content[0].text))["jobs"] == []
            with pytest.raises(MCPError):
                await tasks.create(client.session, "start_backup_task", {**args, "max_depth": 2})
            assert downloads == ["md:MAIN.LS"] and robot.uploads == 0
    asyncio.run(scenario())


def test_lost_task_start_reply_reconciles_job_without_second_start(bridge_case, monkeypatch):
    bridge, robot, _ = bridge_case
    async def scenario():
        server = create_server(bridge, frontend_identity=OWNER, protocol_preview=True)
        adapter = server.job_tasks.routes["start_backup_task"].adapter
        original, starts = adapter.start, []
        async def lost(context, arguments):
            starts.append(context.task_id)
            await original(context, arguments)
            raise OSError("synthetic lost start reply")
        monkeypatch.setattr(adapter, "start", lost)
        tasks = TaskClient()
        async with Client(server, mode="2026-07-28", extensions=[tasks], cache=None) as client:
            args = {"robot_id": "r1", "request_id": "lost-reply-backup", "paths": ["md:MAIN.LS"], "attempt_ascii": False}
            created = await tasks.create(client.session, "start_backup_task", args)
            complete = await terminal(tasks, client, created["taskId"])
            assert complete["status"] == "completed" and not complete["result"]["isError"]
            assert (await tasks.create(client.session, "start_backup_task", args))["taskId"] == created["taskId"]
            assert starts == [created["taskId"]]
            assert server.job_tasks.store.load(created["taskId"], __import__('hashlib').sha256(OWNER.encode()).hexdigest())["data"]["pending"] is None
    asyncio.run(scenario())


def test_cancel_is_requested_until_inflight_read_exits_and_no_rollback(bridge_case, monkeypatch):
    bridge, robot, _ = bridge_case
    entered, release = threading.Event(), threading.Event()
    original = robot.download_bounded
    def slow(remote, local, maximum):
        entered.set()
        assert release.wait(10)
        return original(remote, local, maximum)
    monkeypatch.setattr(robot, "download_bounded", slow)
    async def scenario():
        server = create_server(bridge, frontend_identity=OWNER, protocol_preview=True)
        tasks = TaskClient()
        async with Client(server, mode="2026-07-28", extensions=[tasks], cache=None) as client:
            created = await tasks.create(client.session, "start_backup_task", {"robot_id": "r1", "request_id": "cancel-read-backup",
                "paths": ["md:MAIN.LS"], "attempt_ascii": False})
            assert await asyncio.to_thread(entered.wait, 5)
            await tasks.cancel(client.session, created["taskId"])
            assert (await tasks.get(client.session, created["taskId"]))["status"] == "working"
            job = bridge.operations.jobs.status(job_id(created["taskId"]))
            assert job["status"] == "cancel_requested"
            release.set()
            assert (await terminal(tasks, client, created["taskId"]))["status"] == "cancelled"
            assert robot.uploads == 0
    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_task_requires_negotiation_and_absent_start_evidence_never_replays(bridge_case, monkeypatch):
    bridge, robot, _ = bridge_case
    async def scenario():
        server = create_server(bridge, frontend_identity=OWNER, protocol_preview=True)
        async with Client(server, mode="2026-07-28", cache=None) as unsupported:
            with pytest.raises(MCPError):
                await unsupported.call_tool("start_backup_task", {"robot_id": "r1", "request_id": "must-not-dispatch"})
        adapter = server.job_tasks.routes["start_backup_task"].adapter
        attempts = []
        async def absent(context, arguments):
            attempts.append(context.task_id)
            raise OSError("lost before admission")
        monkeypatch.setattr(adapter, "start", absent)
        tasks = TaskClient()
        async with Client(server, mode="2026-07-28", extensions=[tasks], cache=None) as client:
            args = {"robot_id": "r1", "request_id": "uncertain-admission"}
            created = await tasks.create(client.session, "start_backup_task", args)
            assert (await tasks.get(client.session, created["taskId"]))["status"] == "working"
            assert (await tasks.create(client.session, "start_backup_task", args))["taskId"] == created["taskId"]
            assert attempts == [created["taskId"]] and robot.connects == 0
            result = await client.call_tool("task_job_status", {"task_id": created["taskId"]})
            assert (result.structured_content or json.loads(result.content[0].text))["status"] == "admission_uncertain"
    asyncio.run(scenario())
