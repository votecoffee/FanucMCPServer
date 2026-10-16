# FANUC Workbench robot bridge (local development version)

The GUI and chat tools use the same Python controller service. This extends the
existing project; no separate robot transport implementation is required.

```text
PySide6 GUI --------> ControllerService -----> existing FTP / SFTP backends
                            ^
Codex MCP tools --> RobotBridge
                    access policy, plans, evidence and audit

Local API application --> either service, using the appropriate access policy
```

## What moved out of the GUI

`fanuc_workbench.services.controller.ControllerService` now owns connection tests,
file listing/download/upload, live file comparison, restore preview, and the
entry points into existing backup/restore services. `main_window.py` and
`feature_tabs.py` call these methods instead of opening backend connections or
implementing live comparisons themselves. The temporary comparison directories
are cleaned up. GUI confirmation dialogs and worker-thread dispatch stay in the
GUI. Existing GUI raw uploads retain their prior behavior and confirmation; they
do not silently acquire the chat adapter's new upload policy.

The shared service also implements a verified replacement operation used by
`RobotBridge`. Bounded FTP/SFTP reads live in `services.transfer`. Diagnostics,
comment editing, monitoring, parsers, backup, and selective restore were already
in services and remain there. GUI extension plugins can obtain the new service
class using `context.services.get("controller_service")`; the older registry
entries remain supported.

Neither the shared controller service nor the bridge imports PySide6. The
project's existing normal installation still declares GUI dependencies.

## Available MCP tools

| Tool | Behavior |
| --- | --- |
| `list_robots` | Configured robot IDs, connection addresses, roots and write policy; no connection or secrets |
| `list_files` | One remote directory, with at most 1000 returned entries |
| `read_file` | Exact local file copy, SHA-256, size, and up to 65536 characters of text |
| `collect_logs` | Retrieve 1-10 explicit text/diagnostic paths and parse alarm evidence |
| `prepare_upload` | Snapshot an edited local file, preserve current controller bytes, and return a review plan |
| `cancel_upload` | Consume a pending plan while retaining its evidence |
| `commit_upload` | Apply the approved bytes to one configured application-file target and verify readback |

Log filenames must come from a directory listing or known controller
documentation. Missing files are reported unavailable. This version does not
invent controller-specific log paths, poll indefinitely, or claim an atomic
multi-file snapshot. It has no motion, program-start, register-value, DCS,
mastering, or general system-configuration write tool. Full backup remains
available through `ControllerService.backup` and the GUI; it is not a chat tool
in this first version.

## Local installation and Codex setup

