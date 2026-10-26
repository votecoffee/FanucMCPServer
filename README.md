# FanucMCPServer

MCP server and desktop workbench for FANUC robot files, diagnostics, and reviewed changes.

Connect a compatible chat client or Python application to shared FANUC file,
backup and analysis services. The optional desktop interface uses the same
services. A stable MCP host supports feature extensions that can be reloaded
within an existing session.

The repository and application are named **FanucMCPServer**. The Python
distribution is `fanuc-mcp-server`; the `fanuc_workbench` import namespace and
legacy commands remain compatible with the former FANUC Workbench name.
The shared dependency comes from [CodexMCPBroker](https://github.com/votecoffee/CodexMCPBroker),
which now provides an MCP application framework and a separately installed Windows
management app. FanucMCPServer retains its standalone MCP server, app-owned Codex
plugin and native skills. There is no universal Codex plugin or broker route.
The MCP/native integration is under development; physical-controller acceptance
and native program deployment validation remain pending.

The current source checkpoint is **26.1026.110126** (America/New_York). App,
package and standalone plugin versions share this fixed timestamp. See
[release versioning](docs/RELEASE_VERSIONING.md) for canonical package identities,
leading-zero normalization and the stamp-once release procedure.
An explicit [direct standalone offline profile](docs/STANDALONE_OFFLINE.md)
exposes saved-workspace analysis without extension or native launch routes.
Up to 32 bounded offline metadata connections share an evidence state, with
eight data calls executing and 16 waiting. Guidance and capabilities stay
available without data admission; artifact publication and audit locking remain shared. Starting
an offline chat does not acquire native ownership or recover another chat's jobs.
Offline Windows status reporting verifies existing private directory permissions
without rewriting peers' permissions on every connection; see
[connection readiness](docs/CONNECTION_READINESS.md) for the lifecycle and capacity contract.
A separate [local native validation profile](docs/LOCAL_NATIVE_VALIDATION.md)
adds pinned MakeTP/PrintTP preparation, single-use compilation and diagnostics
without controller or GUI access. It is a candidate for a coordinated native
pilot; source changes do not migrate the installed offline profile.

## Table of Contents

- [Background](#background)
- [Install](#install)
- [Usage](#usage)
- [Capabilities and limits](#capabilities-and-limits)
- [Documentation](#documentation)
- [Development](#development)
- [API](#api)
- [Maintainers](#maintainers)
- [Contributing](#contributing)
- [License](#license)

## Background

Robot troubleshooting often involves collecting controller files, comparing
backups and reviewing edits across several tools. FanucMCPServer exposes those
operations through shared Python services, an MCP adapter and a PySide6 desktop
application. FTP/SFTP provide file transport; an optional installed FANUC Robot
Object provider adds native controller data over TCP.

The MCP host owns configured targets, credentials, retained evidence, approval
and readback. Reloadable Python workers supply analyses and change proposals
behind a versioned operation catalog. Compatible feature updates use
`reload_extensions` in the current MCP session. Host schemas, policy or plugin
metadata changes require the corresponding host/client update.

The host supports [bounded concurrent requests](docs/REQUEST_CONCURRENCY.md),
with parallel access to independent controllers and serialized preflight,
changes and readback for each configured host. Status/cancellation calls have
their own worker lane; existing approval and single-use change plans still apply.

## Install

Windows entry points in this checkout:

```powershell
.\Install.cmd --help
.\Install.cmd --venv-dir "C:/RobotTools/FANUC Desktop" --no-shortcut
.\InstallMCP.cmd --help
.\InstallPlugin.cmd --help
```

`Install.cmd` installs the desktop and its Start menu shortcut unless
`--no-shortcut` is supplied; launch is explicit with `--launch`.
`install_and_run.bat` remains the install-and-launch alias. `InstallPlugin.cmd`
forwards setup arguments to an already built bundle; it does not build one.
Use `--codex-home` with a disposable profile for registration tests; registration
can change that profile's Codex config. Help and `--register none` do not register
a plugin. Both CMDs forward quoted paths and propagate failure exit codes.
`FANUC_INSTALL_PYTHON` optionally selects an absolute Python executable instead
of `py -3`. See [plugin setup](docs/ROBOT_PLUGIN_SETUP.md) for the bundle build.

After installing the desktop, double-click [RunGUI.cmd](RunGUI.cmd) in the repo
root, or run `.\RunGUI.cmd`. It opens the GUI using `.venv` without reinstalling.
For a custom desktop environment, set `FANUC_GUI_PYTHON` to its absolute
`pythonw.exe` path before running the command.

`InstallMCP.cmd` prepares an app-owned stable offline launcher and immutable,
hash-verified runtime outside checkouts and plugin caches. It retains old runtime
bytes and live generations, and returns an exact connection for separate
publication. It does not edit Codex settings or start the server. See the
[stable launcher guide](docs/STABLE_LAUNCHER.md) for required wheel/policy inputs,
atomic selection, bounds and lifecycle checks. Source plugin connection files
are installer recipes; publish only the generated absolute stable binding.

### Dependencies

- Python 3.11 or newer and Git for a source installation.
- Windows for the documented plugin launcher and optional native controller API.
- A compatible MCP client to use chat tools. The bridge itself makes no model API
  calls and requires no OpenAI API key.
- For native access: a compatible licensed FANUC Robot Object installation. Vendor
  libraries are installed separately and are not redistributed.

Install the desktop application and MCP adapter from source on Windows:

```powershell
git clone https://github.com/votecoffee/FanucMCPServer.git
cd FanucMCPServer
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install C:/Packages/universal_codex_tools-0.20.0-py3-none-any.whl
.\.venv\Scripts\python.exe -m pip install -c packaging/framework-requirements.txt -e ".[mcp]"
```

MCP packages pin **SDK 2.3.0** and **universal-codex-tools 0.20.0**. Obtain
the verified framework wheel using the [framework guide](docs/APP_FRAMEWORK.md);
public package-feed availability is not assumed. The separate app-owned host is
experimental. Source/package upgrades do not migrate installed plugins or live
connections.

The MCP process also provides [tray-manager status](docs/SERVICE_MANAGEMENT.md)
from cached local state, with restart disabled until an app-owned supervisor can
enforce safe quiescence across chats. Setup preserves the standalone plugin and
does not install another manager.
The independent [Windows manager](https://github.com/votecoffee/CodexMCPBroker/blob/main/docs/service-management.md)
is installed from its own repository with `InstallManager.cmd`; it needs no
Codex plugin or MCP connection. A newer framework release does not change this
app's reviewed 0.20.0 pin or migrate a running installation automatically.

Controller/local-native MCP workers acquire an exclusive lease for their policy's state directory and
hold it through shutdown. A second standalone or app-host backend worker using
that state is refused before service construction. Independent requests still
share the owning worker's bounded dispatcher. See [worker ownership](docs/SERVICE_MANAGEMENT.md).
Private feature/native workers also have a bounded inventory and creation-bound
Windows handles. RPC workers now use the shared durable ledger; remaining launch paths are listed in
the [resource ownership matrix](docs/RESOURCE_OWNERSHIP.md); release acceptance is pending.
Offline compiler utilities use atomically created private background jobs, bounded
file IPC and hash-checked executables. Each utility job admits at most eight
processes and 512 MiB of private commit; the actual caller owns its lifetime.
Host-policy helpers retain their separate owned RPC lifecycle. Native utility
runtime acceptance is required before enabling a candidate installation.
ROBOGUIDE integration stages backups for a separately opened user session;
automatic application launch remains unavailable pending ownership acceptance.
Installed plugin/plain MCP connections use an owned stdio supervisor in the
selected runtime. SDK byte streams are preserved, with report-only ancestor
records and private worker containment. Real lifecycle and installed acceptance
remain separate from source/unit checks.

The packaged skill is a stable bootstrap for [current workflow guidance](docs/WORKFLOW_GUIDANCE.md).
Ordinary guide readers and Skills-over-MCP share one maintained source and support
explicit guide publication without reconnecting. Native skill metadata still
requires the host's normal install/reload lifecycle.

The next protocol features are [staged for validation](docs/PROTOCOL_FEATURES.md).
Tasks, discovery caching and artifact resources are disabled in normal startup;
their presence in source does not establish client or controller acceptance.

The default distribution includes the desktop dependencies. For a separate
runtime without Qt, follow the [headless plugin bundle guide](docs/ROBOT_PLUGIN_SETUP.md).

Bundle setup installs the app's standalone Codex plugin by default. Use
`--register none` to prepare without registration; host-local direct bindings
are published separately from the returned connection. Broker routing is no longer supported; the shared
framework remains an app dependency. See the [plugin guide](docs/ROBOT_PLUGIN_SETUP.md).
For desktop use alone, omit the `mcp` extra or run `install_and_run.bat`.
On Windows, that script also creates **Start > FanucMCPServer > FanucMCPServer**
with the application icon for the current user. For a manual installation, run
`.\.venv\Scripts\python.exe -B -m fanuc_workbench.desktop_install` to add it.
See the [desktop guide](docs/DESKTOP_GUIDE.md#start-menu-shortcut) for removal or
updating the shortcut after moving an installation.
Existing installations should follow the [upgrade notes](docs/ROBOT_PLUGIN_SETUP.md#name-change-and-existing-installations).

## Usage

### CLI

Create a private configuration from the example:

```powershell
New-Item -ItemType Directory -Path .local -Force | Out-Null
Copy-Item .\examples\robot_bridge.example.json .\.local\robot_bridge.local.json
.\.venv\Scripts\fanuc-mcp-server-doctor.exe --config .\.local\robot_bridge.local.json
```

The example uses a placeholder address with writes and native access disabled.
Edit the local configuration before connecting to a controller. Relative state
and source paths resolve beside that file, inside the ignored `.local/` directory.
Doctor checks the MCP session without contacting a robot unless `--probe` is given.

Configure your MCP client to launch this environment's absolute Python path with
the following arguments, replacing the example paths:

```toml
[mcp_servers.fanuc-mcp-server]
command = "C:/path/to/FanucMCPServer/.venv/Scripts/python.exe"
args = ["-B", "-m", "fanuc_workbench.bridge.stdio_supervisor", "--config", "C:/path/to/FanucMCPServer/.local/robot_bridge.local.json"]
```

`fanuc-mcp-server --config <path>` is the equivalent stdio server command.
Start with `list_robots` and `capabilities`, then explicitly probe the configured
target before listing files or collecting evidence. See the
[MCP guide](docs/ROBOT_BRIDGE.md) for tools and reviewed changes.

Launch the desktop interface with:

```powershell
.\.venv\Scripts\fanuc-mcp-server-gui.exe
```

Configure a connection through **Robot â†’ Connectionâ€¦**, take or open a backup,
then inspect programs, diagnostics and comparisons. Detailed native setup is in
the [native API guide](docs/ROBOT_NATIVE_API.md).
Use **Robot â†’ Manage connectionsâ€¦** to stage profile additions, edits, copies
and removals, then save and select the desired robot. Names are unique; custom
ports/timeouts are retained, and switching the active target pauses live file polling.
Live / Recorder remembers watch drafts, binds recordings to their source/watches,
and offers **New recording** and **Export recording JSON** for separate captures.
Use **Open recording JSON** to inspect saved desktop captures offline, including
paged values/errors, gap-aware trends, timeline/cycle events and the file hash.
The viewer leaves active collection and connection settings unchanged; see the
[desktop guide](docs/DESKTOP_GUIDE.md#saved-recordings).
Use **Help â†’ Open desktop log folder** for local startup/error diagnostics and
**Help â†’ About FanucMCPServer** for version and settings locations.

### Controller access mode

Use **Settings â†’ Safeguards and policiesâ€¦** for the full
[chat-permissions editor](docs/SAFEGUARDS_GUI.md): online-write permission,
per-robot targets/read scope, approval method, expiry, limits and app-host
permissions. Changes require a validated review, retain the original policy and
do not alter running sessions or contact a controller.

Open **Settings â†’ MCP accessâ€¦**, select the MCP server's local robot policy,
and load it. **Read-only controller access** is checked by default. Save the
desired mode, then reconnect the MCP server using that file. Existing sessions
keep their previous policy until reconnected. The **Native Controller** view
needs its policy loaded again separately.

The setting uses the existing JSON key: `"allow_writes": false` means read-only;
`true` permits reviewed writes. **Controller write scope** selects `allowlisted`
(the default) or `all_supported` for broad access to supported write targets on
all configured robots. The broad mode removes the need to maintain exact write
lists. Keep machine-specific choices in private local policy, rather than changing
public defaults. Backups, analysis and local staged edits remain available in
either mode. Configured read roots, explicit providers, supported write classes,
proposal approval, preflight and readback still apply. `capabilities` and the
doctor report show the loaded read-only mode. Other desktop upload/restore tools
retain their own confirmations.

### Python

List configured targets locally without opening a controller connection:

```python
from pathlib import Path
from fanuc_workbench.bridge.service import RobotBridge

with RobotBridge.from_config(Path(".local/robot_bridge.local.json")) as bridge:
    print(bridge.list_robots())
```

Keep the bridge alive while background jobs run and close it when finished.

Shared offline program services include `trace_logic`, `explain_wait` and
`TriggeredRecorder` in `fanuc_workbench.services.program_analysis`. They retain
unresolved references and require explicit observed source/revision metadata;
see [program diagnostics](docs/PROGRAM_DIAGNOSTICS.md).
The same guide covers semantic comparisons, dependency packages and isolated
MakeTP/PrintTP validation. Use `python -B -m fanuc_workbench.program_tools --help`
for the offline CLI; native compiler success and controller acceptance remain
separate checks. Native jobs hash explicitly selected supporting files and retain
diagnostics when a selected file is unavailable. File pins do not establish
dependency completeness or compatibility; no fixed vendor layout is assumed.
The desktop exposes these tools at **Search / Analysis â†’ Program diagnostics**,
including opt-in triggered captures from existing native reads. The optional
reloadable `workbench.programs` 1.1.0 release adds six read analyses through the
operation catalog while retaining the established tool schemas and original release.

## Capabilities and limits

| Connection profile | Tools | Scope |
| --- | --- | --- |
| Direct `--offline` | 15 | Saved evidence and analyses; zero robots/writes, no native/extension launch |
| Direct `--local-native-policy` | 20 | Saved evidence plus pinned local compilation plans; native pilot required, no controller/GUI routes |
| Full standalone server/plugin | 55 | Configured service-layer operations under the selected robot/access policy |
| Experimental app-owned host | 14 front-door / 56 backend | Explicit publication and separate host/native acceptance |

These are separate profiles. Updating source or a dependency does not switch the
installed profile, broaden online permissions or establish native acceptance.

| Area | Available capabilities |
| --- | --- |
| Files and backups | FTP/SFTP listing, exact file/log capture, file-based backup progress, manifests, comparisons and reviewed per-file recovery |
| Offline engineering | LS inspection, lint, cross references, R/PR/frame/comment evidence, mastering/DCS comparisons and documentation packages |
| Program analysis | Reloadable LS tracing, sampled WAIT diagnosis, semantic comparisons, dependency review and local change packages |
| PLC mapping | Local CSV/L5X tag, alias and UDT-member import, searchable catalog, reviewed I/O-reference suggestions and saved workspace mappings |
| Monitoring | Bounded samples, trends, I/O timelines, cycle recordings, alarm correlation, troubleshooting snapshots, offline summaries, evidence bundles and sampled request/ack analysis |
| Native controller API | R/PR/I/O reads, position, tasks, alarms and program metadata; reviewed R/PR value changes and LS deployment |
| Experimental SNPX | Optional digital/group/analog I/O samples with explicit word decoding and raw assigned-word reads over TCP; controller compatibility remains unverified |
| Offline SNPX analysis | Reloadable word decoding/comparison, frame inspection and named I/O payload checks with explicit read-order assumptions |
| Extensions | 51 established tools, four staged job tools, a versioned operation catalog and reloadable feature workers, including 20 workspace analyses |
| Visual Line Tracking | Optional reloadable VQLOG event analysis, filters, timelines and explicit tick-period assumptions |
| Alarm logs | Optional reloadable text alarm records, recurrence and two-log code/text comparison with explicit clocks and retained unrecognized lines |
| Desktop workflows | Remembered robot selection, saved adjustable columns/panes/tabs with layout reset, fleet backups, in-app scheduling, commissioning baselines, PLC handshake analysis, projects and ROBOGUIDE handoff packages |

MCP writes default to disabled and require exact target policy, preserved originals,
reviewed proposals and readback. The example configuration requests client
confirmation; verify confirmation support in the chosen client before enabling
writes. Passwords belong in named environment variables. The MCP bridge requires
verified SFTP host keys; FTP traffic is unencrypted.
The GUI's **Settings > Create robot policy** form creates a new local policy with
an exact-target JSON review; **MCP access** changes an existing policy's access mode.

Motion, program execution/start/stop, I/O forcing, frame writes and
mastering/DCS writes are unsupported. Native deployment does not select or run a
program. Offline analysis, file-transfer success and loader verification each
have separate limits; none establishes production or commissioning acceptance.
See the [coverage matrix](docs/ROBOT_BRIDGE_COVERAGE.md) for details.

Default reads and backups use FTP/SFTP. An optional exact-file HTTP reader can
retrieve generated MD files for reads, logs and file-based samples; HTTP also
supports reviewed description changes. Native data uses FANUC's Robot Object API over TCP. Native access needs
licensed PC/controller support, and loading LS source needs ASCII Program Loader.
See [controller interfaces and software requirements](docs/CONTROLLER_INTERFACES.md)
for feature-specific dependencies, including SFTP server options.
The optional [SNPX reader](docs/SNPX_READS.md) uses TCP without vendor PC binaries;
it requires a compatible controller service/options and never changes assignments.

## Documentation

- [Desktop connections, layouts, PLC imports and policy creation](docs/DESKTOP_GUIDE.md)
- [MCP setup, tools and Python API](docs/ROBOT_BRIDGE.md)
- [Headless plugin installation and upgrades](docs/ROBOT_PLUGIN_SETUP.md)
- [Native controller configuration and verification limits](docs/ROBOT_NATIVE_API.md)
- [Controller interfaces and software requirements](docs/CONTROLLER_INTERFACES.md)
- [Optional allowlisted HTTP file reads](docs/HTTP_READS.md)
- [Experimental read-only SNPX/HMI provider](docs/SNPX_READS.md)
- [Reloadable offline SNPX decoding and comparison](docs/SNPX_DECODING.md)
- [Offline SNPX handshake/reply evidence inspection](docs/SNPX_EVIDENCE.md)
- [Offline named I/O values versus SNPX payloads](docs/SNPX_IO_EVIDENCE.md)
- [Offline watch availability, changes and timing summaries](docs/RECORDING_ANALYSIS.md)
- [Recording and referenced-sample evidence bundles](docs/RECORDING_EVIDENCE.md)
- [Sampled request/ack analysis with gaps and nominal timing](docs/RECORDING_HANDSHAKE.md)
- [Reloadable VQLOG analysis](docs/VQLOG_ANALYSIS.md)
- [Reloadable alarm records and recurrence analysis](docs/ALARM_ANALYSIS.md)
- [Saved alarm log comparison](docs/ALARM_COMPARISON.md)
- [Selected LS program dependencies and static reachability](docs/PROGRAM_DEPENDENCIES.md)
- [Community integration references](docs/COMMUNITY_INTEGRATIONS.md)
- [Reloadable feature development](docs/ROBOT_EXTENSIONS.md)
- [Shared-service coverage](docs/ROBOT_BRIDGE_COVERAGE.md)
- [Desktop Plugin SDK](PLUGIN_SDK.md)
- [Changelog](CHANGELOG.md)

## Development

Follow [connection readiness and capacity](docs/CONNECTION_READINESS.md), including
cold startup and expected open-chat demand through the actual stable binding.

Install the test dependencies and run the offline suite:

```powershell
.\.venv\Scripts\python.exe -m pip install -c packaging/framework-requirements.txt -e ".[dev,mcp]"
.\.venv\Scripts\python.exe -B -m pytest -q
```

The suite uses synthetic fixtures and simulated controller transports. Keep
personal notes, configurations, captures and local test reports outside Git or
under `.local/`. See [CONTRIBUTING.md](CONTRIBUTING.md) for repository hygiene and
validation requirements.

## API

The [MCP and shared Python API guide](docs/ROBOT_BRIDGE.md) describes `RobotBridge`,
tools, artifacts and prepared changes. Use the
[extension contract](docs/ROBOT_EXTENSIONS.md) for reloadable operations, and the
[Plugin SDK](PLUGIN_SDK.md) for desktop tabs.

## Maintainers

[@votecoffee](https://github.com/votecoffee)

## Contributing

Questions and bug reports belong in [GitHub issues](https://github.com/votecoffee/FanucMCPServer/issues).
Pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), include relevant
validation, and use sanitized examples suitable for a public repository.

## License

[MIT](LICENSE) Â© 2026 votecoffee.
Adapted community components retain their [upstream notices and licenses](fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md).
