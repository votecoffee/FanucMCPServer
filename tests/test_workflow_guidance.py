"""Same maintained bytes through tool reads and Skills, with explicit live publication."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from mcp import Client
from mcp.types import PaginatedRequestParams, Request
from universal_tools.skill_catalog import SkillGet, SkillList, SkillParams
from universal_tools.skill_client import SkillClient

from fanuc_workbench.bridge import guidance
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge.service import RobotBridge


def data(result):
    assert not result.is_error, result
    return result.structured_content or json.loads(result.content[0].text)


def raw(result):
    assert len(result.contents) == 1
    content = result.contents[0]
    return content.text.encode() if hasattr(content, "text") else base64.b64decode(content.blob)


def empty_bridge(tmp_path):
    return RobotBridge(BridgeConfig(tmp_path / "state", tmp_path / "edits"),
                       lambda _: pytest.fail("Guidance must never contact a controller"))


def test_packaged_guide_and_supporting_files_match_native_skill_manifests(tmp_path):
    async def scenario():
        with empty_bridge(tmp_path) as bridge:
            server = create_server(bridge)
            async with Client(server, mode="2026-07-28", extensions=[SkillClient()], cache=None) as client:
                catalog = data(await client.call_tool("list_workflows", {}))
                listed = await client.session.send_request(Request(method="skills/list", params=PaginatedRequestParams()), SkillList)
                assert len(listed.skills) == len(catalog["workflows"]) == 1
                entry = listed.skills[0]
                fetched = await client.session.send_request(Request(method="skills/get", params=SkillParams(uri=entry["uri"])), SkillGet)
                assert fetched.skill == entry
                guide = data(await client.call_tool("read_workflow", {"workflow_id": "fanuc-robot", "revision": catalog["revision"]}))
                assert guide["app_id"] == "fanuc-mcp-server" and not guide["controller_contacted"]
                assert len(guide["resources"]) >= 3
                for reference in guide["resources"]:
                    content = raw(await client.read_resource(reference["uri"]))
                    assert hashlib.sha256(content).hexdigest() == reference["sha256"]
                    assert content == (guidance.ROOT / "fanuc-robot" / reference["path"]).read_bytes()
                    manifest = next(item for item in entry["resources"] if item["uri"] == reference["uri"])
                    assert manifest["digest"] == "sha256:" + reference["sha256"]
                    via_tool = data(await client.call_tool("read_workflow_reference", {"workflow_id": "fanuc-robot",
                        "revision": catalog["revision"], "path": reference["path"], "expected_sha256": reference["sha256"]}))
                    assert via_tool["text"].encode() == content
                assert len((await client.list_tools()).tools) == 55
    asyncio.run(scenario())


def test_explicit_publication_updates_already_connected_client_and_rejects_stale_pins(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(guidance.ROOT, source)
    async def scenario():
        with empty_bridge(tmp_path) as bridge:
            state = bridge.config.state_dir / "workflow-guidance"
            server = create_server(bridge)
            async with Client(server, mode="2026-07-28", extensions=[SkillClient()], cache=None) as client:
                before = data(await client.call_tool("list_workflows", {}))
                definitions = (await client.list_tools()).model_dump(by_alias=True)
                reference = before["workflows"][0]["resources"][1]
                target = source / "fanuc-robot" / reference["path"]
                target.write_text(target.read_text() + "\nExplicitly published synthetic revision.\n", encoding="utf-8")
                # Editing source alone does not change a running catalog.
                assert data(await client.call_tool("list_workflows", {})) == before
                published = guidance.publish(state, source, before["revision"])
                after = data(await client.call_tool("list_workflows", {}))
                assert after["revision"] == published["revision"] != before["revision"]
                assert (await client.call_tool("read_workflow", {"workflow_id": "fanuc-robot", "revision": before["revision"]})).is_error
                assert (await client.call_tool("read_workflow_reference", {"workflow_id": "fanuc-robot", "revision": after["revision"],
                    "path": reference["path"], "expected_sha256": reference["sha256"]})).is_error
                entry = (await client.session.send_request(Request(method="skills/list", params=PaginatedRequestParams()), SkillList)).skills[0]
                actual = next(item for item in entry["resources"] if item["uri"] == reference["uri"])
                assert actual["digest"] == "sha256:" + hashlib.sha256(raw(await client.read_resource(reference["uri"]))).hexdigest()
                assert (await client.list_tools()).model_dump(by_alias=True) == definitions
                # A new process reconstructs the same explicit publication.
                assert guidance.WorkflowGuidance(state).revision == after["revision"]
    asyncio.run(scenario())


def test_failed_or_conflicting_publication_preserves_prior_catalog(tmp_path):
    source, state = tmp_path / "source", tmp_path / "published"
    shutil.copytree(guidance.ROOT, source)
    initial = guidance.WorkflowGuidance()
    first = guidance.publish(state, source, initial.revision)
    before = (state / "active.json").read_bytes()
    (source / "fanuc-robot" / "execute.py").write_text("raise AssertionError('must never execute')")
    with pytest.raises(ValueError, match="Markdown"):
        guidance.publish(state, source, first["revision"])
    assert (state / "active.json").read_bytes() == before
    (source / "fanuc-robot" / "execute.py").unlink()
    with pytest.raises(ValueError, match="revision changed"):
        guidance.publish(state, source, "f" * 64)
    assert guidance.WorkflowGuidance(state).revision == first["revision"]


def test_local_embedding_publication_add_remove_and_failure_are_atomic(tmp_path):
    root = tmp_path / "extra"
    root.mkdir()
    (root / "SKILL.md").write_text('---\nname: fanuc-extra\ndescription: Synthetic extra guide\n---\nData only.\n')
    async def scenario():
        catalog = guidance.WorkflowGuidance()
        original = catalog.revision
        await catalog.publish([guidance.ROOT / "fanuc-robot", root], expected_revision=original)
        added = await catalog.catalog()
        assert len(added["workflows"]) == 2
        (root / "SKILL.md").write_text("invalid")
        with pytest.raises(ValueError):
            await catalog.publish([root], expected_revision=catalog.revision)
        assert await catalog.catalog() == added
        await catalog.publish([], expected_revision=catalog.revision)
        assert (await catalog.catalog())["workflows"] == []
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["../config.json", "/absolute.md", "references/../SKILL.md", "references/missing.md"])
def test_reference_reads_are_manifest_confined(tmp_path, path):
    async def scenario():
        with empty_bridge(tmp_path) as bridge:
            server = create_server(bridge)
            async with Client(server, mode="legacy", cache=None) as client:
                catalog = data(await client.call_tool("list_workflows", {}))
                assert (await client.call_tool("read_workflow_reference", {"workflow_id": "fanuc-robot",
                    "revision": catalog["revision"], "path": path, "expected_sha256": "0" * 64})).is_error
    asyncio.run(scenario())
