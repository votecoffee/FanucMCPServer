"""Structural host budgets preserve app policy and reject before write claims."""
import asyncio
import json
import os

import pytest

from fanuc_workbench.bridge.framework_host import publish
from fanuc_workbench.bridge.policy_editor import load_policy, review_policy, save_policy
from test_framework_host import app_publication, data


def test_structural_budget_gui_review_and_republication_preserve_choices(app_publication):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from fanuc_workbench.ui.safeguards_dialog import PolicyPane
    state, config, publication, package = app_publication
    original = publication.read_bytes()
    service = json.loads(original)["backends"][0]["service"]
    assert service["max_argument_depth"] == 64 and service["max_argument_nodes"] == 100000
    app = QApplication.instance() or QApplication([])
    pane = PolicyPane("application", str(publication))
    prefix = ("backends", 0, "service")
    pane.widgets[prefix + ("max_argument_depth",)].setValue(8)
    pane.widgets[prefix + ("max_argument_nodes",)].setValue(321)
    pane.review_changes(); assert pane.save_button.isEnabled(), pane.status.text()
    pane.save_changes(); pane.close()
    saved = json.loads(publication.read_bytes())
    assert saved["backends"][0]["service"]["max_argument_depth"] == 8
    assert saved["backends"][0]["service"]["max_argument_nodes"] == 321
    backups = list((publication.parent / ".fanuc-policy-history").glob("*.bak"))
    assert any(path.read_bytes() == original for path in backups)
    publish(state, config, package=package)
    assert json.loads(publication.read_bytes()) == saved


@pytest.mark.parametrize("limit", [{"max_argument_depth": 1}, {"max_argument_nodes": 3}])
def test_actual_fanuc_host_refuses_oversized_shape_before_write_claim(app_publication, limit):
    from universal_tools.framework import ApplicationHost
    state, _, publication, _ = app_publication
    snapshot = load_policy(publication, "application")
    document = snapshot.document
    document["backends"][0]["service"].update(limit)
    save_policy(review_policy(snapshot, document))
    async def scenario():
        async with ApplicationHost(state, publication) as host:
            client = host.client()
            context = data(await client.invoke("acquire_service", {"service_id": "fanuc-mcp-server"}))["context_id"]
            definition = data(await client.invoke("describe_tool", {"tool_id": "fanuc-mcp-server/prepare_change"}))
            arguments = {"operation_id": "fixture.unsupported", "revision": "fixture-revision",
                         "arguments": {"nested": [[1, 2, 3]]}}
            rejected = await client.invoke("call_write_tool", {"tool_id": "fanuc-mcp-server/prepare_change",
                "catalog_revision": definition["catalog_revision"], "context_id": context,
                "request_id": "shape-budget-001", "arguments": arguments})
            assert rejected.is_error and "budget" in rejected.content[0].text
            receipt = await client.invoke("get_request_status", {"request_id": "shape-budget-001"})
            assert receipt.is_error and "unknown request_id" in receipt.content[0].text
            status = data(await client.invoke("service_session_status", {"service_id": "fanuc-mcp-server"}))
            assert not status["recovery_required"]
            data(await client.invoke("release_service", {"service_id": "fanuc-mcp-server", "context_id": context}))
    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [{"max_argument_depth": 0}, {"max_argument_nodes": 1000001}])
def test_invalid_structural_limits_do_not_change_saved_policy(app_publication, invalid):
    _, _, publication, _ = app_publication
    snapshot = load_policy(publication, "application")
    document = snapshot.document
    document["backends"][0]["service"].update(invalid)
    with pytest.raises(ValueError): review_policy(snapshot, document)
    assert publication.read_bytes() == snapshot.raw
