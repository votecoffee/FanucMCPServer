# Tray service reporting

The shared [CodexMCPBroker repository](https://github.com/votecoffee/CodexMCPBroker)
provides both the framework and the independently installed Windows manager.
Run its `InstallManager.cmd` to install that management app. There is no universal
Codex plugin. FANUC keeps its own standalone server, Codex plugin and native
skills; management reporting does not replace their connection or authorization.

The standalone MCP process registers with the shared Windows MCP Service Manager
for its lifetime. The app uses `universal-codex-tools==0.20.0` and MCP SDK 2.3.0.
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
Status also reports the bounded private-worker inventory and its uncertain count.
Uncertain cleanup reports **degraded** and retains the owner. The inventory is
a process-local cache. Framework 0.20 durable records preserve RPC launch intent,
exact ownership and native recovery barriers independently of the endpoint.
Other launch-path integration and process acceptance remain pending.
The optional app-host owning process also registers, with **degraded** status
because its retained-worker activity is unknown to this collector. Its stdio
proxies and backend workers do not create duplicate owner registrations.

Components include the running app distribution, framework, SDK and, when
associated, plugin. Running versions are captured at process startup; installed
metadata is refreshed independently. Available versions stay unknown and
`update_pending` stays false unless selected metadata establishes an update.
For a [stable installation](STABLE_LAUNCHER.md), the hash-bound current manifest
provides selected backend versions; they can differ from this process's running
versions. The installed standalone plugin remains a separate observation.
The manager may additionally display its own local publisher inventory.
No package-feed or GitHub update check runs here.

Status identifies per-chat stdio workers, shared app-host owners or explicitly
marked validation workers. A per-chat worker is not a shared multi-chat server.
The reviewed stable offline launcher serves in its surviving windowless Python
process, admits no app-created children, and records exact parent-loss/EOF/dead
generation receipts. Its one-actor private job and eight-frontdoor admission
limit do not adopt SDK console/redirector actors or attached applications.
Legacy/full-profile Python stdio supervisors stay alive for their children and forward
opaque bytes through explicit pipes. Client EOF closes worker stdin and lets
the child drain its domain work. A private activation handshake binds both
launcher/worker identities before MCP starts. Unexpected supervisor death closes
its private job and terminates contained workers; controller outcomes remain
unverified until app receipts and native state are inspected. A client may also
force-terminate its tree; such a record is offline rather than a clean stop.
Stdio ancestors are report-only resources, so manager termination cannot bypass
nested native recovery barriers. No user-attached application is terminated.
Old detached instances cannot be safely retired from zero counters alone: their
reporting-only endpoint cannot atomically fence admission or attest to uncertain
effects. Do not bulk-kill names/PIDs or clear restart journals.

## Worker admission

The controller/local-native standalone entrypoint and experimental app-host backend acquire the same OS
lease under the configured `state_dir/mcp-owner` before constructing their
bridge. The limit is **one MCP worker per state directory**, including aliases
of that directory. Extra connections fail with an ownership message; they do not
wait in a startup queue, attach to the existing worker or select another route.
Use the owning client's bounded concurrent requests. Multi-chat shared hosting
remains a separate, explicit deployment choice.

The saved-evidence `--offline` profile admits up to eight independent clients per
state directory. These views use separate OS-held slots, share immutable artifact
publication and the existing audit lock, and drain their own requests. They never
construct/reconcile a job/native owner or acquire/release its exclusive lease.
An offline second chat does not require a shared-host migration or replacing a
live legacy peer. Stable-launcher frontdoor limits also remain unchanged.

The lease stays held through bridge/job/native shutdown, independent of tray
availability and launcher survival. If draining fails, the lease is retained
until process exit. A lock file's presence does not mean a process is alive;
admission uses the OS lock, without deleting files or testing a remembered PID.
The lease does not provide restart supervision, force-stop blocked native calls
or prove the outcome of an interrupted write. Different policy state directories
are independent. Older runtimes without this lease must be stopped separately
before adopting this version; this change cannot fence their processes.

Source regression cases cover duplicate admission, state aliases, construction
failure and drain failure using inert objects. Process EOF, parent-loss, multiple
launcher and shutdown acceptance must also pass in isolated registries before
release. No release or installation is implied by source changes.

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
python -B -m fanuc_workbench.bridge.stdio_supervisor --config C:/Test/empty-robot-policy.json --management-registry C:/Test/FanucRegistry --instance-role validation
```

Alternatively set `FANUC_MCP_MANAGEMENT_REGISTRY` for a disposable launcher test.
The optional framework command accepts the same registry argument before `serve`.
These options select local management state, not controller endpoints or access.
Keep registries and receipts outside public Git. Tests use fake transports and
separate registries for status, busy/stale denial, duplicate receipts, durable
restart barriers and clean shutdown. They do not establish a safe live restart,
controller acceptance or a particular chat host's reload behavior.

See the [resource ownership matrix](RESOURCE_OWNERSHIP.md) for exact handles,
attached-object preservation and remaining launch-path acceptance. A stopped
management record does not attest that every native resource was cleaned up.

See the framework's [management contract](https://github.com/votecoffee/CodexMCPBroker/blob/5b2cac50ca24d21a15b732e0424bb02fd794e1fb/docs/service-management.md).
