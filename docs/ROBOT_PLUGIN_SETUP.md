# Local FANUC Workbench plugin setup

The bridge runs on the Windows PC that can reach the controller. A headless
Python distribution shares Workbench services without requiring Qt or changing
the GUI's environment. Codex uses stdio MCP; other compatible clients can use
the same server. There is no OpenAI model API call or API key in this bridge.

## Build and install

Build in a Python environment with pip, setuptools and wheel. This step uses
the package index to resolve dependency wheels for that Python/Windows version:

```powershell
python -B scripts/build_robot_plugin.py --output C:/RobotTools/FanucPluginBundle
```

Keep that bundle at a stable path. Install with the matching Python version;
the setup script verifies file hashes and installs only from bundled wheels:

```powershell
py -3.14 -B C:/RobotTools/FanucPluginBundle/setup.py --runtime-dir C:/RobotTools/FanucRuntime --register plugin
```

Use `--register mcp` for plain MCP registration, or omit registration to install
only the runtime. Do not register both forms in the same profile. Optional
`--codex-home C:/RobotTools/IsolatedCodexProfile` confines CLI registration to a
separate profile. Python itself and Codex are prerequisites, not bundled software.
The runtime directory must be empty or owned by this installer. Existing user
configuration is preserved. Setup performs no controller connection or write.

The local plugin contains `plugin.json`, `mcp.json`, a `fanuc-robot` skill and a
stdlib launcher. The installer records absolute runtime/config paths in its
local `runtime.json` before CLI installation. The launcher reads those paths;
it does not install dependencies or contact a controller on its own. Configuration
and evidence live outside the plugin cache. Public marketplace submission and
native Codex confirmation UI acceptance remain pending.

## Configure a controller

The initial runtime JSON contains zero robots, writes disabled and elicitation
approval. Add explicit robot profiles using `examples/robot_bridge.example.json`
as the schema reference. Configured roots restrict read access. Passwords belong
in named environment variables, not JSON, command-line arguments or chat.
Restart the MCP connection after a configuration change.

For ROBOGUIDE, use the virtual controller's actual local TCP listener address and
FTP port. A workcell's saved real-robot synchronization address is a different
destination. Discover listeners from the running virtual controller or its
documented service settings; do not assume an address/port across sessions.
Read-only listing, retrieval, backups and analysis work through the same FTP
backend used for real controllers. Program loading/execution and motion are
outside this adapter.

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
