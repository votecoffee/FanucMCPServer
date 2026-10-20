# App-owned shared framework (experimental)

The shared app framework supplies a stable app-owned MCP host with replaceable
backends. FanucMCPServer ships its own standalone Codex plugin, desktop app and
direct 48-tool MCP server. Broker routing and registration are removed. The
experimental app-owned host uses an internal 49-tool backend, including its
discovery catalog; it does not expose a separate broker-adapter entrypoint.
Existing installations are updated only by running setup explicitly.

## Dependency and build

The tested Python dependency is `universal-codex-tools==0.17.0`, imported through
`universal_tools.framework`. Its MCP dependency is exactly 2.3.0. The distribution
has not been assumed available on a public package feed. Obtain the reviewed
wheel from the framework build, verify it against `packaging/framework.lock.json`,
and install it in the app's isolated environment before installing the MCP extra:

```powershell
python -m pip install C:/Packages/universal_codex_tools-0.17.0-py3-none-any.whl
python -m pip install -c packaging/framework-requirements.txt -e ".[dev,mcp]"
python -B -m pytest -q
```

The offline bundle builder requires `--framework-wheel`; it verifies the exact
wheel hash, package metadata, dependency pins and every RECORD entry, then bundles
the distribution and resolved dependency wheels. Runtime installation remains
offline and hash checked. No framework source is copied into this repository and
no Git submodule is required. Use a separate host environment if another app has
incompatible SDK/native dependencies; do not downgrade that app's environment.

## Explicit candidate workflow

Use a **new, private state directory**, separate from any previous service registry:

```powershell
python -B -m fanuc_workbench.bridge.framework_host --state C:/RobotTools/FanucAppState publish --config C:/RobotTools/robot-policy.json
python -B -m fanuc_workbench.bridge.framework_host --state C:/RobotTools/FanucAppState serve
```

Publishing snapshots this app's package into a content-addressed deployment,
pins its complete app-file inventory and exact robot-policy bytes, and validates
the application definition. It creates private authenticated loopback settings
on port 8891 by default and a `runtime.json` pointer. It never starts a controller,
installs a plugin, changes a registration or activates a running host. Third-party
and vendor/native dependencies remain the responsibility of the separately
verified runtime; the app inventory does not authenticate their dependency closure.

In an **isolated acceptance profile**, configure this app's stdio connection to
use the same environment's absolute Python and these arguments:

```text
-B -m fanuc_workbench.bridge.framework_host --state C:/RobotTools/FanucAppState proxy
```

Start the app host explicitly before connecting. The proxy never starts the
unrelated daemon or a missing app host. Each proxy receives independent affinity;
validate actual chat/frontend lifetimes before adoption. The packaged standalone
plugin continues to use the existing direct entrypoint until separate host/profile
acceptance. There is no automatic switch or second-plugin requirement.

## Permissions, discovery and reload

The fixed front door has fourteen discovery/invocation tools. Acquire the
exclusive `fanuc-mcp-server` context; describe current backend schemas/revisions
before calling through the read or write channel. Discovery metadata never grants
authority to write or contact a controller. The backend retains the same shared
services, proposal approval, expiry, preflight, readback and single-use plans.
The FANUC backend requires the legacy `2025-11-25` wire profile. Its current
handlers request imperative approval forms through that retained back-channel.
Framework 0.15 can serve modern frontends and retain the original operation across
owner/argument-bound form continuations while this backend stays legacy. Neither
continuations nor hashes establish human approval; app review, target checks,
single-use plans and readback still apply. Caller `clientInfo` and model arguments
never authenticate an owner.

Downstream `protocol_mode: auto` / `2026-07-28` and `watch_catalog: true` are
rejected before publication, GUI review or host startup. They require a separate
resolver-based FANUC approval integration. Existing publications remain unchanged
when rejected. Discovery caching has no effect without backend TTL hints; actual
invocation always rechecks schema/effect, authorization and revisions.

The framework's HTTP SSE limits apply to HTTP backends; this app publishes stdio
workers. Framework trace spans contain fixed labels without app payloads and do
not enable an exporter. Optional skill-resource snapshots are not enabled by this
app's CLI or plugin and do not install or activate native Codex skills.

