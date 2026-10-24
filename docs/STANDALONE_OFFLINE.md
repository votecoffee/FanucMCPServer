# Direct standalone offline profile

Use this optional profile to import and inspect saved workspace copies without
controller connections, extension code execution or native utilities. It shares
the application's existing local artifact and analysis services. It is not a
sandbox against a client or operator with local shell access.

For explicitly pinned local MakeTP/PrintTP compilation, the separate
[local-native candidate profile](LOCAL_NATIVE_VALIDATION.md) retains the same
zero-robot/controller-write refusal. The 15-tool `--offline` profile itself is
unchanged and cannot enable those tools through arguments or environment.

Create a dedicated private policy with no robots and writes disabled:

```json
{
  "state_dir": "offline-evidence",
  "source_dir": "offline-inputs",
  "robots": {},
  "allow_writes": false,
  "max_concurrent_requests": 2,
  "max_pending_requests": 4,
  "max_file_bytes": 1048576,
  "max_backup_files": 100,
  "max_backup_bytes": 16777216
}
```

The direct MCP client entrypoint is:

```text
-B -m fanuc_workbench.bridge.mcp_server --offline --config C:/RobotTools/offline-policy.json
```

Use the selected environment's absolute Python path as the command. The explicit
`--config` overrides configuration environment variables. An incompatible policy
refuses before state/lease or bridge construction. `--offline` is a direct-server
option; the optional owned stdio supervisor and shared host are separate profiles
and are not used by this entrypoint. Do not change an active registration or
install a candidate while its release is held.

The dispatch allowlist contains 15 tools: `list_workflows`, `read_workflow`,
`read_workflow_reference`, `list_robots`, `capabilities`, `import_workspace`,
`list_artifacts`, `artifact_files`, `read_artifact`, `read_workspace_metadata`,
`analyze_workspace`, `compare_workspaces`, `inspect_program`, `transform_pose`
and `read_audit`. Imports create hash-manifested copies; original inputs stay
unchanged. The 20 workspace analyses use saved files only. No jobs are started.

Controller probes, remote transfers/writes, native API/MakeTP/PrintTP, program
deployment, extension dispatch/reload, connection-policy reload, shared-host
routing and protocol preview/Tasks are absent from dispatch. They cannot be
enabled through tool arguments, inherited credentials, an existing extension
registry or a changed source configuration file. The loaded policy is fixed for
this instance; reconnect explicitly to select another profile. Saved data is
never executed as Python or a native application.

The MCP client owns creation of the frontdoor Python process and any Python
environment launcher. FANUC does not substitute the framework background launcher
for SDK stdio or claim post-launch self-registration as pre-launch containment.
Up to eight offline processes share an evidence state through separate OS-held
client slots. The actual surviving process holds its own slot through its request
drain. It does not construct controller/native/job owners, acquire their exclusive
lease or recover/replay any job. Immutable artifact IDs are unique, catalog entries
publish atomically and audit appends/reads share the existing cross-process
journal lock. A client can read a workspace imported by another client; closing
one leaves its peers available. Startup/stop audit receipts retain the shared resource
API's host/PID/creation FILETIME/executable and owner-generation identity. No
temporary registration helper owns it. This profile starts no Node/shell, feature
worker, native worker, compiler, service/SCM launcher or attached user application.

Normal client EOF uses the SDK's request drain; close preserves partial artifacts,
journals and uncertain prior work. Manager reporting runs in the same process and
does not grant restart or PID termination. A crashed/unreachable frontdoor is
reported offline, not attested stopped or blindly killed. There are no app-owned
child processes to reap in this profile. Native/shared/dirty resources and their
reconciliation remain outside its scope, untouched and unavailable.

Source/stub and harmless process fixtures are separate from real MCP acceptance.
Before release, an authorized disposable direct canary must verify the actual
installed/cache versions and normal SDK initialization, useful saved-file read,
simultaneous same-policy initialization, concurrent imports/reads/audit, peer
preservation through EOF/crash/reconnect and exact loss identities. Capacity
refusal must apply to a ninth client, not ordinary second-chat access. Check manager
offline/restart refusal and preserve the exact wheel/plugin/config hashes.
