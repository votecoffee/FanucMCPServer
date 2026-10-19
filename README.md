# FanucMCPServer

MCP server and desktop workbench for FANUC robot files, diagnostics, and reviewed changes.

Connect a compatible chat client or Python application to shared FANUC file,
backup and analysis services. The optional desktop interface uses the same
services. A stable MCP host supports feature extensions that can be reloaded
within an existing session.

The repository and application are named **FanucMCPServer**. The Python
distribution is `fanuc-mcp-server`; the `fanuc_workbench` import namespace and
legacy commands remain compatible with the former FANUC Workbench name.
The MCP/native integration is under development; physical-controller acceptance
and native program deployment validation remain pending.

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

## Install

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
.\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
```

The default distribution includes the desktop dependencies. For a separate
runtime without Qt, follow the [headless plugin bundle guide](docs/ROBOT_PLUGIN_SETUP.md).
For desktop use alone, omit the `mcp` extra or run `install_and_run.bat`.
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
args = ["-B", "-m", "fanuc_workbench.bridge.mcp_server", "--config", "C:/path/to/FanucMCPServer/.local/robot_bridge.local.json"]
```

`fanuc-mcp-server --config <path>` is the equivalent stdio server command.
Start with `list_robots` and `capabilities`, then explicitly probe the configured
target before listing files or collecting evidence. See the
[MCP guide](docs/ROBOT_BRIDGE.md) for tools and reviewed changes.

Launch the desktop interface with:

```powershell
.\.venv\Scripts\fanuc-mcp-server-gui.exe
```

Configure a connection through **Robot → Connection…**, take or open a backup,
then inspect programs, diagnostics and comparisons. Detailed native setup is in
the [native API guide](docs/ROBOT_NATIVE_API.md).

### Controller access mode

Open **Settings → MCP access…**, select the MCP server's local robot policy,
and load it. **Read-only controller access** is checked by default. Save the
desired mode, then reconnect the MCP server using that file. Existing sessions
keep their previous policy until reconnected. The **Native Controller** view
needs its policy loaded again separately.

The setting uses the existing JSON key: `"allow_writes": false` means read-only;
`true` permits reviewed writes to configured targets. Backups, analysis and local
staged edits remain available in either mode. Enabling writes keeps the exact
target allowlists, confirmation and readback checks. `capabilities` and the
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

## Capabilities and limits

| Area | Available capabilities |
| --- | --- |
| Files and backups | FTP/SFTP listing, exact file/log capture, backup jobs, manifests, comparisons and reviewed per-file recovery |
| Offline engineering | LS inspection, lint, cross references, R/PR/frame/comment evidence, mastering/DCS comparisons and documentation packages |
| Monitoring | Bounded samples, trends, I/O timelines, cycle recordings, alarm correlation and troubleshooting snapshots |
| Native controller API | R/PR/I/O reads, position, tasks, alarms and program metadata; reviewed R/PR value changes and LS deployment |
| Experimental SNPX | Optional digital/group/analog I/O samples with explicit word decoding and raw assigned-word reads over TCP; controller compatibility remains unverified |
| Offline SNPX decoding | Optional reloadable integer/float interpretation of captured words with explicit byte/word order and unverified mappings |
| Extensions | 48 stable MCP tools, a versioned operation catalog and reloadable feature workers, including 20 workspace analyses |
| Visual Line Tracking | Optional reloadable VQLOG event analysis, filters, timelines and explicit tick-period assumptions |
| Desktop workflows | Fleet backups, in-app scheduling, commissioning baselines, PLC handshake analysis, projects and ROBOGUIDE handoff packages |

MCP writes default to disabled and require exact target policy, preserved originals,
reviewed proposals and readback. The example configuration requests client
confirmation; verify confirmation support in the chosen client before enabling
writes. Passwords belong in named environment variables. The MCP bridge requires
verified SFTP host keys; FTP traffic is unencrypted.

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

- [MCP setup, tools and Python API](docs/ROBOT_BRIDGE.md)
- [Headless plugin installation and upgrades](docs/ROBOT_PLUGIN_SETUP.md)
- [Native controller configuration and verification limits](docs/ROBOT_NATIVE_API.md)
- [Controller interfaces and software requirements](docs/CONTROLLER_INTERFACES.md)
- [Optional allowlisted HTTP file reads](docs/HTTP_READS.md)
- [Experimental read-only SNPX/HMI provider](docs/SNPX_READS.md)
- [Reloadable offline SNPX word decoding](docs/SNPX_DECODING.md)
- [Reloadable VQLOG analysis](docs/VQLOG_ANALYSIS.md)
- [Community integration references](docs/COMMUNITY_INTEGRATIONS.md)
- [Reloadable feature development](docs/ROBOT_EXTENSIONS.md)
- [Shared-service coverage](docs/ROBOT_BRIDGE_COVERAGE.md)
- [Desktop Plugin SDK](PLUGIN_SDK.md)
- [Changelog](CHANGELOG.md)

## Development

Install the test dependencies and run the offline suite:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mcp]"
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

[MIT](LICENSE) © 2026 votecoffee.
Adapted community components retain their [upstream notices and licenses](fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md).
