# Universal Tools integration

FanucMCPServer continues to ship its own Codex plugin and standalone MCP server.
The desktop and Python API also run independently. Universal Tools from
[CodexMCPBroker](https://github.com/votecoffee/CodexMCPBroker) is an optional
discovery and routing layer over the same FANUC services.

## Installation routes

Build the normal [offline plugin bundle](ROBOT_PLUGIN_SETUP.md), then choose:

```powershell
py -3.14 -B C:/RobotTools/FanucPluginBundle/setup.py --runtime-dir C:/RobotTools/FanucRuntime --register auto
```

| `--register` | Behavior |
| --- | --- |
| `auto` | Fresh installation: prefer an enabled, initialized, compatible Universal Tools plugin; otherwise install the standalone FANUC plugin. |
| `broker` | Require Universal Tools and register the FANUC service; fail without switching routes if prerequisites are missing. |
| `plugin` | Explicitly install the standalone FANUC plugin, including when Universal Tools is present. |
| `mcp` | Register the standalone MCP server directly with Codex. |
| `none` (default) | Install/update the runtime only. |

`--codex-home` selects an isolated profile. Detection uses its actual installed
and enabled plugin inventory and local server policy, then validates the broker
runtime pointer and version. A folder name alone is insufficient. Setup validates
broker settings through the broker's own parser without starting its daemon or
exposing its token. For a distribution without a local source pointer, supply
`--broker-runtime C:/RobotTools/UniversalTools/runtime.json`. Supported broker
versions are `>=0.8.0,<1.0.0`, using registration/catalog schema 1. Project or
managed policy can impose additional restrictions; verify the intended host.

Automatic upgrades retain the recorded route in local `installation.json`.
Older, already installed FANUC plugins are retained without adding another
identity or switching them to the broker. Existing broker registrations, edited
skills and active/recovery contexts are not overwritten. Missing prerequisites
on an existing broker installation fail without runtime failover. Explicit route
selection does not uninstall or disable another integration. Finish and reconcile
active work before changing routes; use one connection throughout a workflow.

Existing registrations are compared using the broker's complete normalized
schema, including defaults and tool classifications. A matching executable
alone does not establish equivalent policy; malformed registrations are refused.

Broker installation copies the route-aware `fanuc-robot` skill into the selected
profile's `skills` folder, so skipping the standalone plugin retains the FANUC
guidance. Owned, unedited copies can be updated; edited/custom copies are
preserved and reported. Every bundle retains the standalone plugin. Codex's
[plugin packaging](https://developers.openai.com/plugins/build/plugins) supports
skills alongside MCP connections.

## Registration and policy

The optional adapter starts with:

```powershell
python -B -m fanuc_workbench.bridge.broker_server --config C:/RobotTools/robot-policy.json
```

It keeps all 48 standalone tools and adds one read-only metadata tool,
`list_broker_capabilities`. The standalone entry point and its schemas are
unchanged. Export a descriptor without registration or robot contact:

```powershell
python -B -m fanuc_workbench.bridge.broker_server --config C:/RobotTools/robot-policy.json --manifest C:/RobotTools/fanuc-service.json
```

The descriptor selects `session_mode: exclusive`: review plans and feature
workers belong to the retained app process. Acquire a context and keep it through
review/commit, job completion and reconciliation. Another chat cannot borrow it.
Releasing or losing the process discards in-memory plans; durable capture/job
evidence is separate. Shared/transient mode is not selected automatically. The
broker serializes calls to this registration; the app's
[concurrent dispatcher](REQUEST_CONCURRENCY.md) still applies to direct MCP/API
clients and controller access.

New registrations default to `allow_writes: false` and an empty outer-tool
allowlist. Classified reads work. Local preparation, staging, starting/cancelling
jobs and reloads also need the broker write channel because they change app state.
For an intentionally enabled NEW registration, add `--broker-allow-writes` to
setup (or `--allow-writes` when exporting). This creates an exact allowlist of the
current non-read outer tools. New tools are not automatically authorized by an
existing registration. App read-only settings, per-robot policy, exact proposal
approval, single-use plans and verified readback remain required. Descriptors
contain absolute runtime/config paths and required credential environment-variable
names, never credential values. Those variables must be available to the broker
process when it starts.

Setup does not refresh a running broker, acquire a controller, reload workers,
answer a form or change robot policy. Use the broker's `refresh_services` after
setup. For intentional descriptor/policy replacement, reconcile existing work
and use the broker's administrative CLI; setup will not replace an existing policy.

## Discovery and workflow

1. Search for the FANUC service, inspect `describe_service`, acquire its exclusive
   context and inspect exact outer tool schemas with `describe_tool`.
2. Call `refresh_capabilities` with the context for the owned catalog. Search
   entries are hints, not callable routes or approval.
3. Describe a capability at its broker `capability_revision`, then call the
   referenced `describe_operation` through the read channel. Use the returned app
   operation revision and schema for `run_read_operation` or `prepare_change`.
4. Present the exact target, before/proposed bytes or values and proposal hash.
   Only an authorized `commit_*` may perform the reviewed change, retaining app
   confirmation through the broker form relay. Give each intended state-changing
   call a unique broker request ID.
5. After a lost response, inspect `get_request_status` and app evidence. Do not
   replay with a new ID, switch routes or clear a recovery barrier.

Catalog discovery validates pinned extension metadata without executing workers,
reloading features, exposing robot IDs/endpoints or contacting a controller. Its
app revision includes exact operation descriptors/revisions. A loaded generation
takes precedence over staged files. After an authorized feature reload, refresh
the owned catalog. Broker catalog, capability, operation, target and proposal
revisions have different meanings.

## Verification boundary

Offline tests cover route selection, policy preservation, skill ownership,
schema compatibility and metadata discovery. Optional actual-broker tests use
synthetic controller transports for retained preparation, approval, readback,
backup jobs, worker reloads, stale revisions and competing frontends. Set
`FANUC_TEST_BROKER_SOURCE` to a compatible broker checkout
and run `tests/test_broker_interop.py`; this is not a runtime dependency.

Fresh-chat skill selection and confirmation UI need separate host/profile
acceptance. Native program loading and physical-controller acceptance remain
pending. Building/testing does not migrate existing installed connections.