Framework 0.17 supplies the following optional profiles. They are available in the
dependency, but this app does not register or activate them during installation:

| Feature | Assessment for FanucMCPServer |
| --- | --- |
| Tasks and authorized notifications | Backup/recording jobs retain their app-owned handles, cancellation and review/recovery contracts. A future Task adapter must authorize every operation, reconcile ambiguous starts and verify actual job survival. URL acceptance is navigation, not approval or credentials. |
| Verified Skills and live publication | The app uses its packaged standalone skill. Downstream discovery/publication would need explicit origin and content approval; native skill loading still belongs to the host. |
| Resources, prompts and completions relay | No sources are configured. Any future source needs app-selected credentials, principal authorization, revision checks and explicit engineering context. The relay does not forward generic tools or Tasks. |
| MCP Apps interfaces | No UI extension is composed. Existing tool results provide text; interactive resources and actual host rendering need separate acceptance. |
| Remote OAuth and DPoP | The candidate host remains authenticated loopback. Remote endpoints, identity verification, credentials and TLS require an explicit deployment; protected MCP requests must not be retried automatically. |
| Redis Tasks and native frontend replicas | The Redis extra is not installed. This retained-worker app host requires one owning process; jobs, journals, reservations and live controller sessions are not portable between replicas. |

See the framework's [extension contract](https://github.com/votecoffee/CodexMCPBroker/blob/87157f88de4a5320d2712224216b1e6f18a0ec8d/docs/extension-development.md)
and [deployment profiles](https://github.com/votecoffee/CodexMCPBroker/blob/87157f88de4a5320d2712224216b1e6f18a0ec8d/docs/deployment.md)
for the assessed baseline. The fourteen-tool front door does not relay downstream
extensions automatically. SDK 2.2 interoperability checks in the framework do not
change this app's SDK 2.3.0 pin or establish controller acceptance.

The **App framework** GUI exposes structural argument budgets in addition to byte
limits: `max_argument_depth` (default 64, maximum 128) and `max_argument_nodes`
(default 100,000, maximum 1,000,000, including object keys). They are checked before
schema validation, write claims and dispatch. Republishing preserves reviewed
limits. These budgets do not narrow supported integer values or grant write access.

New publications default to a disabled write channel and an empty exact write
allowlist. Local staging, preparation, jobs and reloads also need that channel.
For a new explicitly authorized candidate, `publish --allow-write-channel` selects
the current exact set of non-read tools; `--deny-write-channel` disables it. Normal
republishing preserves existing operator policy and does not expand tool scopes.
Robot `allow_writes` and the operator-selected `write_scope` independently govern online writes.
No adapter for Codex's own permission modes is assumed; model-supplied hashes or
approval arguments cannot authenticate a human.

**Settings > Safeguards and policies > App framework** edits all host access policy
fields in `fanuc-application.json`, validates and reviews a new revision, backs up
the original and stages it without activation. **Host settings** accepts
the app's `runtime.json` for global port/token/request-budget edits. The same
stopped-host and retained-work restrictions apply to those global settings.

Publish new app code, or republish after changing robot policy, then finish and
release retained work and call `refresh_services`. Owned contexts defer replacement.
Uncertain, interrupted and app-error writes require explicit app-owned
reconciliation; the updater never acknowledges outcomes or clears barriers.
Changed deployment files or policy bytes fail closed rather than silently loading
new permissions. An endpoint-only reload through the older direct route does not
override the framework's pinned policy. Changes to framework/runtime or native
plugin/skill definitions still require their normal restart/update lifecycle.

The framework serializes calls to this backend. Long work uses app-owned jobs;
the direct server's bounded concurrent dispatcher remains available independently.

## Acceptance boundary

Offline fixtures exercise the actual FANUC backend through the pinned framework,
using synthetic controller transports: complete tool definitions/results, retained
plans, confirmation, readback, competing clients, publication deferral/replacement,
stable front-door schemas, stale/hash refusal and GUI policy changes. They do not
establish physical-controller, native deployment or real Codex multi-chat acceptance.
Latency measurements and complete native/media/workflow parity must be assessed
before adopting this candidate. Source tests do not change a live plugin,
registration or runtime pointer.
