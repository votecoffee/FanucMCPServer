"""Optional Universal Tools adapter; the standalone MCP surface stays unchanged."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from fanuc_workbench.bridge.mcp_server import create_server as standalone_server
from fanuc_workbench.bridge.service import RobotBridge


def capability_catalog(bridge: RobotBridge) -> dict:
    rows = sorted(bridge.extensions.discovery_snapshot(), key=lambda row: row["id"])
    if any(len(row["id"]) > 128 for row in rows):
        raise ValueError("An operation ID exceeds the broker metadata limit")
    capabilities = [{"id": row["id"], "description": row["description"],
        "tags": ["FANUC", row["effect"], row["extension_id"]],
        "discovery": {"tool": "describe_operation", "arguments": {"operation_id": row["id"]}}}
        for row in rows]
    # Include operation revisions even when their search descriptions are identical.
    revision = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode("utf-8")).hexdigest()
    result = {"schema_version": 1, "catalog_revision": "fanuc.catalog.v1:" + revision,
              "capabilities": capabilities}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
        raise ValueError("Discovery catalog exceeds the broker metadata limit")
    return result


def create_server(bridge: RobotBridge):
    from mcp.types import ToolAnnotations

    server = standalone_server(bridge)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def list_broker_capabilities() -> dict:
        """Discovery-only installation metadata. No robot access, worker startup or approvals."""
        return await bridge.requests.run(capability_catalog, bridge, control=True)

    return server


async def service_manifest(bridge: RobotBridge, python: Path, config: Path, *, allow_writes=False) -> dict:
    tools = await create_server(bridge).list_tools()
    write_tools = sorted(tool.name for tool in tools
                         if not tool.annotations or tool.annotations.readOnlyHint is not True)
    variables = sorted({name for access in bridge.config.robots.values()
                        for name in (access.password_env, access.http_read_password_env) if name})
    return {"schema_version": 1, "id": "fanuc-mcp-server", "display_name": "FanucMCPServer",
        "description": "FANUC files, backups, diagnostics and reviewed changes through app-owned policy.",
        "transport": {"type": "stdio", "command": str(python.resolve()),
            "args": ["-B", "-m", "fanuc_workbench.bridge.broker_server", "--config", str(config.resolve())],
            "cwd": str(config.resolve().parent), "env_vars": variables},
        "session_mode": "exclusive", "resource_id": "fanuc-mcp-server", "lease_seconds": 900.0,
        "timeout_seconds": 60.0, "max_argument_bytes": 2 * 1024 * 1024,
        "allow_writes": bool(allow_writes), "allowed_write_tools": write_tools if allow_writes else [],
        "capability_catalog": {"schema_version": 1, "tool": "list_broker_capabilities", "arguments": {}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, help="Export a local service descriptor instead of starting MCP")
    parser.add_argument("--allow-writes", action="store_true", help="Permit exact non-read outer tools at the broker; app policy still applies")
    args = parser.parse_args()
    with RobotBridge.from_config(args.config) as bridge:
        if args.manifest:
            descriptor = asyncio.run(service_manifest(bridge, Path(sys.executable), args.config,
                                                       allow_writes=args.allow_writes))
            args.manifest.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
        else:
            create_server(bridge).run(transport="stdio")


if __name__ == "__main__":
    main()
