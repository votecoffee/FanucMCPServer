# Local FanucMCPServer plugin setup

The bridge runs on the Windows PC that can reach the controller. A headless
Python distribution shares FanucMCPServer services without requiring Qt or changing
the GUI's environment. Codex uses stdio MCP; other compatible clients can use
the same server. There is no OpenAI model API call or API key in this bridge.
See [controller interfaces and software requirements](CONTROLLER_INTERFACES.md)
for the services and FANUC licenses needed by each optional capability.

## Name change and existing installations

The project/repository is **FanucMCPServer**. New bundles identify the plugin
and stdio server as `fanuc-mcp-server`, and the local marketplace as
`fanuc-mcp-server-local`. Python distributions are `fanuc-mcp-server` for the
GUI/shared services and `fanuc-mcp-server-bridge` for the headless runtime.
Commands are `fanuc-mcp-server`, `fanuc-mcp-server-gui` (GUI distribution),
`fanuc-mcp-server-doctor` and `fanuc-mcp-server-extension`.

App, bridge distribution and plugin releases share the fixed timestamp described
in [release versioning](RELEASE_VERSIONING.md). The plugin/cache and wheel version
is its canonical numeric identity; installer metadata also retains padded display.

`fanuc_workbench` Python imports and the `fanuc_workbench.plugins` SDK entry-point
group retain their names for compatibility. The old `fanuc-workbench*` commands
remain aliases. `FANUC_MCP_SERVER_CONFIG` is the preferred configuration variable;
`FANUC_WORKBENCH_CONFIG` remains a fallback. Explicit `--config` takes precedence.
The GUI reads old FANUC Workbench profiles when its new profile file is absent,
and preserves the old file when saving to the new location.

The current [stable installer](STABLE_LAUNCHER.md) prepares a new app-owned
offline installation. Legacy in-place runtime folders, evidence and uncertainty
remain retained; the installer refuses to overwrite them. The desktop installer
has its own environment and legacy package migration. Existing controller/native
profiles retain their independent acceptance gates.

## Build and install

The root `InstallPlugin.cmd --help` describes the checkout wrapper. After building
the bundle below, `InstallPlugin.cmd --bundle "C:/RobotTools/FANUC Bundle"`
forwards the remaining setup arguments unchanged. Include the explicit stable
installation arguments below; use `--register none` to defer publication or `--codex-home` to choose a disposable
registration profile. `InstallPlugin.cmd --bundle PATH -- --help` shows bundle
setup help without installation. Setup defaults to this app's standalone plugin;
it never selects a universal plugin.

