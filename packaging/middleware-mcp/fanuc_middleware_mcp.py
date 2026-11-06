"""Opt-in stdio adapter for an operator-selected FANUC middleware checkout."""

import argparse
import importlib.util
import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations


def create_server(cli: Path) -> MCPServer:
    spec = importlib.util.spec_from_file_location("fanuc_middleware_cli", cli)
    if spec is None or spec.loader is None:
        raise ValueError("Expected the middleware Python entry point")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)

    server = MCPServer("FANUC saved-listing middleware")

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )
    def fanuc_positions(input_path: str, offset: int = 0, limit: int = 100) -> CallToolResult:
        """Read one page of positions from an absolute local FANUC .LS path."""
        status, result = entry.invoke(
            ["positions", "--input", input_path, "--offset", str(offset), "--limit", str(limit)]
        )
        payload = {**json.loads(result), "exit_code": status}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
            is_error=status != 0,
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cli",
        type=Path,
        required=True,
        help="Absolute path to scripts/fanuc_middleware.py in a trusted checkout",
    )
    args = parser.parse_args()
    if not args.cli.is_absolute() or args.cli.name != "fanuc_middleware.py":
        parser.error("--cli must be an absolute path to fanuc_middleware.py")
    create_server(args.cli).run(transport="stdio")
