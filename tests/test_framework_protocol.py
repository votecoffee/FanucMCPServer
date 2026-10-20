"""Two SDK2 frontends, authenticated app host and actual FANUC worker, all isolated."""
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from fanuc_workbench.bridge.framework_host import publish
from test_framework_host import app_publication, data
from test_robot_bridge import ORIGINAL, PROPOSED, digest


@pytest.mark.parametrize("answer", ["accept", "deny", "unsupported"])
def test_app_host_frontends_preserve_ownership_forms_and_reload_without_reconnection(app_publication, answer):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import ElicitResult
    state, config, publication, package = app_publication
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)}
    prefix = [sys.executable, "-B", "-m", "fanuc_workbench.bridge.framework_host", "--state", str(state)]
    port = json.loads((state / "settings.json").read_bytes())["port"]

    async def scenario():
        prompts, foreign_prompts = [], []
        async def confirm(_, params):
            prompts.append(params.message)
            assert digest(PROPOSED) in params.message and "fixture" in params.message
            return ElicitResult(action="accept", content={"approve": answer == "accept"})
        async def foreign_confirm(_, params):
            foreign_prompts.append(params.message)
            return ElicitResult(action="accept", content={"approve": True})
        async with AsyncExitStack() as resources:
            clients = []
            for options in ({"elicitation_callback": confirm} if answer != "unsupported" else {},
                            {"elicitation_callback": foreign_confirm}):
                reader, writer = await resources.enter_async_context(stdio_client(
                    StdioServerParameters(command=sys.executable, args=prefix[1:] + ["proxy"], env=environment)))
                client = await resources.enter_async_context(ClientSession(reader, writer, **options))
                await client.initialize(); clients.append(client)
            owner, other = clients
            before = (await owner.list_tools()).model_dump(by_alias=True)
            assert len(before["tools"]) == 14
            context = data(await owner.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"}))["context_id"]
            assert (await other.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"})).is_error
            async def call(name, arguments, request=None, client=owner):
                tool = "fanuc-mcp-server/" + name
                definition = data(await client.call_tool("describe_tool", {"tool_id": tool}))
                payload = {"tool_id": tool, "catalog_revision": definition["catalog_revision"],
                           "context_id": context, "arguments": arguments}
                if request: payload["request_id"] = request
                return await client.call_tool("call_write_tool" if request else "call_read_tool", payload)
            assert (await call("list_robots", {}, client=other)).is_error
            robots = data(await call("list_robots", {}))
            assert len(robots["robots"]) == 1
            for _ in range(3):
                assert data(await call("list_robots", {})) == robots
            read_args = {"robot_id": "fixture", "path": "md:MAIN.LS"}
            assert data(await call("read_file", read_args))["sha256"] == digest(ORIGINAL)
            plan = data(await call("prepare_upload", {**read_args, "source_file": "MAIN.LS",
                "expected_sha256": digest(ORIGINAL)}, "prepare-fixture"))
            source = package / "bridge/framework_backend.py"
            source.write_text(source.read_text().replace("    return server\n", '''    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def framework_fixture_read() -> dict:
        """Identify the new synthetic backend generation."""
        return {"generation": 2}
    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def framework_fixture_write() -> dict:
        """New write tool: never implicitly authorized by publication."""
        return {"unexpected": True}
    return server
''', 1))
            await asyncio.to_thread(publish, state, config, package=package)
            deferred = data(await other.call_tool("refresh_services"))
            assert deferred["backend_publications"][0]["status"] == "deferred"
            result = await call("commit_change", {"plan_id": plan["plan_id"],
                "approved_sha256": plan["proposed_sha256"]}, "commit-fixture")
            assert result.is_error is (answer != "accept")
            assert len(prompts) == (0 if answer == "unsupported" else 1) and foreign_prompts == []
            if answer == "unsupported":
                assert "universal_tools/elicitation_error" in result.meta
            expected = PROPOSED if answer == "accept" else ORIGINAL
            assert data(await call("read_file", read_args))["sha256"] == digest(expected)
            assert (await call("commit_change", {"plan_id": plan["plan_id"],
                "approved_sha256": plan["proposed_sha256"]}, "commit-fixture")).is_error
            receipt = data(await owner.call_tool("get_request_status", {"request_id": "commit-fixture"}))
            assert receipt["status"] in {"completed", "app_error"}
            data(await owner.call_tool("release_service", {"service_id": "fanuc-mcp-server", "context_id": context}))
            refreshed = data(await owner.call_tool("refresh_services"))["backend_publications"][0]
            if answer == "accept":
                assert refreshed["status"] == "applied"
                context = data(await owner.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"}))["context_id"]
                assert data(await call("framework_fixture_read", {})) == {"generation": 2}
                forbidden = data(await owner.call_tool("describe_tool", {"tool_id": "fanuc-mcp-server/framework_fixture_write"}))
                assert forbidden["callable"] is False
                assert (await call("framework_fixture_write", {}, "new-write-not-authorized")).is_error
                data(await owner.call_tool("release_service", {"service_id": "fanuc-mcp-server", "context_id": context}))
            else:
                assert refreshed["reason"] == "app_write_reconciliation_required"
            assert (await owner.list_tools()).model_dump(by_alias=True) == before
            assert foreign_prompts == []

    with (state / "isolated-host.log").open("wb") as log:
        host = subprocess.Popen(prefix + ["serve"], env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        try:
            deadline = time.monotonic() + 30
            while True:
                assert host.poll() is None, "Isolated app host exited before readiness"
                with socket.socket() as sock:
                    sock.settimeout(.2)
                    if sock.connect_ex(("127.0.0.1", port)) == 0: break
                assert time.monotonic() < deadline, "Isolated app host startup deadline"
                time.sleep(.05)
            asyncio.run(scenario())
        finally:
            host.terminate()
            try: host.wait(timeout=15)
            except subprocess.TimeoutExpired: host.kill(); host.wait(timeout=10)
