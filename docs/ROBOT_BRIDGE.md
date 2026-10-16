# FANUC Workbench robot bridge (expanded development version)

The GUI, local Python API and MCP adapter share controller, parser and analysis
services. The server now exposes 38 tools. No physical robot has been contacted;
native acceptance remains pending. See [the service coverage matrix](ROBOT_BRIDGE_COVERAGE.md).

## Setup

Use Python 3.11 or newer in the checkout containing these changes:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[mcp,dev]"
Copy-Item .\examples\robot_bridge.example.json .\robot_bridge.local.json
```

An existing compatible environment can be used instead. Edit the JSON before
connecting. The example address 192.0.2.1 is not a discovered robot. Paths resolve
relative to the JSON. Keep local configuration, edits, evidence and credentials
out of source control.

Add a local stdio server in Codex MCP settings using this environment's absolute
Scripts/python.exe path and these arguments:

```text
-m fanuc_workbench.bridge.mcp_server --config C:/absolute/path/robot_bridge.local.json
```

Equivalent local configuration:

```toml
[mcp_servers.fanuc_workbench]
command = "C:/absolute/path/.venv/Scripts/python.exe"
args = ["-m", "fanuc_workbench.bridge.mcp_server", "--config", "C:/absolute/path/robot_bridge.local.json"]
```

Restart the connection, then call list_robots and capabilities before network
operations. The optional local plugin bundle includes dependency wheels, a skill
and a launcher for a separate runtime. Its installer can register the plugin or
the stdio MCP server when explicitly requested by a command-line option. See
[offline plugin setup](ROBOT_PLUGIN_SETUP.md). No HTTP listener or robot tunnel
is installed. No OpenAI API key is required by this bridge.

## Credentials and approval

Configure hosts, roots, transports and usernames outside chat. For passwords use
a password_env entry, for example FANUC_ROBOT1_PASSWORD, supplied through normal
secret management. Passwords do not belong in JSON, command arguments or chats.
SFTP defaults to port 22 and requires a verified host key. Unknown-host trust
overrides are refused. The existing FTP transport is unencrypted.

Writes default to disabled. The example now selects approval_mode equal to
elicitation. This asks the MCP client to confirm the exact target and reviewed
change hash. Decline, cancellation or unsupported elicitation prevents the write.
A model-supplied hash alone is insufficient. The SDK protocol path is tested;
support in your actual Codex installation still needs checking. Read tools do not
require elicitation.

The older chat approval mode remains for compatibility and relies on explicit
approval in the conversation. Elicitation is client-mediated confirmation,
**not independent human authentication**: a client can automate its response.
Local Python, shell access and configuration remain trusted. Audit records are
ordinary local files, not a tamper-proof ledger.

## Tools

| Area | Tools |
| --- | --- |
| Discovery/files | list_robots, capabilities, probe_robot, list_files, read_file, collect_logs |
| Artifacts | import_workspace, list_artifacts, artifact_files, read_artifact |
| Backups/jobs | start_backup, start_fleet_backup, job_status, list_jobs, cancel_job |
| Workspace analysis | analyze_workspace, compare_workspaces, inspect_program |
| Offline editing | edit_workspace_file, transform_pose |
| Local metadata/projects | read_workspace_metadata, write_workspace_metadata, workspace_project |
| Live data/recordings | sample_robot, start_watch, analyze_recording, compare_recordings |
| Snapshots/packages | capture_snapshot, compare_snapshots, make_package |
| Reviewed files | prepare_upload, commit_upload, cancel_upload, prepare_restore_file |
| Reviewed descriptions | prepare_comment_update, commit_comment_update, cancel_change |
| Audit | read_audit |

read_file preserves exact bytes and returns SHA-256 and bounded display text.
Use directory listings or controller documentation for log names. Missing files
are unavailable; multi-file capture is not atomic.

list_files supports offset/limit pagination and reports total/next_offset. Older
FTP controllers with UNIX-style LIST responses use a batch listing; unknown
formats retain the per-file NLST fallback. probe_robot tests file-service access
and reports elapsed time; it does not establish robot running state or identity.

write_workspace_metadata saves GUI-compatible checklist, PLC tag and handshake
maps in a new workspace artifact. Checklist status is user-authored evidence,
not commissioning approval. workspace_project creates or reads a local project,
or adds a captured workspace to a new project version. Originals are preserved.
start_fleet_backup starts independent jobs for 1-8 distinct configured robot IDs;
inspect each result and not_started. It does not install a recurring scheduler.

start_backup returns a job ID immediately. Omit paths for configured roots, or
supply selected file paths. Poll job_status and inspect result.complete, warnings,
and backup_manifest.json: a completed job can contain an incomplete capture.
Cancelled/failed backups retain partial artifacts when a manifest exists.

Backups reuse BackupService with optional ASCII companions, path validation and
limits. Defaults are 16 MiB per file, 5000 transfer attempts and 512 MiB per
capture. Directory entries and recursion are also bounded. Fresh connections
isolate transfer failures; there is no blind write retry. Cancellation is
cooperative between transfers, not an interruption of an active network call.
Two read workers and a bounded queue serve independent robots.

History survives restart; orphaned jobs become interrupted and do not resume.
Advisory locks coordinate bridge clients sharing state_dir and the same configured
host string. The existing GUI, other applications and DNS aliases are outside
that lock scope. FTP/SFTP provide no atomic compare-and-swap.

import_workspace copies a source_dir-relative backup into preserved state,
rejecting links and enforcing limits. Artifact IDs select manifest-listed files,
not arbitrary paths. Bytes and file sets are checked before analysis. Reimport
changed evidence. read_artifact returns bounded byte ranges as base64, including
binary data. Lists, files, analysis and audit support pagination.

## Analysis, editing and packages

analyze_workspace supports these kinds:

```text
health options alarms alarm_intelligence mastering dcs ethernetip lint
programs dependencies unused_programs xref register_usage comments positions
variables numeric_registers position_registers frames io
```

These reuse existing services and return evidence-only results. Option matches
do not authenticate installed options. inspect_program adds structural LS
validation and flow inspection; it does not invoke a compiler or loader.

edit_workspace_file creates a separate source copy. Kinds are comment,
numeric_register and position_register. Targets include R[1] and PR[2,7]
(group 2, index 7). Values are structured, for example {"value": "12.5"} or
{"values": {"J2": 9.5}}. Unsupported axes, nonfinite values and ambiguous records
are refused. Existing encoding and line endings are preserved, or an
unrepresentable edit fails.

Edited VA/DG listings are offline documentation/analysis. They are not uploaded
as live register or frame changes. Live register-value/frame writes and I/O
forcing have no verified provider.

capture_snapshot and compare_snapshots reuse troubleshooting snapshots.
make_package supports documentation (HTML/XLSX/PDF), baseline, roboguide staging,
and deployment with an explicit file list. These are offline artifacts; they do
not establish commissioning approval, launch ROBOGUIDE, or load a controller.

## Samples and recordings

sample_robot reads needed NUMREG.VA, POSREG.VA and IOSTATE.DG files for 1-100
expressions. R, PR, common I/O and flags are supported when readable evidence
exists. PR[group,index] selects multigroup records. Per-signal errors and source
files are retained. The two-second minimum is shared by bridge clients using
the same host and state directory.

start_watch captures 1-300 samples at intervals of 2-60 seconds. It retains
trend/timeline CSV, cycle JSON, recording JSON and source artifact IDs. Missing
samples do not invent transitions. cycle_signal must name a watched expression.
Failed/cancelled jobs preserve partial recordings. This is file-based sampling,
not deterministic real-time controller I/O.

analyze_recording supports cycle_profile, handshake with explicit mapping, and
alarm_correlation with a workspace and compatible timestamps. compare_recordings
compares cycle durations. Correlation does not synchronize clocks or prove causation.

## Reviewed changes and recovery

Enable writes locally and add exact application paths to writable_files, then
restart the server. Initial classes are LS, KL, TP, PC, TXT and CSV, excluding
recognized safety/mastering filenames. System/variable/diagnostic classes are refused.

1. Retrieve the existing controller file and original SHA-256.
2. Edit a separate source copy with deliberate encoding/line-ending handling.
3. Call prepare_upload with the robot, remote path, source_dir-relative filename
   and original hash. The target must already exist and be readable.
4. Review the complete proposal, backup and hashes. The returned diff is a
   bounded convenience preview; binary proposals need external review.
5. Commit only the approved plan/hash. Elicitation mode also requires client confirmation.
6. The service saves and hashes another preflight copy, rejects stale originals,
   uploads the prepared bytes and compares downloaded readback to the staged hash.

prepare_restore_file uses a manifest-bound backup through this same workflow.
Recovery is deliberate and per-file; rollback is not automatic. Plans expire
after 15 minutes by default, are single-use including failed attempts, and vanish
on restart. Later edits to source or preview files cannot alter prepared bytes.

Live descriptions additionally require comment_service_url as an HTTP(S) origin
on that robot host and exact comment_targets, such as ["R[1]", "PR[1]", "DIN[1]"].
This is disabled by default. Preparation uses a retrieved NUMREG.VA, POSREG.VA or
IOSTATE.DG listing and existing ComSet mapping. Group-1 PR comments are supported.
Commit rejects stale listings, applies the comment and requires matching fresh
listing readback. Redirects are refused. HTTP 200 alone is insufficient.
Controller-specific endpoint/security behavior still needs physical validation.

Interrupted writes or mismatched readback produce uncertain state and consume
the plan. Inspect actual bytes/comments before another change. Coordinate other
writers; preflight cannot prevent races. An LS loader may transform/consume a
file and defeat byte readback even after acting. Transfer equality is not
load/compile, running-program, safety or commissioning acceptance. Controller
mode/running-state interlocks remain unsupported.

The operator owns retention and disk capacity for backups, partial jobs, edits,
readbacks and UTC audit records. Remote roots are lexical restrictions; use
trusted roots without cross-root symlinks.

## Shared Python API and validation

```python
from pathlib import Path
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge

with RobotBridge(BridgeConfig.load(Path("robot_bridge.local.json"))) as bridge:
    robots = bridge.list_robots()
    supported = bridge.operations.capabilities()
    # job = bridge.operations.start_backup("robot1")
    # status = bridge.operations.jobs.status(job["job_id"])
```

Keep the bridge alive while jobs run, then close it to cancel/join workers.
The MCP entry point closes during orderly shutdown. Trusted applications can
also use ControllerService directly. API applications can dispatch function calls
locally to these operations. This package makes no OpenAI API/model calls and
needs no OpenAI API key. It supplies no hosted authenticated gateway or model loop.

Run python -m pytest -q with dev and mcp extras. Coverage includes original
services, all analysis kinds, encoding/bytes, partial captures, cancellation,
process locks, samples/recordings, packages, file/comment recovery and real SDK
stdio/elicitation flows. Controllers/transports are simulated; native acceptance
remains pending.

Official documentation checked October 16, 2026:

- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [MCP tools and elicitation](https://developers.openai.com/plugins/build/mcp-server)
- [Plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [Local MCP plugins](https://developers.openai.com/plugins/deploy/desktop-only)
