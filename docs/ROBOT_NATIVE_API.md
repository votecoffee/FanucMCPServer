# Native controller data, register changes and program deployment

This development lane adds shared services and a GUI view for native R/PR/I/O,
current position, tasks, alarms and program information; reviewed R/PR value
changes; and native loading/compilation of LS programs. The MCP catalog carries
these operations without adding permanent MCP tool schemas.

The first provider uses the locally installed FANUC `FRRobot.FRCRobot` public
COM API to connect to the configured robot host over TCP. COM here is a vendor
data API, with no desktop/window automation. It requires a compatible licensed
FANUC installation. Other machines/controllers must verify provider availability.
The adapter uses bounded, separate worker processes and never automatically
retries a failed write. The host owns policy, preserved reads, prepared plans,
confirmation, audit, preflight and readback. Feature code cannot dispatch native
writes or arbitrary vendor API/KCL methods.

Native access is disabled by default. A local operator explicitly selects the
provider and exact register/program targets. Register changes preserve type and
position metadata unless the reviewed proposal explicitly supports a change;
initial PR edits change existing axis values only. Uninitialized PRs are refused.
Program loading accepts validated LS source, checks native task/program state,
and transfers it to `md:NAME.LS` through the configured FTP/SFTP backend for the
controller's ASCII loader. The controller needs compatible ASCII upload support.
Transfer acceptance alone is not reported as successful compilation. Deployment
never selects, starts or executes a program. FANUC's `IRobot.Load` takes a
controller-device path; FanucMCPServer does not pass local PC filenames to that API.

Every change starts from a preserved native read tied to robot, endpoint, target
and SHA-256. Preparation freezes the proposal. Commit requires exact approval,
rechecks state and consumes the plan before a vendor write. Readback and loader
evidence distinguish verified results from uncertain failures. There is no
atomic compare-and-swap guarantee against other clients and no automatic rollback.

Validate native changes on an explicitly configured test controller before
physical use. Review the exact prepared proposal before committing, preserve
original values/files and verify readback. Keep controller-specific endpoints,
approvals and acceptance reports in local storage outside version control.
Native program deployment validation and physical-controller acceptance remain
pending; offline tests do not establish either.

## Configure and use

FanucMCPServer 2.1 adds **Native Controller** with live values/status, reviewed R/PR
editing and LS deployment. Load the same local robot policy used by the MCP
bridge and choose a robot. The FTP/SFTP profile remains the file transport;
the native adapter connects to that profile's host using the installed vendor
API. The app requires pywin32 and compatible FANUC Robot Object licensing.
FANUC libraries are not redistributed.

Native fields on each configured robot are:

```json
{
  "native_provider": "frrobot",
  "native_register_targets": ["R[999]", "PR[1,100]"],
  "native_programs": ["FWBTEST"]
}
```

These are examples, not automatically selected test targets. Defaults are
`disabled` and empty allowlists. Global `allow_writes` and configured approval
still apply. Native policy changes require a host configuration update;
`reload_connections` accepts endpoint-only changes. Connection/feature reloads
invalidate pending native plans while preserving evidence.

The host still advertises 48 permanent MCP tools. Describe these new catalog
operations and use the exact returned revision (`core.native@1` initially):

| Operation | Purpose |
| --- | --- |
| `core.controller.status` | Current position, tasks and alarm history/active flag |
| `core.controller.sample` | Typed R/PR/I/O reads |
| `core.controller.programs` | Paged native program metadata |
| `core.controller.reconnect` | Explicit worker recovery; invalidates native plans |
| `core.registers.read` | Preserve the typed value and endpoint-bound SHA-256 |
| `core.registers.update` | Prepare `{type,value}` for R or `{values:{axis:value}}` for PR |
| `core.programs.inspect` | Preserve presence, catalog/directory and TP/LS bytes |
| `core.programs.deploy` | Prepare an LS file from `source_dir` using its inspection hash |

Reads use `run_read_operation`; proposals use `prepare_change`, followed by exact
review and `commit_change`. Existing recording services use native values when
configured, retaining their two-second recording interval limit. The GUI and
Python API use the same `bridge.native` methods and core plans. The confirmation
dialog shows the target, old/new values or source hash and proposal SHA-256.
Editing a proposal invalidates its prepared GUI approval.

## Verification limits

- R values support signed 32-bit INTEGER and finite 32-bit REAL. REAL rounding is
  shown before approval and readback is exact.
- PR editing supports initialized Cartesian/joint positions and existing axes
  only. Group, representation, frame, tool, configuration and external axes stay
  bound. Approved axis readback tolerance is **0.0001 mm/degrees**; metadata is exact.
- Deployment accepts ASCII LS source up to 1 MiB and uppercase application names
  up to eight characters. It refuses protected programs and active/paused TP tasks.
- Verification requires transfer acceptance, native compiled-program presence, a nonempty TP capture and a
  normalized LS comparison. Instruction/position tokens and rows remain bound;
  line numbering and generated owner/size/date/file/line-count fields are ignored.
  Other differences fail verification. This is not runtime/physical equivalence.
- Original TP/LS bytes and catalog/directory evidence are retained before changes.
  Failed load/readback never retries or rolls back automatically. Plans are
  consumed when the commit attempt begins.
- Alarm history differs from the API's active-alarm flag. Captures are sequential,
  not simultaneous snapshots. Task status is observed data, not a safety interlock.
  Motion, program start/stop, I/O writes and frame writes are excluded.
