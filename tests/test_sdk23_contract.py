"""Wire contracts survive the SDK migration; no controller or installed host."""
import asyncio
import hashlib
import json
from pathlib import Path
import tomllib

from fanuc_workbench.bridge.broker_server import create_server
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge


def test_sdk23_preserves_standalone_and_adapter_wire_definitions(tmp_path):
    async def check():
        with RobotBridge(BridgeConfig(tmp_path / "state", tmp_path / "edits")) as bridge:
            rows = [tool.model_dump(by_alias=True, mode="json", exclude_none=True)
                    for tool in await create_server(bridge).list_tools()]
            for adapter, expected in ((False, "6b9440fcc4a8df3f1ed2613d6ceb318b98db6a90ecd0f01c63225e558a5b2f66"),
                                      (True, "7549293455f9e1cb0a17e6f2c11c2ffc3a2685dc44e3b0c5f7bf88cf53705b79")):
                selected = [row for row in rows if adapter or row["name"] != "list_broker_capabilities"]
                raw = json.dumps(sorted(selected, key=lambda row: row["name"]), sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=False).encode()
                assert len(selected) == (49 if adapter else 48)
                assert hashlib.sha256(raw).hexdigest() == expected
    asyncio.run(check())


def test_package_dependencies_pin_the_tested_framework_and_sdk():
    root = Path(__file__).resolve().parents[1]
    desktop = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    bridge = tomllib.loads((root / "packaging/bridge/pyproject.toml").read_text())["project"]
    for deps in (desktop["optional-dependencies"]["mcp"], bridge["dependencies"]):
        assert "mcp==2.3.0" in deps and "universal-codex-tools==0.14.0" in deps
