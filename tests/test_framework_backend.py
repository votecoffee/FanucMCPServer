import asyncio
import json
import sys
from pathlib import Path

import pytest

from fanuc_workbench.bridge.framework_backend import capability_catalog, create_server, service_manifest
from fanuc_workbench.bridge.mcp_server import create_server as direct_server
from fanuc_workbench.bridge.service import RobotBridge


def test_app_backend_catalog_does_not_start_workers_or_expose_robot_settings(tmp_path, monkeypatch):
    config = tmp_path / "policy.json"
    config.write_text(json.dumps({"state_dir": "state", "source_dir": "edits", "robots": {"private_robot": {"profile": {"host": "192.0.2.1"}}}}))
    with RobotBridge.from_config(config) as bridge:
        def forbidden(*args, **kwargs):
            pytest.fail("Metadata must not reload workers or access a controller")
        monkeypatch.setattr(bridge.extensions, "reload", forbidden)
        monkeypatch.setattr(bridge, "_connect", forbidden, raising=False)
        before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
        catalog = capability_catalog(bridge)
        assert capability_catalog(bridge) == catalog
        assert bridge.extensions.current is None
        assert before == sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
        assert set(catalog) == {"schema_version", "catalog_revision", "capabilities"}
        assert len(catalog["capabilities"]) >= 35
        assert "private_robot" not in json.dumps(catalog) and "192.0.2.1" not in json.dumps(catalog)
        for row in catalog["capabilities"]:
            assert row["discovery"] == {"tool": "describe_operation", "arguments": {"operation_id": row["id"]}}


def test_app_backend_keeps_direct_schemas_and_exports_explicit_retained_policy(tmp_path):
    config = tmp_path / "policy.json"
    config.write_text(json.dumps({"state_dir": "state", "source_dir": "edits", "robots": {"r1": {"profile": {"host": "192.0.2.1"}, "password_env": "SYNTHETIC_FTP_PASSWORD"}}}))
    async def check(bridge):
        direct = {tool.name: tool.model_dump(by_alias=True) for tool in await direct_server(bridge).list_tools()}
        backend = {tool.name: tool.model_dump(by_alias=True) for tool in await create_server(bridge).list_tools()}
        assert len(direct) == 51 and len(backend) == 52
        assert {name: backend[name] for name in direct} == direct
        assert backend["list_app_capabilities"]["annotations"]["readOnlyHint"] is True
        descriptor = await service_manifest(bridge, Path(sys.executable), config)
        assert descriptor["session_mode"] == "exclusive"
        assert descriptor["allow_writes"] is False and descriptor["allowed_write_tools"] == []
        assert descriptor["transport"]["env_vars"] == ["SYNTHETIC_FTP_PASSWORD"]
        assert "--config" in descriptor["transport"]["args"]
        enabled = await service_manifest(bridge, Path(sys.executable), config, allow_writes=True)
        assert enabled["allow_writes"] is True and "commit_change" in enabled["allowed_write_tools"]
        assert "prepare_upload" in enabled["allowed_write_tools"]
        assert "list_robots" not in enabled["allowed_write_tools"]
    with RobotBridge.from_config(config) as bridge:
        asyncio.run(check(bridge))
