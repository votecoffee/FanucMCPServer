"""Cached, reporting-only tray integration for the owning MCP process.

No restart supervisor exists for chat-owned stdio servers or the optional host.
Reporting never admits a restart, reads a controller, or reconciles app effects.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import threading
import time

from universal_tools import __version__ as FRAMEWORK_VERSION
from universal_tools.management import ComponentVersion, ManagedService, ManagedStatus, RestartResult

from fanuc_workbench import __version__ as APP_VERSION
from fanuc_workbench.version import display_if_timestamp


PLUGIN_ID = "fanuc-mcp-server@fanuc-mcp-server-local"
RESTART_REASON = (
    "No surviving app-owned restart supervisor. Finish all chats' work and reconcile "
    "review plans, jobs and uncertain effects before an operator reloads the service."
)


def installed_version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def plugin_version(runtime):
    if runtime is None:
        return None
    # Only the explicitly installed launcher supplies this local pointer. Never
    # guess an association from a process name or an arbitrary tool argument.
    def read(path):
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Plugin metadata exceeds its limit")
        return json.loads(raw)
    pointer = read(runtime)
    if pointer.get("plugin_id") != PLUGIN_ID:
        return None
    manifest = read(runtime.parent / "plugin.json")
    version = manifest.get("version")
    if (manifest.get("name") != "fanuc-mcp-server" or not isinstance(version, str)
            or not 1 <= len(version) <= 128):
        raise ValueError("Invalid installed plugin metadata")
    return display_if_timestamp(version)


class ServiceReporting:
    """One cached status per owner; the HTTP callback does no file/native I/O."""

    def __init__(self, bridge=None, *, runtime=None, registry=None, interval=1.0, role="per-chat"):
        self.bridge = bridge
        self.runtime = Path(runtime) if runtime is not None else None
        self.interval = interval
        if role not in {"per-chat", "app-host", "validation"}:
            raise ValueError("Unknown service role")
        self.role = role
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = None
        self.updated = time.monotonic()
        self.distribution = ("fanuc-mcp-server-bridge" if installed_version("fanuc-mcp-server-bridge") != "unknown"
                             else "fanuc-mcp-server")
        self.running = {
            "app": APP_VERSION,
            "framework": FRAMEWORK_VERSION,
            "sdk": installed_version("mcp"),
        }
        self.running_plugin = plugin_version(self.runtime)
        self.cached = ManagedStatus(state="starting", message="Starting local status reporting",
                                    restart_allowed=False, restart_reason=RESTART_REASON)
        self.agent = ManagedService("fanuc-mcp-server", "FanucMCPServer", self.status,
            restart=self.deny_restart, plugin_id=PLUGIN_ID if self.running_plugin else None, registry=registry)

    @staticmethod
    def deny_restart(_request):
        # Deliberately no admission fence or restart effect: reporting cannot
        # prove quiescence of other chats, retained workers or native effects.
        return RestartResult(outcome="blocked", message=RESTART_REASON)

    def refresh(self):
        versions = {key: ComponentVersion(installed=(display_if_timestamp(installed_version(distribution))
                                                   if key == "app" else installed_version(distribution)), running=self.running[key],
                                         available=None, update_pending=False)
                    for key, distribution in (("app", self.distribution), ("framework", "universal-codex-tools"),
                                              ("sdk", "mcp"))}
        if self.running_plugin:
            current = plugin_version(self.runtime)
            if current is None:
                raise ValueError("Plugin identity changed")
            versions["plugin"] = ComponentVersion(installed=current, running=self.running_plugin,
                                                   available=None, update_pending=False)
        # The selected immutable backend may differ from this live generation.
        # Observe only pointer/manifest metadata here; status never loads code,
        # adopts a process, touches a controller or declares an update healthy.
        if self.runtime is not None:
            pointer = json.loads(self.runtime.read_bytes())
            if pointer.get("stable_root"):
                import hashlib
                root = Path(pointer["stable_root"])
                from fanuc_workbench.bridge.stable_launcher import read, parse
                selection = parse(read(root / "launcher/current.json"))
                snapshot_id = selection["snapshot_id"]
                if (not isinstance(snapshot_id, str) or len(snapshot_id) != 64
                        or any(c not in "0123456789abcdef" for c in snapshot_id)):
                    raise ValueError("Invalid selected launcher identity")
                raw = read(root / "launcher/snapshots" / snapshot_id / "manifest.json", 8388608)
                if hashlib.sha256(raw).hexdigest() != snapshot_id:
                    raise ValueError("Selected launcher metadata hash differs")
                selected = parse(raw)
                for key, installed in (("app", selected["release"]["display_version"]),
                                       ("framework", selected["framework"]), ("sdk", selected["sdk"]),
                                       ("plugin", selected["installed_plugin_version"])):
                    running = self.running_plugin if key == "plugin" else self.running[key]
                    versions[key] = ComponentVersion(installed=installed, running=running,
                        available=None, update_pending=installed != running)
        if self.bridge is None:
            status = ManagedStatus(state="degraded", message=(
                "Shared app-host owner. Retained-worker activity is unknown; restart is disabled."),
                versions=versions, restart_allowed=False, restart_reason=RESTART_REASON)
        else:
            activity = self.bridge.management_activity()
            active = activity["active_operations"]
            plans = activity["review_plans"]
            resources = activity["owned_resources"]
            status = ManagedStatus(state="degraded" if resources["uncertain"] else ("busy" if active else "running"), active_operations=active,
                message=f"{self.role} stdio worker: {active} active/queued operations; {plans} retained review plans; "
                        f"{resources['active']}/{resources['limit']} owned workers, {resources['uncertain']} uncertain. Lifetime follows its client/launcher pipe.",
                versions=versions, restart_allowed=False,
                restart_reason=("Review plans are retained. " + RESTART_REASON) if plans else RESTART_REASON)
        with self.lock:
            self.cached, self.updated = status, time.monotonic()

    def status(self):
        with self.lock:
            status = self.cached.model_copy(deep=True)
            age = time.monotonic() - self.updated
        if age > 5:
            return status.model_copy(update={"state": "degraded", "message": "Local management status is stale",
                                             "restart_allowed": False, "restart_reason": "Status is stale; restart is disabled"})
        return status

    def _collect(self):
        while not self.stopped.is_set():
            try:
                self.refresh()
            except Exception:
                with self.lock:
                    self.cached = self.cached.model_copy(update={
                        "state": "degraded", "message": "Local status is unavailable; no controller probe was attempted",
                        "restart_allowed": False, "restart_reason": RESTART_REASON})
                    self.updated = time.monotonic()
            self.stopped.wait(self.interval)

    def start(self):
        self.refresh()
        self.agent.start()
        self.thread = threading.Thread(target=self._collect, name="fanuc-status-cache", daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.agent.close()


@contextmanager
def report_service(bridge=None, **options):
    reporting = None
    def close():
        try:
            reporting.close()
        except Exception:
            print("FanucMCPServer tray reporting cleanup was incomplete.", file=sys.stderr)
    try:
        reporting = ServiceReporting(bridge, **options)
        reporting.start()
    except Exception:
        if reporting is not None:
            close()
        # Optional local monitoring must not interrupt the engineering service.
        # No exception detail, paths, tokens, endpoints or robot data are logged.
        print("FanucMCPServer tray reporting unavailable; the MCP service remains available.", file=sys.stderr)
        reporting = None
    try:
        yield reporting
    finally:
        if reporting is not None:
            close()
