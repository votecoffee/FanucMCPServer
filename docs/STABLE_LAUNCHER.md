# Stable standalone launcher

The reviewed stable deployment profile is `standalone-offline`: the same 15
saved-evidence tools, zero configured robots and controller writes disabled.
Native compiler, controller, GUI, feature-worker and shared-host routes remain
separately gated. Installing a launcher does not enable them.

## Prepare and select

Use a dedicated app-owned directory outside source checkouts, plugin caches,
build output and temporary staging. `InstallMCP.cmd` prepares an immutable
dependency closure from a reviewed wheel inventory, retains a windowless base
Python and selects an exact manifest. It does not read/edit Codex settings,
register plugins, start MCP, contact robots or delete an old runtime.

```powershell
.\InstallMCP.cmd --help
.\InstallMCP.cmd --app-root "C:/RobotTools/FanucMCPServer" --wheelhouse "C:/Packages/FANUC/wheels" --wheel-manifest "C:/Packages/FANUC/wheel-inventory.json" --config "C:/RobotData/offline-policy.json" --expected-selection-sha256 absent --management-registry "C:/RobotData/manager-registry" --installed-plugin-version 26.1022.180303
```

The wheel inventory is a JSON array sorted by relative path, each row containing
`path`, `sha256` and `bytes`. Review every wheel, including the FANUC bridge's
fixed release and framework lock; the installer verifies the complete inventory
and the reviewed framework 0.20.0 hash. SDK stays 2.3.0. There is no online
dependency download during installation or startup. The supplied plugin version
must be an observed installed version, independently of the new backend version.

For updates, supply the exact SHA-256 of `launcher/current.json` instead of
`absent`. The installer refuses stale selection, existing release directories,
unowned roots, changed stable bootstrap bytes, linked paths or tampered prior
payloads. Incomplete/failed staging stays available for inspection. A bootstrap
change needs a separately reviewed migration with `--expected-bootstrap-sha256`; ordinary backend updates preserve
the stable entry bytes. Never bypass refusal by deleting uncertain files.

The layout is:

```text
app root/
  python/                   retained base Python and standard library
  StartMcp.py               stable stdlib-only entry
  launcher/current.json     atomic exact selection
  launcher/snapshots/hash/  retained hash-bound manifest
  launcher/backups/         exact prior selection bytes
  runtimes/release/          immutable packages, offline policy and metadata
    dependencies.zip        pure Python dependency/application code
    stdlib.zip              selected standard library
    native/                 declared native extensions and DLLs
    guidance/               explicit current workflow documents
  receipts/                 installation receipts
  sessions/                 durable process-generation and cleanup receipts
```

Policy state/source paths retain their original absolute data locations. Runtime
and launcher bytes never depend on the checkout branch or a plugin cache.
Installation preserves earlier runtimes and live generations. Existing sessions
keep their original imports; selection applies only to new launches. Offline
clients hold separate bounded slots and can share the same evidence state.
Each client drains only its own requests. The native/controller exclusive lease
remains unchanged; offline views never acquire, replace or release that lease.

## Publish the connection

Use the exact connection returned by installation: app-owned
`python/pythonw.exe`, `-I -B` and the absolute `StartMcp.py`, with the app root
as `cwd`. The bootstrap verifies the selected Python bootstrap/native closure and full backend inventory
before importing MCP packages. Missing, malformed, tampered or linked selections
refuse startup without falling back to another release or profile.

Archives and native payloads stay pinned against writes/replacement from
verification through process exit. Imports use only those archives and exact
manifest-declared native extensions. Unselected legacy standard-library files,
new native-directory modules and unsafe archive member paths are refused.
The installer retains its original unpacked staging for payload comparison.

The bundle installer `InstallPlugin.cmd --bundle ...` now uses this contract and
requires the same explicit policy, expected-selection hash and registry. It
retains the app-owned standalone plugin and skills. `--register none` prepares
without Codex publication and requires `--installed-plugin-version`; default
`--register plugin` installs the generated standalone plugin. Legacy in-place
runtime roots are preserved and refused. Direct host-local bindings may be
published independently using the returned connection, with duplicate plugin
server routes disabled and native skills retained. The source plugin's
`${FANUC_MCP_INSTALL_ROOT}` values are installer recipe markers, not a promise
that a host expands those variables; never publish an unresolved recipe.

Follow [binary-safe TOML publication](CONFIG_PUBLICATION.md). One serialized
publisher must capture the current config hash and exact original bytes, verify
the expected FANUC route, preserve unrelated settings and atomically publish.
Do not edit immutable cache files or restore stale global backups.

## Lifetime and evidence

The actual windowless interpreter serves MCP in process. No app-created helper,
shell, Node process, venv redirector or native child route is enabled. A private
Windows Job admits one actor and 128 MiB private commit; the job handle lives
through actual process exit. The client's SDK may separately own a redirector,
console host or job. Those actors must be inventoried during client acceptance;
they are not app-created processes or authority for app cleanup.

Each admitted process records its full kernel identity and an exact retained
parent handle. Physical Windows Store/MSIX images come from kernel handles, not
logical `sys.executable` aliases. Parent exit stops this offline process even if
another process keeps the input pipe open. Normal EOF follows SDK 2.3's original
wire-handle claims and closes the app. Protocol stdout carries no logs.

Admission is serialized and limited to 32 bounded metadata frontdoors, with at
most 4096 retained session receipts. 32 OS-held metadata slots also bound each
evidence state, including direct offline clients. Eight executing data calls and 16 waiters
are admitted separately, only for the duration of work. Closing/crashing one client
releases only its own slot. Audit reads/appends share the existing journal lock;
UUID artifact manifests publish atomically. Offline startup/close does not
construct a job/native owner or recover another process's journal. Only a positively absent/different prior
generation with no native intents or children can receive a dead-session receipt.
Unknown identities and uncertain intents refuse recovery; live peers remain
untouched. There is no PID/name-global termination, domain retry or journal replay.

Manager reports separate selected backend and running backend versions. Updating
selection can show a pending app update while an old generation still runs.
Plugin version is an independent installed observation. Restart stays disabled;
metadata cannot prove readiness or quiescence of a running peer.

Retain isolated acceptance for cache removal, selection/payload tamper refusal,
failed/concurrent publication, active-peer preservation, initialize/discovery,
current guidance, synthetic reads, EOF, abrupt process/parent death and reconnect.
Package/SDK checks do not prove activation in an already-open desktop chat or
authorize native/controller acceptance.

See [connection readiness and capacity](CONNECTION_READINESS.md) for steady/legacy
transition memory budgets, expected chat demand, cold startup and migration recovery.
