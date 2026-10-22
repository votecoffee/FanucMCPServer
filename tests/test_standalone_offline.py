"""Offline dispatch and receipts only; no MCP session, process or controller."""
import asyncio
import json
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge import offline_profile, instance_owner
from fanuc_workbench.services.workspace_api import ANALYSES


@pytest.fixture
def offline(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline tools cannot spawn, connect, compile or reload code")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    config = BridgeConfig(tmp_path / "state", tmp_path / "inputs")
    with RobotBridge(config, forbidden) as bridge:
        registry = config.state_dir / "extensions/registry.json"
        registry.parent.mkdir()
        registry.write_bytes(b"not executable or valid registry data; the offline profile must never read it")
        for target, name in ((bridge.extensions, "reload"), (bridge.extensions, "ensure"),
                             (bridge.native, "_client")):
            monkeypatch.setattr(target, name, forbidden, raising=False)
        yield bridge


def test_offline_catalog_is_the_dispatch_allowlist_and_preserves_full_profile(offline):
    async def check():
        server = create_server(offline, offline=True)
        assert {row.name for row in await server.list_tools()} == offline_profile.TOOLS
        assert len(offline_profile.TOOLS) == 15
        assert server.job_tasks is server.artifact_resources is None
        full = {row.name for row in await create_server(offline).list_tools()}
        for name in full - offline_profile.TOOLS:
            assert server._tool_manager.get_tool(name) is None
        assert len(full) == 55 and offline.extensions.current is None
        with pytest.raises(Exception, match="[Uu]nknown tool"):
            await server.call_tool("reload_extensions", {})
    asyncio.run(check())


def test_offline_import_read_analysis_and_program_inspection_never_start_helpers(offline):
    source = offline.config.source_dir / "synthetic"
    source.mkdir(parents=True)
    raw = b'/PROG SAMPLE\n/ATTR\n/MN\n   1: R[1]=2 ;\n/POS\n/END\n'
    (source / "SAMPLE.LS").write_bytes(raw)
    marker = offline.config.state_dir / "uncertain-write.json"
    marker.write_bytes(b'{"status":"uncertain","retain":true}')
    original = marker.read_bytes()
    async def call(server, name, arguments):
        result = await server.call_tool(name, arguments)
        assert not result.is_error
        return json.loads(result.content[0].text)
    async def check():
        server = create_server(offline, offline=True)
        imported = await call(server, "import_workspace", {"source_folder": "synthetic"})
        identifier = imported["artifact_id"]
        listing = await call(server, "artifact_files", {"artifact_id": identifier})
        assert listing["items"][0]["path"] == "SAMPLE.LS"
        for kind in ANALYSES:
            result = await call(server, "analyze_workspace", {"artifact_id": identifier, "kind": kind})
            assert result["evidence_only"]
        await call(server, "inspect_program", {"artifact_id": identifier, "path": "SAMPLE.LS"})
        capabilities = await call(server, "capabilities", {})
        assert capabilities["profile"] == "standalone-offline"
        assert not capabilities["native_utilities"] and not capabilities["extension_execution"]
        assert not capabilities["spawned_workers"] and not capabilities["shared_host"]
    asyncio.run(check())
    assert offline.extensions.current is None and not offline.native.clients
    assert (source / "SAMPLE.LS").read_bytes() == raw and marker.read_bytes() == original


@pytest.mark.parametrize("robots,writes", [(True, False), (False, True), (True, True)])
def test_incompatible_policy_is_refused_before_state_or_factory(tmp_path, robots, writes):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"state_dir": "state", "source_dir": "inputs", "allow_writes": writes,
        "robots": {"r1": {"profile": {"host": "192.0.2.1"}}} if robots else {}}))
    def forbidden(*args, **kwargs):
        pytest.fail("Incompatible profile must not construct a bridge")
    with pytest.raises(ValueError, match="zero robots"):
        with instance_owner.owned_mcp_bridge(path, factory=forbidden, validate=offline_profile.validate):
            pass
    assert not (tmp_path / "state").exists()


def test_preview_cannot_expand_offline_profile(offline):
    with pytest.raises(ValueError, match="Protocol preview"):
        create_server(offline, offline=True, protocol_preview=True)


def test_explicit_offline_cli_policy_overrides_environment_and_refuses_before_start(tmp_path, monkeypatch):
    from fanuc_workbench.bridge import mcp_server
    path = tmp_path / "explicit.json"
    path.write_text(json.dumps({"state_dir": "state", "source_dir": "inputs", "robots": {}, "allow_writes": True}))
    monkeypatch.setenv("FANUC_MCP_SERVER_CONFIG", str(tmp_path / "must-not-open.json"))
    monkeypatch.setattr(sys, "argv", ["fanuc-server", "--offline", "--config", str(path)])
    monkeypatch.setattr(mcp_server, "create_server", lambda *args, **kwargs: pytest.fail("No MCP runtime may start"))
    with pytest.raises(ValueError, match="zero robots"):
        mcp_server.main()
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("closed", [False, True])
def test_offline_receipt_uses_actual_owner_identity_and_preserves_uncertainty(offline, closed):
    identity = {"host_id": "synthetic-host", "pid": 123, "created": "1337", "executable": "fixture-python"}
    context = SimpleNamespace(owner=SimpleNamespace(owner_id="a" * 32,
        identity=SimpleNamespace(model_dump=lambda **kwargs: dict(identity))))
    with offline_profile.lifetime_receipt(offline, context):
        if closed:
            offline.close()
    rows = [json.loads(row) for row in (offline.config.state_dir / "audit.jsonl").read_text().splitlines()]
    assert rows[0]["action"] == "offline_owner_started"
    assert rows[-1]["action"] == ("offline_owner_stopped" if closed else "offline_owner_unverified")
    assert all(row["identity"] == identity and row["owner_id"] == "a" * 32 for row in rows)
    assert all(row["ownership"] == "client-owned-frontdoor" for row in rows)
