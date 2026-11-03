"""Wire contracts survive the SDK migration; no controller or installed host."""
import asyncio
import hashlib
import json
from pathlib import Path
import tomllib
import subprocess

from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge


def test_sdk23_preserves_standalone_wire_definitions(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Schema generation must never launch a worker or controller")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    async def check():
        with RobotBridge(BridgeConfig(tmp_path / "state", tmp_path / "edits")) as bridge:
            rows = [tool.model_dump(by_alias=True, mode="json", exclude_none=True)
                    for tool in await create_server(bridge).list_tools()]
            guides = {"list_workflows", "read_workflow", "read_workflow_reference", "start_backup_task",
                      "start_recording_task", "task_job_status", "wait_job"}
            assert len(rows) == 55 and guides <= {row["name"] for row in rows}
            rows = [row for row in rows if row["name"] not in guides]
            raw = json.dumps(sorted(rows, key=lambda row: row["name"]), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False).encode()
            assert len(rows) == 48
            # Current source catalog, including the published offline argument bounds.
            assert hashlib.sha256(raw).hexdigest() == "0b1e11f51628fe0b1d39fd5b83b29c0f69352711a88bc32c4f4429cd2ebd9799"
    asyncio.run(check())


def test_package_dependencies_pin_the_tested_framework_and_sdk():
    root = Path(__file__).resolve().parents[1]
    desktop = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    bridge = tomllib.loads((root / "packaging/bridge/pyproject.toml").read_text())["project"]
    for deps in (desktop["optional-dependencies"]["mcp"], bridge["dependencies"]):
        assert "mcp==2.3.0" in deps and "universal-codex-tools==26.1103.174507" in deps
    for package in (desktop, bridge):
        assert package["scripts"]["fanuc-mcp-server"] == "fanuc_workbench.bridge.stdio_supervisor:main"
