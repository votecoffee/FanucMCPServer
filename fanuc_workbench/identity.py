"""Public product identity and compatible configuration selection."""
import os
from pathlib import Path

APP_NAME = "FanucMCPServer"


def application_icon() -> Path:
    return Path(__file__).resolve().parent / "resources" / "fanuc_mcp_server.ico"


def configuration_environment():
    return os.environ.get("FANUC_MCP_SERVER_CONFIG") or os.environ.get("FANUC_WORKBENCH_CONFIG")
