"""Public product identity and compatible configuration selection."""
import os

APP_NAME = "FanucMCPServer"


def configuration_environment():
    return os.environ.get("FANUC_MCP_SERVER_CONFIG") or os.environ.get("FANUC_WORKBENCH_CONFIG")
