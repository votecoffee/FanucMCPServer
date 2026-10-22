"""Hash-bound local resource readers, without creating an MCP server or worker."""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from fanuc_workbench.bridge.artifact_resources import ArtifactResources, CATALOG
from fanuc_workbench.bridge.storage import ArtifactStore


class InlineRequests:
    async def run(self, function, *args, control=False, **kwargs):
        return function(*args, **kwargs)


def view(tmp_path):
    store = ArtifactStore(tmp_path)
    folder = tmp_path / "capture"
    folder.mkdir()
    (folder / "log.txt").write_bytes(b"synthetic saved bytes")
    item = store.register(folder, "sample")
    bridge = SimpleNamespace(operations=SimpleNamespace(store=store), requests=InlineRequests())
    return ArtifactResources(bridge), item, folder


def test_bound_range_preserves_identity_and_has_no_native_acquisition(tmp_path):
    resources, item, _ = view(tmp_path)
    async def check():
        catalog = json.loads(await resources.read_bytes(CATALOG))
        record = catalog["artifacts"][0]["files"][0]
        assert str(tmp_path) not in json.dumps(catalog)
        uri = record["resource_uri"].replace("offset=0&length=32768", "offset=3&length=5")
        result = json.loads(await resources.read_bytes(uri))
        assert base64.b64decode(result["base64"]) == b"theti"
        assert result["sha256"] == record["sha256"]
        assert result["revision"] == catalog["artifacts"][0]["revision"]
        assert result["controller_contacted"] is False and result["artifact_id"] == item["artifact_id"]
    asyncio.run(check())


@pytest.mark.parametrize("change", [
    lambda uri: uri.replace("length=32768", "length=65537"),
    lambda uri: uri.replace("offset=0", "offset=-1"),
    lambda uri: uri + "&length=1",
    lambda uri: uri.replace("/file/log.txt", "/file/../log.txt"),
    lambda uri: uri.replace("revision=", "revision=stale"),
    lambda uri: uri.replace("sha256=", "sha256=stale"),
])
def test_resource_refuses_unbounded_stale_duplicate_or_traversing_identity(tmp_path, change):
    resources, _, _ = view(tmp_path)
    uri = resources.catalog()["artifacts"][0]["files"][0]["resource_uri"]
    with pytest.raises(ValueError):
        asyncio.run(resources.read_bytes(change(uri)))


def test_resource_rejects_changed_bytes_and_a_different_state_store(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    resources, _, folder = view(first)
    uri = resources.catalog()["artifacts"][0]["files"][0]["resource_uri"]
    second = tmp_path / "second"
    second.mkdir()
    foreign, _, _ = view(second)
    with pytest.raises(ValueError, match="Unknown artifact"):
        asyncio.run(foreign.read_bytes(uri))
    (folder / "log.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(resources.read_bytes(uri))
