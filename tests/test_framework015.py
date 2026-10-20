"""Framework opt-ins use only disposable app state and synthetic controller data."""
import asyncio
import json

import pytest

from fanuc_workbench.bridge.framework_host import publish
from fanuc_workbench.bridge.policy_editor import load_policy, review_policy, save_policy
from test_framework_host import app_publication, data
from test_robot_bridge import ORIGINAL, PROPOSED, digest


def test_framework_defaults_and_reviewed_legacy_settings_survive_publication(app_publication):
    state, config, publication, package = app_publication
    snapshot = load_policy(publication, "application")
    document = snapshot.document
    service = document["backends"][0]["service"]
    assert service["protocol_mode"] == "legacy" and not service["watch_catalog"]
    assert service["discovery_cache_ttl_seconds"] == 0
    service.update(timeout_seconds=75, discovery_cache_ttl_seconds=5)
    saved = save_policy(review_policy(snapshot, document))[0].document
    publish(state, config, package=package)
    assert json.loads(publication.read_bytes()) == saved


@pytest.mark.parametrize("changes", [{"protocol_mode": "auto"}, {"protocol_mode": "2026-07-28"},
                                    {"protocol_mode": "auto", "watch_catalog": True}])
def test_unsupported_backend_modes_are_refused_without_mutating_publication(app_publication, changes):
    from fanuc_workbench.bridge.framework_host import main
    state, config, publication, package = app_publication
    snapshot = load_policy(publication, "application")
    document = snapshot.document
    document["backends"][0]["service"].update(changes)
    with pytest.raises(ValueError, match="requires the legacy"):
        review_policy(snapshot, document)
    assert publication.read_bytes() == snapshot.raw
    # A manually supplied incompatible publication is preserved for local repair,
    # never activated or silently rewritten to a different profile.
    publication.write_text(json.dumps(document))
    raw = publication.read_bytes()
    with pytest.raises(ValueError, match="requires the legacy"):
        publish(state, config, package=package)
    with pytest.raises(ValueError, match="requires the legacy"):
        main(["--state", str(state), "serve"])
    assert publication.read_bytes() == raw


@pytest.mark.parametrize("approved", [True, False])
def test_modern_frontends_keep_original_fanuc_plan_owner_and_confirmation(app_publication, approved):
    from mcp import Client
    from mcp.types import ElicitResult
    from universal_tools.framework import ApplicationHost
    state, _, publication, _ = app_publication

    async def scenario():
        prompts, foreign_prompts = [], []
        async def answer(_, params):
            prompts.append(params.message)
            assert digest(PROPOSED) in params.message and "fixture" in params.message
            return ElicitResult(action="accept", content={"approve": approved})
        async def foreign_answer(_, params):
            foreign_prompts.append(params.message)
            return ElicitResult(action="accept", content={"approve": True})
        async with ApplicationHost(state, publication) as host:
            # Constructor identities belong to this trusted fixture adapter;
            # neither tools nor caller-supplied clientInfo may select them.
            async with Client(host.create_server(frontend_identity="a" * 32), mode="2026-07-28",
                              elicitation_callback=answer, cache=None) as owner, Client(
                    host.create_server(frontend_identity="b" * 32), mode="2026-07-28",
                    elicitation_callback=foreign_answer, cache=None) as other:
                definitions = (await owner.list_tools()).model_dump(by_alias=True)
                assert len(definitions["tools"]) == 14
                context = data(await owner.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"}))["context_id"]
                assert (await other.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"})).is_error
                async def call(name, arguments, request=None, client=owner):
                    tool = "fanuc-mcp-server/" + name
                    described = data(await client.call_tool("describe_tool", {"tool_id": tool}))
                    payload = {"tool_id": tool, "catalog_revision": described["catalog_revision"],
                               "context_id": context, "arguments": arguments}
                    if request: payload["request_id"] = request
                    return await client.call_tool("call_write_tool" if request else "call_read_tool", payload)
                target = {"robot_id": "fixture", "path": "md:MAIN.LS"}
                assert data(await call("read_file", target))["sha256"] == digest(ORIGINAL)
                plan = data(await call("prepare_upload", {**target, "source_file": "MAIN.LS",
                    "expected_sha256": digest(ORIGINAL)}, "prepare-modern"))
                commit = {"plan_id": plan["plan_id"], "approved_sha256": plan["proposed_sha256"]}
                assert (await call("commit_change", commit, "foreign-commit", other)).is_error
                assert prompts == foreign_prompts == []
                result = await call("commit_change", commit, "commit-modern")
                assert result.is_error is (not approved)
                assert len(prompts) == 1 and foreign_prompts == []
                expected = PROPOSED if approved else ORIGINAL
                assert data(await call("read_file", target))["sha256"] == digest(expected)
                assert (await call("commit_change", commit, "commit-modern")).is_error
                receipt = data(await owner.call_tool("get_request_status", {"request_id": "commit-modern"}))
                assert receipt["status"] == ("completed" if approved else "app_error")
                assert (await owner.list_tools()).model_dump(by_alias=True) == definitions
                data(await owner.call_tool("release_service", {"service_id": "fanuc-mcp-server", "context_id": context}))
    asyncio.run(scenario())


def test_ownerless_modern_frontend_cannot_acquire_fanuc_session(app_publication):
    from mcp import Client
    from universal_tools.framework import ApplicationHost
    state, _, publication, _ = app_publication
    async def scenario():
        async with ApplicationHost(state, publication) as host:
            async with Client(host.create_server(), mode="2026-07-28", cache=None) as client:
                assert (await client.call_tool("acquire_service", {"service_id": "fanuc-mcp-server"})).is_error
    asyncio.run(scenario())
