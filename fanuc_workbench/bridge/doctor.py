from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


async def diagnose(config: Path, probe: str | None = None) -> dict:
    # Diagnostics are temporary validation workers, never live manager owners.
    with TemporaryDirectory(prefix="fanuc-doctor-registry-") as registry:
        return await _diagnose(config, probe, registry)


async def _diagnose(config: Path, probe: str | None, registry: str) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from fanuc_workbench.bridge.config import BridgeConfig

    loaded = BridgeConfig.load(config)
    if probe is not None and probe not in loaded.robots:
        raise ValueError("Probe requires a configured robot ID")
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    environment.update(FANUC_MCP_MANAGEMENT_REGISTRY=registry, FANUC_MCP_INSTANCE_ROLE="validation")
    params = StdioServerParameters(command=sys.executable,
                                   args=["-B", "-m", "fanuc_workbench.bridge.mcp_server", "--config", str(config.resolve())],
                                   env=environment)
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        tools = await session.list_tools()
        robots = await session.call_tool("list_robots", {})
        if robots.is_error:
            raise RuntimeError("Robot configuration tool failed")
        capabilities = await session.call_tool("capabilities", {})
        if capabilities.is_error:
            raise RuntimeError("Runtime capability check failed")
        runtime = json.loads(capabilities.content[0].text)["runtime"]
        names = [tool.name for tool in tools.tools]
        result = {"status": "ok", "python": sys.version.split()[0], "mcp": importlib.metadata.version("mcp"),
                  "tool_count": len(names), "tools": names, "configured_robots": sorted(loaded.robots),
                  "writes_enabled": loaded.allow_writes, "read_only": loaded.read_only,
                  "write_scope": loaded.write_scope,
                  "approval_mode": loaded.approval_mode,
                  "controller_contacted": probe is not None, "gui_imported": runtime["gui_imported"],
                  "caller_gui_imported": any(x.startswith("PySide6") for x in sys.modules),
                  "client_confirmation_ui": "verify separately in your chat client"}
        if probe is not None:
            checked = await session.call_tool("probe_robot", {"robot_id": probe})
            if checked.is_error:
                result.update(status="error", probe={"error": "Probe tool failed"})
            else:
                result["probe"] = json.loads(checked.content[0].text)
                if not result["probe"]["reachable"]:
                    result["status"] = "error"
        return result


def main():
    parser = argparse.ArgumentParser(description="Check local FANUC bridge setup and MCP protocol")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--probe", help="Optional explicit robot ID for a read-only connection probe")
    args = parser.parse_args()
    try:
        result = asyncio.run(diagnose(args.config, args.probe))
    except Exception as exc:
        result = {"status": "error", "error_type": type(exc).__name__, "controller_contacted": bool(args.probe)}
    print(json.dumps(result, indent=2))
    if result["status"] != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
