# Current workflow guidance and Skills-over-MCP

The standalone plugin retains the focused `fanuc-robot` bootstrap. Its name,
description and triggers remain stable. The bootstrap fetches current guidance
before discovering exact live tool schemas/revisions. Guide content never grants
write authority or changes the selected connection, approval mode or target policy.

## One maintained source

`fanuc_workbench/guidance/` contains the canonical detailed guide and Markdown
references. These files are package data in desktop and headless builds.
The protocol manifests, resource views and ordinary reader responses are generated
from the same verified snapshots; the bootstrap does not carry a second procedure.

- `list_workflows` returns app/workflow identity, the catalog revision and file
  manifests with exact byte counts and SHA-256 values.
- `read_workflow` requires that revision and returns the detailed instructions.
- `read_workflow_reference` requires a listed path, revision and exact file hash.
  Removed, stale and unlisted references fail closed. No arbitrary file path is accepted.

These tools make guidance available to clients without native skill loading. On
the optional app-owned host, discover/describe the backend readers and use the
existing context/revision contract. Guidance discovery and resource reads never
probe a robot or acquire an engineering context implicitly.

## Explicit publication, no Codex reload for guide data

Local administrators can publish a complete replacement from one canonical tree:

```powershell
python -B -m fanuc_workbench.bridge.guidance --config C:/RobotTools/robot-policy.json --source C:/RobotTools/guide-source --expected-revision CATALOG_SHA256
```

The source root contains named skill folders, each with `SKILL.md` and optional
Markdown references. Only Markdown data is accepted; links, hidden/cache resources
and contributed code are rejected. Publication verifies a frozen candidate, stores
an immutable content-addressed snapshot under the policy's private state directory
and atomically replaces `workflow-guidance/active.json`. Conflicting revisions,
invalid content and changed source bytes preserve the prior active publication.
The complete source set determines additions and removals. No controller object,
job replay, approval change or native operation occurs.

Connected clients refresh that explicit publication on guide/skill/resource reads;
the Skills provider also watches for publications and sends resource-change hints.
Editing source files alone leaves existing snapshots unchanged. Reconnecting
clients refetch current manifests. The trusted local embedding API additionally
supports `WorkflowGuidance.publish(..., expected_revision=...)` for an in-process
catalog. No model-facing tool accepts publication paths or activates code.

## Profiles and lifecycle

The standalone SDK endpoint composes framework `ExtensionServer` with a real
`WorkflowGuidance` catalog. It implements `skills/list`, `skills/get` and
`resources/read`; optional directory reads are not advertised. The app-owned
host and its stdio proxy use the same provider and shared guidance-state path.
Its retained engineering backend stays on the reviewed legacy approval profile
and does not advertise a downstream Skills relay. This is app-owned guidance,
not automatic federation of arbitrary downstream instructions.

Republish older app-host definitions once with the new app code to record that
shared guidance path. Code/new-tool changes still need the normal standalone
installation/reconnect or immutable backend publication, quiescence and host
refresh. Guidance-only publications change no worker code and need no worker
replacement. Pending plans, jobs, ownership and uncertain-effect barriers remain.

OpenAI currently imports MCP skills as plugin-submission snapshots; native
ChatGPT/Codex loaders do not fetch these instructions at runtime. Thus packaged
bootstrap/metadata changes still need normal plugin installation and host reload.
Live protocol publication is not native skill activation. See the
[official skill import guidance](https://developers.openai.com/plugins/build/skills#import-a-skill-from-mcp).

## Evidence boundaries

Offline tests compare ordinary-reader text with native manifests/resource hashes,
publish changes through an already connected client, preserve tool schemas, reject
stale references and conflicting revisions, test add/remove and retain the prior
catalog after invalid publication. They do not establish native host skill
selection, user-facing approval behavior, installed robot options or controller
acceptance. Keep installed/runtime evidence under ignored local directories.
