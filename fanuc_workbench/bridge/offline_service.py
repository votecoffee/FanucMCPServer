"""Concurrent saved-evidence clients without controller or job ownership.

Each client drains its own bounded request dispatcher. Only short audit append/
read locks are shared; immutable artifact manifests publish atomically. Starting
or closing this view never constructs/reconciles a job, native worker or plan.
"""
from contextlib import contextmanager
import threading

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.offline_profile import validate
from fanuc_workbench.bridge.requests import RequestDispatcher
from fanuc_workbench.bridge.evidence_io import append_audit, artifact_folder
from fanuc_workbench.bridge.storage import ArtifactStore, RobotBusyError, RobotLocks


OFFLINE_CLIENT_LIMIT = 32
_uncertain_leases = []


class OfflineOperations:
    """Reuse the shared saved-evidence services, excluding job/native facades."""
    def __init__(self, bridge):
        self.bridge = bridge
        self.store = ArtifactStore(bridge.config.state_dir)

    def __getattr__(self, name):
        if name not in {"import_workspace", "read_metadata", "compare", "inspect_program", "audit", "transform_pose"}:
            raise AttributeError(name)
        from fanuc_workbench.bridge.operations import WorkbenchOperations
        function = getattr(WorkbenchOperations, name)
        if name == "transform_pose":
            return function
        return lambda *a, **kw: function(self, *a, **kw)


class OfflineBridge:
    offline_metadata = True
    def __init__(self, config, *, config_path=None):
        validate(config)
        self.config, self.config_path = config, config_path
        self._audit_lock = threading.RLock()
        self._close_lock = threading.RLock()
        self._closed = False
        config.state_dir.mkdir(parents=True, exist_ok=True)
        # The same journal key/path used by existing full/legacy peers. Never
        # replace this with a process-only mutex or a second audit namespace.
        self.audit_locks = RobotLocks(config.state_dir / "audit-locks")
        self.requests = RequestDispatcher(config.max_concurrent_requests, config.max_pending_requests,
            config.robot_lock_timeout_seconds, configuration_supplier=lambda: self.config)
        self.operations = OfflineOperations(self)

    _audit = append_audit
    _folder = artifact_folder

    def list_robots(self):
        return {"allow_writes": False, "write_scope": self.config.write_scope, "robots": []}

    def run_data(self, function, *args, **kwargs):
        from fanuc_workbench.bridge.offline_admission import OfflineWorkGate
        return OfflineWorkGate(self.config.state_dir).run(function, *args,
            cancel_event=self.requests.cancel_event, **kwargs)

    def management_activity(self):
        from fanuc_workbench.bridge.owned_workers import workers
        with self.requests.lock:
            active = len(self.requests.active)
        return {"active_operations": active, "review_plans": 0, "owned_resources": workers.snapshot()}

    def close(self):
        if self.requests.current_request_id:
            raise RuntimeError("Close the offline client outside its active request")
        with self._close_lock:
            if not self._closed:
                self.requests.close()  # Drain only this client's local reads/copies.
                self._closed = True


@contextmanager
def owned_offline_bridge(config_path, *, factory=OfflineBridge):
    config_path = config_path.resolve()
    config = BridgeConfig.load(config_path)
    validate(config)  # Mandatory before admission or state/dispatcher creation.
    # This separate transport budget also bounds direct development entrypoints.
    # Neither these leases nor legacy idle peers occupy the data-work budget.
    slots = RobotLocks(config.state_dir / "mcp-owner" / "metadata-clients")
    lease = None
    for index in range(OFFLINE_CLIENT_LIMIT):
        candidate = slots.acquire(f"metadata-client-{index}")
        try:
            candidate.__enter__()
        except RobotBusyError:
            continue
        lease = candidate
        break
    if lease is None:
        raise SystemExit("Offline metadata connection capacity reached (32); data worker capacity is separate")
    try:
        bridge = factory(config, config_path=config_path)
    except BaseException:
        lease.__exit__(None, None, None)
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
