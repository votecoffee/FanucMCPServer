"""Opt-in real broker hops with an actual FANUC adapter and a synthetic controller."""
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

from fanuc_workbench.bridge.broker_server import service_manifest
from fanuc_workbench.bridge.service import RobotBridge
from test_robot_bridge import ORIGINAL, PROPOSED, digest


@pytest.mark.parametrize("approved", [True, False])
def test_actual_broker_retains_fanuc_plans_and_confirmation(tmp_path, approved):
    source = os.environ.get("FANUC_TEST_BROKER_SOURCE")
    if not source:
        pytest.skip("Set FANUC_TEST_BROKER_SOURCE for optional real-broker protocol tests")
    broker_source = Path(source).resolve() / "src"
    assert (broker_source / "universal_tools/cli.py").is_file()
    repo = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(broker_source), str(repo), str(repo / "tests")])}
    config = tmp_path / "policy.json"
    config.write_text(json.dumps({"state_dir": "evidence", "source_dir": "edits",
        "allow_writes": True, "approval_mode": "elicitation", "robots": {
            "fixture": {"profile": {"host": "192.0.2.1"}, "writable_files": ["md:MAIN.LS"]}}}))
    edits = tmp_path / "edits"; edits.mkdir()
    (edits / "MAIN.LS").write_bytes(PROPOSED)
    fixture = tmp_path / "fixture.py"
    fixture.write_text('''
import sys
from pathlib import Path
from fanuc_workbench.bridge.broker_server import create_server
from fanuc_workbench.bridge.service import RobotBridge
from test_robot_bridge import MemoryRobot
robot = MemoryRobot()
with RobotBridge.from_config(Path(sys.argv[1]), lambda profile: robot) as bridge:
    create_server(bridge).run(transport="stdio")
''')
    with RobotBridge.from_config(config) as bridge:
        descriptor = asyncio.run(service_manifest(bridge, Path(sys.executable), config, allow_writes=True))
    descriptor["transport"]["args"] = ["-B", str(fixture), str(config)]
    descriptor["transport"]["env_vars"] = ["PYTHONPATH"]
    manifest = tmp_path / "service.json"
    manifest.write_text(json.dumps(descriptor))
    state = tmp_path / "broker-state"
    prefix = [sys.executable, "-B", "-m", "universal_tools", "--state", str(state)]
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    for arguments in (["init", "--port", str(port)], ["register", str(manifest)]):
        subprocess.run(prefix + arguments, env=environment, check=True, capture_output=True, timeout=30)
    settings = json.loads((state / "settings.json").read_text())

    def data(result):
        assert not result.isError, result.model_dump_json()
        return result.structuredContent or json.loads(result.content[0].text)

    async def scenario():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.types import ElicitResult

        prompts, foreign_prompts = [], []
        async def confirm(_, params):
            prompts.append(params.message)
            assert digest(PROPOSED) in params.message and "fixture" in params.message
            return ElicitResult(action="accept", content={"approve": approved})
        async def foreign_confirm(_, params):
            foreign_prompts.append(params.message)
            return ElicitResult(action="accept", content={"approve": True})
        async with AsyncExitStack() as resources:
            clients = []
            for callback in (confirm, foreign_confirm):
                reader, writer = await resources.enter_async_context(stdio_client(
                    StdioServerParameters(command=sys.executable,
                        args=prefix[1:] + ["proxy"], env=environment)))
                client = await resources.enter_async_context(ClientSession(reader, writer, elicitation_callback=callback))
                await client.initialize()
                clients.append(client)
            owner, other = clients
            data(await owner.call_tool("refresh_services"))
            before = (await owner.list_tools()).model_dump()
            context = data(await owner.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"}))["context_id"]
            assert (await other.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"})).isError

            async def call(name, arguments, request_id=None, client=owner):
                tool_id = "fanuc-mcp-server/" + name
                definition = data(await client.call_tool("describe_tool", {"tool_id": tool_id}))
                payload = {"tool_id": tool_id, "catalog_revision": definition["catalog_revision"],
                           "arguments": arguments, "context_id": context}
                if request_id:
                    payload["request_id"] = request_id
                return await client.call_tool("call_write_tool" if request_id else "call_read_tool", payload)

            assert (await call("list_robots", {}, client=other)).isError
            data(await owner.call_tool("refresh_capabilities", {"service_id": "fanuc-mcp-server", "context_id": context}))
            matches = data(await owner.call_tool("search_tools", {
                "service_id": "fanuc-mcp-server", "kind": "capability", "query": "core.files.replace"}))["matches"]
            assert matches and all(row["callable"] is False for row in matches)
            assert data(await call("read_file", {"robot_id": "fixture", "path": "md:MAIN.LS"}))["sha256"] == digest(ORIGINAL)
            plan = data(await call("prepare_upload", {"robot_id": "fixture", "path": "md:MAIN.LS",
                "source_file": "MAIN.LS", "expected_sha256": digest(ORIGINAL)}, "prepare-fixture"))
            data(await owner.call_tool("refresh_services"))
            result = await call("commit_change", {"plan_id": plan["plan_id"],
                "approved_sha256": plan["proposed_sha256"]}, "commit-fixture")
            assert result.isError is (not approved)
            assert len(prompts) == 1 and foreign_prompts == []
            expected = PROPOSED if approved else ORIGINAL
            assert data(await call("read_file", {"robot_id": "fixture", "path": "md:MAIN.LS"}))["sha256"] == digest(expected)
            assert (await call("commit_change", {"plan_id": plan["plan_id"],
                "approved_sha256": plan["proposed_sha256"]}, "commit-fixture")).isError
            data(await owner.call_tool("get_request_status", {"request_id": "commit-fixture"}))
            assert len(prompts) == 1
            assert (await owner.list_tools()).model_dump() == before
            data(await owner.call_tool("release_service", {"service_id": "fanuc-mcp-server", "context_id": context}))

    with (tmp_path / "broker.log").open("wb") as log:
        process = subprocess.Popen(prefix + ["serve"], env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        try:
            deadline = time.monotonic() + 30
            with httpx.Client(timeout=1, trust_env=False) as client:
                while True:
                    assert process.poll() is None, "Isolated broker stopped; inspect its fixture log"
                    try:
                        response = client.get(f"http://127.0.0.1:{port}/health",
                                              headers={"Authorization": "Bearer " + settings["token"]})
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "Isolated broker startup timed out"
                    time.sleep(.1)
            asyncio.run(scenario())
        finally:
            subprocess.run(prefix + ["stop"], env=environment, capture_output=True, timeout=20)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate(); process.wait(timeout=5)
    audit = [json.loads(line) for line in (tmp_path / "evidence/audit.jsonl").read_text().splitlines()]
    assert len([row for row in audit if row["action"] == "upload_started"]) == int(approved)
