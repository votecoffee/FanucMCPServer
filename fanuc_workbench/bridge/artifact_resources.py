"""Bounded views of the existing app-owned artifact store, never native acquisition."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from itertools import islice
from urllib.parse import parse_qs, quote, unquote, urlsplit

from mcp.server.mcpserver import Context


CATALOG = "fanuc-artifact://catalog"


class ArtifactResources:
    identifier = "fanuc-artifact-views"

    def __init__(self, bridge):
        self.bridge = bridge
        self.listeners = set()
        self.generation = 0
        self.revision = hashlib.sha256(b"[]").hexdigest()

    def claims_uri(self, uri):
        return uri.startswith("fanuc-artifact://")

    def resource_metadata(self):
        return [{"uri": CATALOG, "name": "Saved FANUC artifact views", "mimeType": "application/json"}]

    def manifest(self, identifier):
        item = self.bridge.operations.store.get(identifier)
        # Same app-local state scope as the ordinary artifact readers. Absolute
        # storage paths never become resource identifiers or version material.
        public = {key: value for key, value in item.items() if key != "root"}
        revision = hashlib.sha256(json.dumps(public, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return item, revision

    def describe(self, identifier, record, *, revision=None):
        if revision is None:
            _, revision = self.manifest(identifier)
        return (f"fanuc-artifact://{identifier}/file/{quote(record['path'], safe='/')}"
                f"?revision={revision}&sha256={record['sha256']}&offset=0&length=32768")

    def catalog(self):
        listing = self.bridge.operations.store.list(limit=100)
        artifacts = []
        for row in listing["items"]:
            item, revision = self.manifest(row["artifact_id"])
            artifacts.append({"artifact_id": item["artifact_id"], "kind": item["kind"], "revision": revision,
                "file_count": len(item["files"]), "files": [
                    {**record, "resource_uri": self.describe(item["artifact_id"], record, revision=revision)}
                    for record in item["files"][:100]]})
        return {"app_id": "fanuc-mcp-server", "artifacts": artifacts, "total": listing["total"],
                "limit": 100, "freshness": "preserved immutable capture; not live controller state",
                "controller_contacted": False}

    async def read_bytes(self, uri):
        if len(uri) > 4096:
            raise ValueError("Artifact URI exceeds its limit")
        if uri == CATALOG:
            result = await self.bridge.requests.run(self.catalog, control=True)
        else:
            parsed = urlsplit(uri)
            if parsed.scheme != "fanuc-artifact" or parsed.fragment or not parsed.path.startswith("/file/"):
                raise ValueError("Unknown artifact view")
            fields = parse_qs(parsed.query, strict_parsing=True, max_num_fields=4)
            if set(fields) != {"revision", "sha256", "offset", "length"} or any(len(values) != 1 for values in fields.values()):
                raise ValueError("Artifact view needs exact version/hash/range identity")
            identifier, path = parsed.netloc, unquote(parsed.path.removeprefix("/file/"))
            item, revision = await self.bridge.requests.run(self.manifest, identifier, control=True)
            record = next((row for row in item["files"] if row["path"] == path), None)
            if revision != fields["revision"][0] or record is None or record["sha256"] != fields["sha256"][0]:
                raise ValueError("Stale or unlisted artifact view; refetch its manifest")
            result = await self.bridge.requests.run(self.bridge.operations.store.read, identifier, path,
                int(fields["offset"][0]), int(fields["length"][0]), control=True)
            result.update(app_id="fanuc-mcp-server", revision=revision, sha256=record["sha256"],
                          freshness="preserved capture", controller_contacted=False)
        raw = json.dumps(result, ensure_ascii=False).encode()
        if len(raw) > 1024 * 1024:
            raise ValueError("Artifact catalog view exceeds its budget; use paginated ordinary artifact readers")
        return raw

    @asynccontextmanager
    async def lifespan(self, server):
        context = Context(mcp_server=server, subscriptions=server._subscriptions)
        async def watch():
            previous = None
            while True:
                try:
                    # Bounded, local catalog invalidation hints. Never read or
                    # publish report contents merely to detect a new capture.
                    catalog = self.bridge.operations.store.catalog
                    paths = sorted(islice(catalog.glob("*.json"), 500))
                    signature = [catalog.stat().st_mtime_ns,
                                 [(path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in paths]]
                    stamp = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
                    if previous is not None and stamp != previous:
                        self.generation += 1
                        self.revision = stamp
                        await context.notify_resource_updated(CATALOG)
                    previous = stamp
                except (OSError, ValueError):
                    pass  # Hints have no replay; readers always verify current store bytes.
                await asyncio.sleep(.25)
        task = asyncio.create_task(watch())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
