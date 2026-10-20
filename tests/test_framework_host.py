"""App-owned SDK profile, using the real FANUC adapter and synthetic transports."""
import asyncio
import copy
import inspect
import json
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("universal_tools.framework")
from universal_tools.framework import ApplicationHost
from universal_tools.models import atomic_json
from fanuc_workbench.bridge import framework_host
from fanuc_workbench.bridge.framework_backend import create_server
from fanuc_workbench.bridge.policy_editor import load_policy, review_policy, save_policy
from fanuc_workbench.bridge.service import RobotBridge
from test_robot_bridge import MemoryRobot, ORIGINAL, PROPOSED, digest


def data(result):
    assert not result.is_error, result.model_dump_json()
    return result.structured_content or json.loads(result.content[0].text)


def test_publication_refuses_output_inside_package_before_creating_state(tmp_path):
    package = tmp_path / "fanuc_workbench"; package.mkdir()
    config = tmp_path / "policy.json"; config.write_text("{}")
    state = package / "state"
    with pytest.raises(ValueError, match="recursive snapshots"):
        framework_host.publish(state, config, package=package)
    assert not state.exists()


@pytest.fixture
def app_publication(tmp_path):
    config = tmp_path / "robot.local.json"
    config.write_text(json.dumps({"state_dir": "evidence", "source_dir": "edits", "allow_writes": True,
        "approval_mode": "elicitation", "robots": {"fixture": {"profile": {"host": "192.0.2.1"},
        "writable_files": ["md:MAIN.LS"]}}}))
    (tmp_path / "edits").mkdir(); (tmp_path / "edits/MAIN.LS").write_bytes(PROPOSED)
    package = tmp_path / "source/fanuc_workbench"
    shutil.copytree(Path(framework_host.__file__).resolve().parents[1], package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Replace only this disposable package's transport factory, never production
    # source, installed runtimes, real controller policies or environment hooks.
    (package / "backends/factory.py").write_text(
        "from fanuc_workbench.backends.base import RobotBackend\n"
        "from fanuc_workbench.core.models import RemoteEntry\n"
        "from fanuc_workbench.services.transfer import TransferLimitError\n"
        f"ORIGINAL = {ORIGINAL!r}\n" + inspect.getsource(MemoryRobot) + "\n"
        "robot = MemoryRobot()\ndef create_backend(profile): return robot\n")
    state = tmp_path / "app"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    publication = framework_host.publish(state, config, package=package, port=port, allow_writes=True)
    return state, config, publication, package


def test_publication_is_source_pinned_repeatable_and_preserves_operator_policy(app_publication):
    state, config, publication, package = app_publication
    before = publication.read_bytes()
    document = json.loads(before)
    backend = document["backends"][0]
    worker = Path(backend["artifact"]["path"])
    assert digest(worker.read_bytes()) == backend["artifact"]["sha256"]
    assert not (state / "framework").exists() and not list((state / "services").glob("*.json"))
    port = json.loads((state / "settings.json").read_bytes())["port"]
    framework_host.publish(state, config, package=package, port=port)
    assert publication.read_bytes() == before
    snapshot = load_policy(publication, "application")
    draft = snapshot.document
    draft["backends"][0]["service"].update(allow_writes=False, allowed_write_tools=[])
    saved, backup = save_policy(review_policy(snapshot, draft))
    assert backup.read_bytes() == before and saved.document["backends"][0]["revision"] != backend["revision"]
    framework_host.publish(state, config, package=package, port=port)
    service = json.loads(publication.read_bytes())["backends"][0]["service"]
    assert service["allow_writes"] is False and service["allowed_write_tools"] == []
    bad = copy.deepcopy(saved.document); bad["backends"][0]["service"]["transport"]["args"].append("--bad")
    with pytest.raises(ValueError, match="Only access policy"):
        review_policy(saved, bad)


@pytest.mark.parametrize("changed", ["package", "policy", "bytecode"])
def test_worker_refuses_changed_package_or_policy_before_startup(app_publication, changed):
    state, config, publication, _ = app_publication
    worker = Path(json.loads(publication.read_bytes())["backends"][0]["artifact"]["path"])
    if changed == "package":
        (worker.parent / "fanuc_workbench/bridge/config.py").write_text("raise RuntimeError('must not execute')")
    elif changed == "policy":
        config.write_text(config.read_text() + "\n")
    else:
        cache = worker.parent / "fanuc_workbench/__pycache__"
        cache.mkdir(); (cache / "config.pyc").write_bytes(b"untracked bytecode")
    result = subprocess.run([sys.executable, "-B", str(worker)], capture_output=True, timeout=20)
    assert result.returncode != 0 and b"publish a new backend revision" in result.stderr
    assert b"must not execute" not in result.stderr


def test_actual_fanuc_framework_retains_plans_approval_results_and_frontend_schemas(app_publication):
    state, config, publication, package = app_publication

    async def scenario():
        from mcp.types import ElicitResult
        from universal_tools.elicitation import ElicitationTarget
        with RobotBridge.from_config(config, lambda _: MemoryRobot()) as direct:
            server = create_server(direct)
            direct_tools = {tool.name: tool.model_dump(by_alias=True, mode="json", exclude_none=True) for tool in await server.list_tools()}
            direct_result = await server.call_tool("list_robots", {})
        async with ApplicationHost(state, publication) as host:
            owner, foreign = host.client(), host.client()
            before = [row.model_dump(by_alias=True, mode="json") for row in await host.create_server().list_tools()]
            assert len(before) == 14 and len(direct_tools) == 49
            for name, definition in direct_tools.items():
                described = data(await owner.invoke("describe_tool", {"tool_id": "fanuc-mcp-server/" + name}))
                assert described["definition"] == definition
            context = data(await owner.invoke("acquire_service", {"service_id": "fanuc-mcp-server"}))["context_id"]
            assert (await foreign.invoke("acquire_service", {"service_id": "fanuc-mcp-server"})).is_error
            async def call(name, arguments, request=None, client=owner, relay=None):
                tool_id = "fanuc-mcp-server/" + name
                definition = data(await client.invoke("describe_tool", {"tool_id": tool_id}))
                payload = {"tool_id": tool_id, "arguments": arguments,
                    "catalog_revision": definition["catalog_revision"], "context_id": context}
                if request: payload["request_id"] = request
                return await client.invoke("call_write_tool" if request else "call_read_tool", payload, relay=relay)
            result = await call("list_robots", {})
            assert data(result) == data(direct_result)
            assert result.model_dump(by_alias=True, mode="json", exclude_none=True) == direct_result.model_dump(by_alias=True, mode="json", exclude_none=True)
            assert (await call("list_robots", {}, client=foreign)).is_error
            assert data(await call("read_file", {"robot_id": "fixture", "path": "md:MAIN.LS"}))["sha256"] == digest(ORIGINAL)
            plan = data(await call("prepare_upload", {"robot_id": "fixture", "path": "md:MAIN.LS",
                "source_file": "MAIN.LS", "expected_sha256": digest(ORIGINAL)}, "prepare-fixture"))
            # Same app front door, changed backend publication, retained work.
            (package / "framework_marker.txt").write_text("generation two")
            port = json.loads((state / "settings.json").read_bytes())["port"]
            await asyncio.to_thread(framework_host.publish, state, config, package=package, port=port)
            assert data(await foreign.invoke("refresh_services", {}))["backend_publications"][0]["status"] == "deferred"
            prompts = []
            async def confirm(message, schema, **kwargs):
                prompts.append(message)
                assert digest(PROPOSED) in message
                return ElicitResult(action="accept", content={"approve": True})
            session = SimpleNamespace(client_params=SimpleNamespace(capabilities=SimpleNamespace(
                elicitation=SimpleNamespace(form={}, url=None))), elicit_form=confirm)
            relay = ElicitationTarget(session, "fixture-client-request")
            committed = data(await call("commit_change", {"plan_id": plan["plan_id"],
                "approved_sha256": plan["proposed_sha256"]}, "commit-fixture", relay=relay))
            assert committed and len(prompts) == 1
            assert data(await call("read_file", {"robot_id": "fixture", "path": "md:MAIN.LS"}))["sha256"] == digest(PROPOSED)
            assert (await call("commit_change", {"plan_id": plan["plan_id"],
                "approved_sha256": plan["proposed_sha256"]}, "commit-fixture")).is_error
            data(await owner.invoke("release_service", {"service_id": "fanuc-mcp-server", "context_id": context}))
            assert data(await owner.invoke("refresh_services", {}))["backend_publications"][0]["status"] == "applied"
            assert [row.model_dump(by_alias=True, mode="json") for row in await host.create_server().list_tools()] == before
    asyncio.run(scenario())


def test_application_policy_gui_edits_nested_write_scope_and_revision(app_publication):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from fanuc_workbench.ui.safeguards_dialog import PolicyPane
    app = QApplication.instance() or QApplication([])
    _, _, publication, _ = app_publication
    original = json.loads(publication.read_bytes())
    pane = PolicyPane("application", str(publication))
    pane.widgets[("backends", 0, "service", "allow_writes")].setChecked(False)
    pane.review_changes()
    assert pane.save_button.isEnabled(), pane.status.text()
    pane.save_changes()
    after = json.loads(publication.read_bytes())
    assert after["backends"][0]["service"]["allow_writes"] is False
    assert after["backends"][0]["revision"] != original["backends"][0]["revision"]
    assert after["backends"][0]["artifact"] == original["backends"][0]["artifact"]
    pane.close()
