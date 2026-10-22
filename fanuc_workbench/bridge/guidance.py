"""One immutable guidance view for ordinary reads and Skills-over-MCP.

Only explicit local administration publishes Markdown snapshots. Tool reads never
select filesystem roots, execute contributed code or change controller policy.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
from urllib.parse import unquote

from mcp.types import PaginatedRequestParams
from universal_tools.skill_catalog import SkillCatalog, SkillParams

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.storage import RobotLocks, atomic_json


APP_ID = "fanuc-mcp-server"
ROOT = Path(__file__).resolve().parents[1] / "guidance"
CONTEXT = SimpleNamespace(protocol_version="2026-07-28")
MAX_POINTER = 1024 * 1024


def validate_roots(roots):
    for package in roots:
        if not package.is_dir() or package.is_symlink() or getattr(package, "is_junction", lambda: False)():
            raise ValueError("Guidance package must be an ordinary directory")
        for path in package.rglob("*"):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("Guidance cannot contain links")
            if path.is_file() and (path.suffix != ".md" or not path.resolve().is_relative_to(package.resolve())):
                raise ValueError("Guidance accepts only Markdown data, never contributed code")


def packages(root):
    root = Path(root)
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)() or not root.is_dir():
        raise ValueError("Guidance source must be an ordinary directory")
    result = sorted(root.iterdir())
    validate_roots(result)
    return result


def inventory(root):
    result = {}
    for package in packages(root):
        for path in sorted(package.rglob("*.md")):
            raw = path.read_bytes()
            result[path.relative_to(root).as_posix()] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return result


def pointer(state):
    path = state / "active.json"
    if not path.exists():
        return None, None
    with path.open("rb") as source:
        raw = source.read(MAX_POINTER + 1)
    if len(raw) > MAX_POINTER or path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("Invalid guidance publication pointer")
    document = json.loads(raw)
    revision = document.get("revision", "")
    if (document.get("schema_version") != 1 or document.get("app_id") != APP_ID
            or len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision)):
        raise ValueError("Invalid guidance publication identity")
    return document, hashlib.sha256(raw).hexdigest()


def publication_root(state, document):
    target = state / "releases" / document["revision"]
    if inventory(target) != document["files"]:
        raise ValueError("Guidance publication bytes changed; preserve it for inspection")
    return target


class WorkflowGuidance(SkillCatalog):
    """Fixed extension identity, live content from an explicitly published snapshot."""

    def __init__(self, state=None, *, source=ROOT):
        self.source = Path(source)
        self.state = Path(state) if state is not None else None
        self._pointer_digest = None
        self._view_lock = asyncio.Lock()
        selected = self.source
        document = None
        if self.state is not None:
            document, self._pointer_digest = pointer(self.state)
            if document:
                selected = publication_root(self.state, document)
        super().__init__(packages(selected), directory_read=False)
        if document and document["revision"] != self.revision:
            raise ValueError("Guidance manifest does not match the published revision")

    async def refresh(self):
        if self.state is None:
            return
        async with self._view_lock:
            document, token = pointer(self.state)
            if token == self._pointer_digest:
                return
            if document is None:
                raise ValueError("Published guidance pointer was removed; explicit publication is required")
            selected = publication_root(self.state, document)
            candidate = SkillCatalog(packages(selected))
            if candidate.revision != document["revision"]:
                raise ValueError("Guidance manifest revision mismatch")
            await super().publish(packages(selected), expected_revision=self.revision)
            self._pointer_digest = token

    @asynccontextmanager
    async def lifespan(self, _server):
        async def watch():
            while True:
                try:
                    await self.refresh()
                except (OSError, ValueError):
                    pass  # Reads fail closed; never replace a valid view with an invalid candidate.
                await asyncio.sleep(1)
        task = asyncio.create_task(watch())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def publish(self, roots=(), *, expected_revision):
        # Local embedding API only. Ordinary MCP tools expose no publication path.
        roots = [Path(root) for root in roots]
        validate_roots(roots)
        async with self._view_lock:
            return await super().publish(roots, expected_revision=expected_revision)

    async def list_skills(self, ctx, params):
        await self.refresh()
        return await super().list_skills(ctx, params)

    async def get_skill(self, ctx, params):
        await self.refresh()
        return await super().get_skill(ctx, params)

    async def read_bytes(self, uri):
        await self.refresh()
        return await super().read_bytes(uri)

    async def catalog(self):
        await self.refresh()
        async with self._view_lock:
            rows, cursor = [], None
            while True:
                page = await super().list_skills(CONTEXT, PaginatedRequestParams(cursor=cursor))
                for entry in page.skills:
                    name = entry["frontmatter"]["name"]
                    rows.append({"workflow_id": name, "description": entry["frontmatter"]["description"],
                                 "uri": entry["uri"], "resources": self.references(entry)})
                cursor = page.next_cursor
                if cursor is None:
                    break
            return {"app_id": APP_ID, "revision": self.revision, "workflows": rows,
                    "controller_contacted": False}

    @staticmethod
    def references(entry):
        prefix = entry["uri"].removesuffix("SKILL.md")
        return [{"path": unquote(item["uri"][len(prefix):]), "uri": item["uri"],
                 "sha256": item["digest"].removeprefix("sha256:"), "bytes": item["size"]}
                for item in entry["resources"]]

    async def read(self, workflow_id, revision, path="SKILL.md", expected_sha256=None):
        await self.refresh()
        async with self._view_lock:
            if revision != self.revision:
                raise ValueError("Stale workflow revision; rediscover the current guide")
            if not isinstance(workflow_id, str) or not workflow_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in workflow_id):
                raise ValueError("Unknown workflow identity")
            entry = (await super().get_skill(CONTEXT, SkillParams(uri=f"skill://{workflow_id}/SKILL.md"))).skill
            refs = self.references(entry)
            found = next((item for item in refs if item["path"] == path), None)
            if found is None:
                raise ValueError("Reference is not in the current workflow manifest")
            if expected_sha256 is not None and expected_sha256 != found["sha256"]:
                raise ValueError("Stale workflow reference hash; read the current manifest")
            raw = await super().read_bytes(found["uri"])
            if hashlib.sha256(raw).hexdigest() != found["sha256"]:
                raise ValueError("Workflow reference integrity check failed")
            return {"app_id": APP_ID, "workflow_id": workflow_id, "revision": revision,
                    **found, "text": raw.decode("utf-8"), "resources": refs, "controller_contacted": False}


def publish(state, source, expected_revision):
    """Local administrator CAS publication; no robot object or native worker exists."""
    state, source = Path(state).resolve(), Path(source).resolve()
    if state == source or state.is_relative_to(source):
        raise ValueError("Keep publication state outside guidance source")
    candidate = SkillCatalog(packages(source))
    files = inventory(source)
    state.mkdir(parents=True, exist_ok=True)
    with RobotLocks(state / "locks").acquire("publication"):
        previous, _ = pointer(state)
        current = previous["revision"] if previous else SkillCatalog(packages(ROOT)).revision
        if current != expected_revision:
            raise ValueError("Guidance publication revision changed")
        releases = state / "releases"
        releases.mkdir(exist_ok=True)
        target = releases / candidate.revision
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix=".stage-", dir=releases) as temporary:
                stage = Path(temporary)
                assert stage.is_relative_to(releases)
                for package in packages(source):
                    shutil.copytree(package, stage / package.name)
                frozen = SkillCatalog(packages(stage))
                if frozen.revision != candidate.revision or inventory(stage) != files:
                    raise ValueError("Guidance source changed while staging")
                os.rename(stage, target)
        if inventory(target) != files:
            raise ValueError("Existing guidance snapshot changed")
        document = {"schema_version": 1, "app_id": APP_ID, "revision": candidate.revision, "files": files}
        atomic_json(state / "active.json", document)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish data-only FANUC workflow guidance; no controller contact")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args(argv)
    config = BridgeConfig.load(args.config)
    print(json.dumps(publish(config.state_dir / "workflow-guidance", args.source, args.expected_revision), indent=2))


if __name__ == "__main__":
    main()
