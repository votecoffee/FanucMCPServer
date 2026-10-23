"""Reporting never probes a robot or grants restart; only synthetic restart effects."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

import pytest

from universal_tools.management import ManagedService, ManagedStatus, ManagementClient, RestartResult
from fanuc_workbench.bridge import management
from test_bridge_operations import bridge_case
from test_framework_host import app_publication
from test_robot_bridge import ORIGINAL, PROPOSED, digest


def plugin(tmp_path):
    root = tmp_path / "Plugin With Spaces"
    root.mkdir()
    pointer = root / "runtime.json"
    pointer.write_text(json.dumps({"plugin_id": management.PLUGIN_ID}))
    (root / "plugin.json").write_text(json.dumps({"name": "fanuc-mcp-server", "version": "1.0.0"}))
    return pointer


def test_cached_callback_never_reads_files_or_controller(bridge_case, tmp_path, monkeypatch):
    bridge, robot, _ = bridge_case
    reporting = management.ServiceReporting(bridge, runtime=plugin(tmp_path), registry=tmp_path / "registry")
    reporting.refresh()
    def forbidden(*_):
        pytest.fail("Status callback must read only its cached snapshot")
    monkeypatch.setattr(bridge, "management_activity", forbidden)
    monkeypatch.setattr(management, "installed_version", forbidden)
    monkeypatch.setattr(management, "plugin_version", forbidden)
    status = reporting.status()
    assert status.state == "running" and not status.restart_allowed
    assert status.versions["plugin"].installed == status.versions["plugin"].running == "1.0.0"
    assert all(item.available is None and not item.update_pending for item in status.versions.values())
    assert robot.connects == robot.uploads == 0
    # Callers cannot mutate subsequent HTTP snapshots through returned models.
    status.versions.clear()
    assert reporting.status().versions


def test_installed_and_running_versions_remain_distinct(bridge_case, tmp_path, monkeypatch):
    bridge, _, _ = bridge_case
    pointer = plugin(tmp_path)
    reporting = management.ServiceReporting(bridge, runtime=pointer)
    original = management.installed_version
    monkeypatch.setattr(management, "installed_version", lambda name: "9.0.0" if name == "universal-codex-tools" else original(name))
    (pointer.parent / "plugin.json").write_text('{"name":"fanuc-mcp-server","version":"2.0.0"}')
    reporting.refresh()
    versions = reporting.status().versions
    assert versions["plugin"].installed == "2.0.0" and versions["plugin"].running == "1.0.0"
    assert versions["framework"].installed == "9.0.0" and versions["framework"].running == "0.20.0"
    assert versions["framework"].available is None and not versions["framework"].update_pending


def test_stable_selection_reports_pending_backend_without_changing_live_generation(bridge_case, tmp_path):
    from fanuc_workbench.bridge import stable_launcher as launcher
    bridge, robot, _ = bridge_case
    pointer = plugin(tmp_path)
    pointer.write_bytes(launcher.encoded({"plugin_id": management.PLUGIN_ID, "stable_root": str(tmp_path)}))
    reporting = management.ServiceReporting(bridge, runtime=pointer)
    manifest = {"release": {"display_version": "26.1023.235959"}, "framework": "0.20.0", "sdk": "2.3.0",
        "installed_plugin_version": "1.0.0"}
    raw = launcher.encoded(manifest); identifier = launcher.digest(raw)
    snapshot = tmp_path / "launcher/snapshots" / identifier
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.json").write_bytes(raw)
    (tmp_path / "launcher/current.json").write_bytes(launcher.encoded({"schema_version": 1, "snapshot_id": identifier}))
    reporting.refresh()
    versions = reporting.status().versions
    assert versions["app"].running == management.APP_VERSION
    assert versions["app"].installed == "26.1023.235959" and versions["app"].update_pending
    assert versions["plugin"].installed == versions["plugin"].running == "1.0.0"
    assert not reporting.status().restart_allowed and robot.connects == robot.uploads == 0


def test_busy_plans_stale_requests_and_concurrent_duplicates_never_restart(bridge_case, tmp_path):
    bridge, robot, config = bridge_case
    (config.source_dir / "MAIN.LS").write_bytes(PROPOSED)
    registry = tmp_path / "registry"
    reporting = management.ServiceReporting(bridge, registry=registry, interval=60).start()
    client = ManagementClient(registry)
    done = threading.Event()
    future = None
    try:
        idle = client.inspect(reporting.agent.record)
        plan = bridge.prepare_upload("r1", "md:MAIN.LS", "MAIN.LS", digest(ORIGINAL))
        future = bridge.requests.submit(done.wait, 30)
        bridge.operations.jobs.start("synthetic", "r1", lambda _: done.wait(30))
        connects = robot.connects
        reporting.refresh()
        busy = client.inspect(reporting.agent.record)
        assert busy["status"]["state"] == "busy" and busy["status"]["active_operations"] == 2
        assert "1 retained review plans" in busy["status"]["message"]
        key = secrets.token_hex(16)
        def restart(row, request):
            return client.restart(row["app_id"], row["instance_id"], row["revision"], request)
        assert restart(idle, secrets.token_hex(16))["outcome"] == "blocked"
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: restart(busy, key), range(4)))
        assert all(receipt == receipts[0] and receipt["outcome"] == "blocked" for receipt in receipts)
        assert restart(dict(busy, revision="f" * 64), key)["outcome"] == "uncertain"
        assert plan["plan_id"] in bridge._plans
        assert robot.connects == connects and robot.uploads == 0
    finally:
        done.set()
        if future:
            future.result(timeout=5)
        reporting.close()


def test_instances_associate_exact_plugin_and_stop_cleanly(bridge_case, tmp_path):
    bridge, robot, _ = bridge_case
    pointer, registry = plugin(tmp_path), tmp_path / "registry"
    with management.report_service(bridge, runtime=pointer, registry=registry) as first:
        with management.report_service(bridge, runtime=pointer, registry=registry) as second:
            assert first.agent.instance_id != second.agent.instance_id
            rows = ManagementClient(registry).snapshot()["services"]
            assert len(rows) == 2 and {row["plugin_id"] for row in rows} == {management.PLUGIN_ID}
            assert all(not row["status"]["restart_allowed"] for row in rows)
            assert all("supervisor" in row["status"]["restart_reason"] for row in rows)
    assert not first.thread.is_alive() and not second.thread.is_alive()
    rows = ManagementClient(registry).snapshot()["services"]
    assert all(row["status"]["state"] == "stopped" for row in rows)
    assert robot.connects == robot.uploads == 0


def test_stale_cache_and_host_activity_unknown_disable_restart(tmp_path):
    reporting = management.ServiceReporting(registry=tmp_path)
    reporting.refresh()
    assert reporting.status().state == "degraded" and "unknown" in reporting.status().message
    reporting.updated -= 6
    assert "stale" in reporting.status().message and not reporting.status().restart_allowed
    assert reporting.deny_restart(None).outcome == "blocked"


def test_failed_reporting_start_does_not_interrupt_mcp_or_leak_exception(tmp_path, monkeypatch, capsys):
    def failed(_):
        raise OSError("private-token-and-robot-address")
    monkeypatch.setattr(ManagedService, "start", failed)
    with management.report_service(registry=tmp_path) as reporting:
        assert reporting is None
    logged = capsys.readouterr()
    assert not logged.out and "tray reporting unavailable" in logged.err
    assert "private-token" not in logged.err


def test_real_stdio_owner_reports_and_clean_exit_stops_registration(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    config = tmp_path / "empty.json"
    config.write_text('{"state_dir":"state","source_dir":"edits","robots":{}}')
    registry = tmp_path / "registry"
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "fanuc_workbench.bridge.mcp_server",
            "--config", str(config), "--management-registry", str(registry)],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            assert len((await client.list_tools()).tools) == 55
            rows = ManagementClient(registry).snapshot()["services"]
            assert len(rows) == 1 and rows[0]["status"]["state"] == "running"
            assert rows[0]["plugin_id"] is None and not rows[0]["status"]["restart_allowed"]
    asyncio.run(scenario())
    assert ManagementClient(registry).snapshot()["services"][0]["status"]["state"] == "stopped"


def test_host_registers_only_its_serving_owner(app_publication, tmp_path, monkeypatch):
    from fanuc_workbench.bridge.framework_host import main
    import universal_tools.framework
    state, _, publication, _ = app_publication
    registry = tmp_path / "registry"
    assert not registry.exists()  # Publication alone does not register a service.
    observations = []
    def serve(actual_state, actual_publication, *, skills):
        assert skills.identifier == "io.modelcontextprotocol/skills"
        assert actual_state == state and actual_publication == publication
        rows = ManagementClient(registry).snapshot()["services"]
        assert len(rows) == 1 and rows[0]["status"]["state"] == "degraded"
        assert not rows[0]["status"]["restart_allowed"]
        observations.append(rows[0]["instance_id"])
    monkeypatch.setattr(universal_tools.framework, "serve_application", serve)
    main(["--state", str(state), "--management-registry", str(registry), "serve"])
    assert len(observations) == 1
    assert ManagementClient(registry).snapshot()["services"][0]["status"]["state"] == "stopped"


def test_server_factories_do_not_register_backend_or_proxy_owners(bridge_case, tmp_path, monkeypatch):
    from fanuc_workbench.bridge.mcp_server import create_server
    from fanuc_workbench.bridge.framework_backend import create_server as backend_server
    registry = tmp_path / "registry"
    monkeypatch.setenv("FANUC_MCP_MANAGEMENT_REGISTRY", str(registry))
    bridge, robot, _ = bridge_case
    create_server(bridge)
    backend_server(bridge)
    assert not registry.exists()
    assert robot.connects == robot.uploads == 0


def test_lost_restart_response_and_new_process_keep_uncertain_barrier(tmp_path):
    # This deliberately restarts no FANUC service. The only effect is one line
    # written by a disposable synthetic supervisor before its process exits.
    registry, counter = tmp_path / "registry", tmp_path / "effects.txt"
    script = tmp_path / "crash.py"
    script.write_text(f'''import os, threading
from pathlib import Path
from universal_tools.management import ManagedService, ManagedStatus
def crash(request):
    with Path({str(counter)!r}).open('a') as output:
        output.write(request.request_id + '\\n')
        output.flush()
        os.fsync(output.fileno())
    os._exit(23)
with ManagedService('fanuc-test-supervisor', 'Synthetic',
        lambda: ManagedStatus(restart_allowed=True), restart=crash, registry={str(registry)!r}):
    threading.Event().wait()
''')
    process = subprocess.Popen([sys.executable, "-B", str(script)], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    client = ManagementClient(registry)
    key = secrets.token_hex(16)
    try:
        deadline = time.monotonic() + 15
        while not (records := client.registry.records()[0]):
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(.02)
        row = client.inspect(records[0])
        assert client.restart(row["app_id"], row["instance_id"], row["revision"], key)["outcome"] == "uncertain"
        assert process.wait(timeout=5) == 23
        assert counter.read_text().splitlines() == [key]
        effects = []
        def never(request):
            effects.append(request.request_id)
            return RestartResult(outcome="accepted", message="Synthetic only")
        with ManagedService("fanuc-test-supervisor", "Replacement", lambda: ManagedStatus(restart_allowed=True),
                            restart=never, registry=registry) as replacement:
            row = client.inspect(replacement.record)
            assert not row["status"]["restart_allowed"]
            assert replacement.pending_restarts()[0]["request_id"] == key
            assert client.restart(row["app_id"], row["instance_id"], row["revision"], secrets.token_hex(16))["outcome"] == "blocked"
            assert effects == [] and counter.read_text().splitlines() == [key]
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
