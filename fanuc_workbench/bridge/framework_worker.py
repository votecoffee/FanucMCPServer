"""Verified deployment entrypoint for the optional app-owned framework host."""
from pathlib import Path
from functools import wraps

from fanuc_workbench.bridge.framework_backend import create_server
from fanuc_workbench.bridge.instance_owner import owned_mcp_bridge


class VerifiedRequests:
    """Recheck deployment/configuration before dispatch, including after queue wait."""

    def __init__(self, requests, verify):
        self.requests, self.verify = requests, verify

    def __getattr__(self, name):
        return getattr(self.requests, name)

    async def run(self, function, *args, **kwargs):
        self.verify()
        @wraps(function)
        def checked(*values, **options):
            self.verify()
            return function(*values, **options)
        return await self.requests.run(checked, *args, **kwargs)


def run(verify, config: Path):
    verify()
    with owned_mcp_bridge(config) as bridge:
        bridge.requests = VerifiedRequests(bridge.requests, verify)
        create_server(bridge).run(transport="stdio")
