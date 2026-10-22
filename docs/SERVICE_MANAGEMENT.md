# Tray service reporting

The standalone MCP process registers with the shared Windows MCP Service Manager
for its lifetime. The app uses `universal-codex-tools==0.18.0` and MCP SDK 2.3.0.
Install the manager separately if desired; this app's installer does not install
or start another manager, change routes or require a universal plugin.

The default private registry uses the Windows user's Local AppData known folder
without package redirection, under `CodexMCPManager/Registry`. Each process has a
random instance ID and an authenticated loopback endpoint. Installed plugin
launchers provide the canonical association
`fanuc-mcp-server@fanuc-mcp-server-local`. A direct source/MCP launch without
installer metadata has no guessed plugin association.

## Status and versions

Status requests read an in-memory snapshot. A background collector reads only
local process counters, package metadata and the explicit plugin manifest. It
does not probe a robot, attach a project, invoke engineering tools, reconcile job
history or expose targets, credentials, tokens or endpoints in status messages.

The snapshot reports admitted requests and queued/running local jobs, plus a count
of retained file, comment and native review plans. These are process-local counts,
not an assessment of other chats, controller state or unresolved native effects.
Busy work reports **busy**; a stale/unavailable snapshot reports **degraded**.
The optional app-host owning process also registers, with **degraded** status
because its retained-worker activity is unknown to this collector. Its stdio
proxies and backend workers do not create duplicate owner registrations.

Components include the running app distribution, framework, SDK and, when
associated, plugin. Running versions are captured at process startup; installed
metadata is refreshed independently. Available versions stay unknown and
`update_pending` stays false unless an independent update source establishes an
update. The manager may additionally display its own local publisher inventory.
No package-feed or GitHub update check runs here.

The app component is the headless bridge distribution in a packaged runtime and
the desktop distribution in an editable development environment. Installing a
plugin does not establish a running chat connection. Existing processes load this
integration only after their normal operator-controlled reload. A clean exit
leaves a stopped record; an unreachable endpoint is offline.

## Restart is disabled

These chat-owned processes have no surviving app-owned supervisor. The manager
therefore reports restart disabled with that reason, including when counters are
zero. It cannot discard prepared plans, cancel transfers/jobs, release another
chat's leases, resolve uncertain effects or kill/relaunch a process. Reporting
does not prove restart admission or native quiescence.

Restart requests still use the framework's durable journal: exact duplicates
retain their receipts, changed payloads reject and uncertain intents block later
requests across process lifetimes. This integration never clears or reconciles
that journal and never schedules a restart effect. A future supervisor must
atomically fence new work and account for every chat's leases, review plans,
queued jobs and uncertain outcomes before accepting a restart. Acceptance would
still require separate observation of replacement health.

## Isolated verification

Use a dedicated registry for offline checks:

```powershell
python -B -m fanuc_workbench.bridge.mcp_server --config C:/Test/empty-robot-policy.json --management-registry C:/Test/FanucRegistry
```

Alternatively set `FANUC_MCP_MANAGEMENT_REGISTRY` for a disposable launcher test.
The optional framework command accepts the same registry argument before `serve`.
These options select local management state, not controller endpoints or access.
Keep registries and receipts outside public Git. Tests use fake transports and
separate registries for status, busy/stale denial, duplicate receipts, durable
restart barriers and clean shutdown. They do not establish a safe live restart,
controller acceptance or a particular chat host's reload behavior.

See the framework's [management contract](https://github.com/votecoffee/CodexMCPBroker/blob/7d8a0ccdc06598313061147ee8774837118a8861/docs/service-management.md).