Use the Python environment for the checkout containing this change. In its root:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[mcp,dev]"
Copy-Item .\examples\robot_bridge.example.json .\robot_bridge.local.json
```

An existing compatible environment can be used instead. Edit
`robot_bridge.local.json` before connecting. `192.0.2.1` is an example address,
not a discovered robot. Keep that file, evidence, edited files, and credentials
out of source control. Paths in the JSON resolve relative to that JSON file.

Configure the robot host, transport, roots and username. Use `password_env` for
password authentication, for example `"password_env": "FANUC_ROBOT1_PASSWORD"`.
Set that variable in the server process's environment using your normal secret
management. Do not put the password into a chat, command-line argument, or JSON.
SFTP defaults to port 22 when omitted and requires a verified host key in the
Paramiko/system host-key store. The bridge rejects unknown-host trust overrides.
Use FTP only on an appropriate robot network; the existing FTP transport does
not encrypt its connection.

In Codex's MCP settings, add a local stdio server. Set **command** to the
absolute path of this environment's `Scripts/python.exe`, and **arguments** to:

```text
-m fanuc_workbench.bridge.mcp_server --config C:/absolute/path/robot_bridge.local.json
```

The equivalent local Codex configuration is:

```toml
[mcp_servers.fanuc_workbench]
command = "C:/absolute/path/.venv/Scripts/python.exe"
args = ["-m", "fanuc_workbench.bridge.mcp_server", "--config", "C:/absolute/path/robot_bridge.local.json"]
```

Restart the MCP connection and check `list_robots` before any network operation.
This configuration has not been installed into your Codex settings automatically.
The server uses local stdio and does not expose an HTTP listener or robot port.

`plugins/fanuc-workbench` also contains portable local plugin descriptors. These
are a **development scaffold**: they assume `fanuc-workbench-mcp` is installed on
the client's PATH and `FANUC_WORKBENCH_CONFIG` is supplied in its environment.
They do not bundle Python or dependencies, and installation in the Codex plugin
directory has not been verified. Use the explicit interpreter configuration above
for initial testing. Public directory submission is a separate packaging task.

## Reviewed replacement workflow

Writes default to disabled. A local operator must deliberately set
`"allow_writes": true` and add exact paths such as `"md:MAIN.LS"` to that robot's
`writable_files` list, then restart the server. Policy cannot be changed by a
bridge tool. There are no wildcard write targets. The initial file classes are
LS, KL, TP, PC, TXT and CSV application files; system/variable/diagnostic file
classes and recognized safety/mastering filenames are refused.

1. Read the existing controller file and keep its SHA-256.
2. Copy the retrieved bytes into the configured `source_dir` and edit them.
   Preserve encoding and line endings deliberately. No source encoding or
   newline conversion occurs in the bridge.
3. Call `prepare_upload` with the robot ID, exact remote path, source filename
   relative to `source_dir`, and the original SHA-256. The target must already
   exist and be retrievable; new-file creation is not implemented.
4. Review the target, complete proposed file, original backup and hashes. The
   returned text diff is a bounded convenience preview and can be incomplete;
   binary changes require external review of the staged file.
5. Obtain the user's explicit approval of that exact change, then call
   `commit_upload(plan_id, approved_sha256)` using its proposed hash.
6. The service reconnects, saves and hashes another preflight copy, rejects a
   changed original, uploads the staged bytes, downloads readback and compares
   hashes. The response identifies the backup and readback evidence.

Plans expire after 15 minutes by default and are single use, including failed
attempts. Server restarts discard plans. Changes to the source file or proposed
preview after preparation do not change the in-memory plan bytes. Backups,
proposed files, preflight/readback evidence and `audit.jsonl` are retained; the
operator must manage retention and disk capacity. Audit records include UTC
times and hashes, and the upload-start audit must succeed before a write.

The approval hash binds bytes; it does **not** prove that a human approved them.
The MCP host/agent must honor the explicit approval workflow. This bridge is an
access boundary for its tools, not a sandbox against someone who can run local
shell commands, edit its configuration, or open robot connections independently.
Remote roots are lexical path restrictions; configure trusted controller roots
without symlinks to other locations, particularly on SFTP servers.

FTP/SFTP do not provide atomic compare-and-swap or a controller lock. Another
writer can race the preflight check. Coordinate access to the robot. Interrupted
uploads, readback mismatches, or post-write audit failures produce an uncertain
result and consume the plan. No automatic retry or rollback occurs: inspect the
actual controller bytes before deciding the next change. Program loaders may
transform or consume an uploaded LS file; byte readback can therefore fail even
when a loader acted. Test the controller's behavior before relying on this path.

File transfer equality is not compiler acceptance, native program-load
acceptance, running-program status, safety validation, or commissioning approval.
This development version has been tested offline, not against a physical robot.

## Using the shared service from Python or an API application

```python
from pathlib import Path
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.controller import ControllerService

robot = ControllerService(RobotProfile("Test cell", "192.0.2.1", roots=["md:"]))
entries = robot.list_files("md:")
robot.download_file("md:MAIN.LS", Path("capture.ls"), maximum=16 * 1024 * 1024)
```

For the same configured policy and review workflow as MCP:

```python
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge

bridge = RobotBridge(BridgeConfig.load(Path("robot_bridge.local.json")))
robots = bridge.list_robots()  # no robot connection
```

An OpenAI API application can execute these operations locally through its own
function-call dispatcher. This implementation does not call OpenAI, need an
OpenAI API key, or implement that application's model loop. A hosted remote MCP
deployment would additionally need an authenticated gateway with robot-network
reachability; there is no remote server or tunnel in this version.

## Validation and documentation

Run `python -m pytest -q` with the `dev` and `mcp` extras installed. Tests cover
the existing application services, shared controller operations, path and policy
denials, exact bytes, bounded FTP/SFTP reads, stale bases, expiry/replay,
interrupted uploads, corrupt readback, audit failures, GUI-independent imports,
and a real MCP SDK stdio subprocess handshake. Transport tests use local doubles;
they do not prove physical-controller compatibility. SDK-only tests skip when
the optional MCP extra is absent.

Official integration references checked October 16, 2026:

- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [Desktop-only local MCP plugins](https://developers.openai.com/plugins/deploy/desktop-only)
- [OpenAI API function calling](https://developers.openai.com/api/docs/guides/function-calling)

The local plugin descriptors are not a public-directory release package.