The root `Install.cmd` handles the desktop environment and optional Start menu
shortcut; `--launch` opens the app only after success. Both CMDs preserve quoted
paths and nonzero exit codes. Neither installs the separate Windows manager from
[CodexMCPBroker](https://github.com/votecoffee/CodexMCPBroker).

Build in a Python environment with pip, setuptools and wheel. This step uses
the package index to resolve dependency wheels for that Python/Windows version:

```powershell
python -B scripts/build_robot_plugin.py --output C:/RobotTools/FanucPluginBundle --framework-wheel C:/Packages/universal_codex_tools-0.20.0-py3-none-any.whl
```

The framework wheel must match `packaging/framework.lock.json`; the builder
checks its SHA-256, metadata and RECORD, then resolves dependencies using the
committed framework constraints. See [app-owned framework hosting](APP_FRAMEWORK.md).
No separate broker plugin is used.

Keep that bundle at a stable path. Install with the matching Python version;
the setup script verifies file hashes and installs only from bundled wheels:

```powershell
py -3.14 -B C:/RobotTools/FanucPluginBundle/setup.py --runtime-dir C:/RobotTools/FanucRuntime --config C:/RobotData/offline-policy.json --expected-selection-sha256 absent --management-registry C:/RobotData/manager-registry
```

Setup installs the app's own Codex plugin by default (`--register plugin`). The
shared framework is a Python package dependency and does not require a second
Codex plugin. Broker registration, its runtime/write flags and automatic route
selection have been removed. An old runtime recorded as a broker integration
must first have its retained work reconciled, then use a new standalone runtime
directory; setup refuses to mutate the old runtime or clear its recovery state.

Use `--register none --installed-plugin-version VERSION` to prepare/select a
runtime while retaining an observed existing standalone plugin. Publish the
returned host-local direct connection separately when needed; keep one enabled
server route. Optional
`--codex-home C:/RobotTools/IsolatedCodexProfile` confines CLI registration to a
separate profile. Matching Python and Codex are installation prerequisites.
Setup retains a windowless base interpreter and standard library, so startup
does not depend on the installation's original Python path or venv redirector.
The app directory must be empty or owned by the stable installer. Setup performs
no controller connection or write and accepts only zero-robot offline policies.

Supply the reviewed policy explicitly on each install. Relative data paths are
resolved against its original folder, preserving existing evidence. Updates
require the exact prior selection SHA-256 and a new fixed release; they never
replace a live peer's backend or mutate a retained runtime.

The local plugin keeps its own `plugin.json` and `fanuc-robot` skill. The bundle
hashes an immutable `mcp.template.json`; setup emits `mcp.json` with the selected
app-owned `python/pythonw.exe`, `StartMcp.py` and stable working directory.
The `${FANUC_MCP_INSTALL_ROOT}` source markers are installer recipes; no host
variable expansion is assumed. Publish only the generated absolute binding.
The stable entry verifies immutable snapshots and preserves original SDK wire
handles, EOF and exact parent lifetime. Configuration and evidence live outside
the plugin cache. Public marketplace submission and
native Codex confirmation UI acceptance remain pending.

The managed stdio profile requires Windows. Its SDK streams remain ordinary
stdin/stdout pipes. A bounded private activation handshake establishes ledger
identity and job membership before the MCP worker starts; activation does not
prefetch MCP input. The framework background launcher is not substituted for
SDK stdio. See [resource ownership and acceptance](RESOURCE_OWNERSHIP.md).

The 0.8 host supports feature reload behind a stable MCP interface. See
[ROBOT_EXTENSIONS.md](ROBOT_EXTENSIONS.md) for local staging, dependency isolation,
reload, rollback and the operation catalog. Updating an older host to this
interface is a one-time client/plugin update; later compatible feature changes
use `reload_extensions` in the existing session.

## Configure a controller

The initial runtime JSON contains zero robots, writes disabled and elicitation
approval. Add explicit robot profiles using `examples/robot_bridge.example.json`
as the schema reference. Configured roots restrict read access. Passwords belong
in named environment variables, not JSON, command-line arguments or chat.
Reload endpoint-only edits as described below. Other configuration changes
require a new MCP process.

Only one MCP worker can own a policy state directory. A second plugin/direct
connection using it is refused until the existing owner finishes shutdown.
Use one installed registration and the owning connection's concurrent request
support; see [worker admission and lifetime](SERVICE_MANAGEMENT.md).

For ROBOGUIDE, use the virtual controller's actual local TCP listener address and
FTP port. A workcell's saved real-robot synchronization address is a different
destination. Discover listeners from the running virtual controller or its
documented service settings; do not assume an address/port across sessions.
Read-only listing, retrieval, backups and analysis work through the same FTP
backend used for real controllers. Optional native program loading requires the
separate provider and policy described in [ROBOT_NATIVE_API.md](ROBOT_NATIVE_API.md).
Program execution and motion are unsupported.

ROBOGUIDE can assign different loopback addresses when its virtual controllers
restart. An open ROBOGUIDE application alone does not establish a running file
service. Rediscover FTP/HTTP listeners owned by the current virtual-controller
processes and verify which virtual controller each endpoint represents before
updating local profiles. For a configured description service, update both the
profile host and its matching `comment_service_url`; keep write allowlists and
approval policy intact. Do not substitute a saved physical-robot address.

The bridge initially loads its JSON configuration at process startup. Starting
with version 0.7, use `reload_connections` after an operator updates connection
settings in that same local JSON. The tool accepts no path or endpoint arguments,
refuses changes to robot IDs, credentials, roots, HTTP scheme, write/approval
policy, limits or storage, and refuses active work. Finish or cancel read jobs
first. Successful endpoint changes invalidate pending plans and make no network
connection; then check `list_robots` and `probe_robot`, and retrieve fresh bytes
before preparing a controller change. Other bridge processes are unaffected.

Version 0.6 and earlier require reconnecting the MCP server/client (or restarting
Codex for its bundled local connection) to pick up configuration changes. Loading
the newer server is a one-time prerequisite for the refresh tool. Policy changes
still require a new process in all versions. An independent local MCP/API session
can use the newer service while the existing Codex connection remains running.

## Verify

```powershell
C:/RobotTools/FanucRuntime/venv/Scripts/python.exe -B -m fanuc_workbench.bridge.doctor --config C:/RobotTools/FanucRuntime/robot_bridge.local.json
```

Doctor initializes a real MCP stdio session, lists tools and configured robots,
and reports approval policy. By default it makes no controller connection. Add
`--probe robot_id` for an explicit read-only file-service connection test.
The report is also saved as `doctor.json` during setup. A successful probe does
not verify controller physical identity, program loading or runtime behavior.

Begin with `list_robots`, `capabilities`, `probe_robot`, `list_files`, `read_file`
and a backup. Enable writes only with exact file/description allowlists. Present
the prepared diff, target and SHA-256 before committing an explicitly approved
change. A transfer/readback is separate from controller loading and execution.

API clients can use `read_captured_file` for hash-checked base64 byte ranges and
`stage_file` for a proposed file up to 1 MiB. Pass its returned `source_file` to
the normal preparation tool. These tools do not require client filesystem access
and do not perform robot writes.

To create a new TXT/CSV storage file, add its exact path to `creatable_files`
and include its parent device in read roots. `MD:` and new program files are
refused. Use `prepare_file_create`, review the absence check/proposal, then
`commit_upload` with the prepared hash and client confirmation. Creation checks
absence again at commit; it is not atomic against other controller clients.
Use `writable_files` separately for subsequent replacements. Both allowlists
default to empty and writes default to disabled.

Both the installer and plugin launcher can run from paths containing spaces.
Dependency compatibility is checked against Python major/minor, OS and machine
architecture. Build a new bundle for another platform or Python version.
