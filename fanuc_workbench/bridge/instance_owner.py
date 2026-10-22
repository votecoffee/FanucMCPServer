"""Exclusive MCP worker ownership, independent of optional tray reporting.

The OS lease follows the actual worker through its drain, not its launcher PID.
It neither starts a supervisor nor recovers/replays interrupted operations.
"""
from contextlib import contextmanager
import sys

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.storage import RobotBusyError, RobotLocks


# If drain fails, retain the live lease until process exit. A failed close is
# not evidence that it is safe to admit another native-owning process.
_uncertain_leases = []


@contextmanager
def owned_mcp_bridge(config_path, *, factory=None):
    config_path = config_path.resolve()
    config = BridgeConfig.load(config_path)
    lease = RobotLocks(config.state_dir / "mcp-owner").acquire("native-worker-owner")
    try:
        lease.__enter__()
    except RobotBusyError:
        raise SystemExit(
            "FanucMCPServer already has an MCP worker for this policy state. "
            "Close its owning client and allow shutdown to finish before reconnecting. "
            "No additional worker, controller connection or automatic restart was started."
        ) from None
    try:
        if factory is None:
            from fanuc_workbench.bridge.service import RobotBridge
            factory = RobotBridge
        bridge = factory(config, config_path=config_path)
    except BaseException:
        lease.__exit__(*sys.exc_info())
        raise
    try:
        yield bridge
    finally:
        try:
            bridge.close()
        except BaseException:
            _uncertain_leases.append(lease)
            raise
        else:
            lease.__exit__(None, None, None)
