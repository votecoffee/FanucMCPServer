---
name: fanuc-robot
description: "Inspect FANUC robot files and diagnostic evidence, manage FanucMCPServer backups and local workspaces, and prepare reviewed application-file or description changes using the local FanucMCPServer MCP server. Use for this robot integration; not for general robotics questions or unsupported motion commands."
---

# FANUC robot work

Choose the configured integration route before starting a workflow. With the
standalone plugin, call its FANUC tools directly. With Universal Tools, search
for service `fanuc-mcp-server`, read `describe_service` and the exact outer tool
schema with `describe_tool`, then acquire its exclusive context. Include that
`context_id` and the current broker `catalog_revision` on routed calls. Use
`call_read_tool` for classified reads; local preparation, jobs and controller
commits use `call_write_tool` with a unique `request_id` when permitted by its
exact outer-tool policy. The robot's write policy and approval still apply.

Broker capability matches are discovery hints. Describe the capability at its
`capability_revision`, then call its referenced read-only `describe_operation`
through the broker to obtain the actual app revision and argument schema.
After a feature reload, refresh the owned capability snapshot. Keep broker,
operation, target and proposal revisions distinct.

Keep one route and retained context throughout prepare/review/commit. Other
frontends cannot borrow that context. Finish jobs and reconcile pending changes
before release, expiry or switching routes; closing the app host loses its
in-memory plans. After a lost broker write response, inspect `get_request_status`
and app evidence. Do not replay with a new ID or fall back to the direct plugin.
A recovery barrier requires operator reconciliation, not automatic reacquisition.

Use the FanucMCPServer tools for configured robots and preserved workspace
artifacts. Begin a new robot workflow by inspecting `list_robots` and
`capabilities`; use `probe_robot` only for the user's intended target. An empty
robot list needs local configuration before network work. Never guess addresses
or put passwords into tool arguments or the conversation.

After an operator updates verified connection endpoints in the existing local
configuration, `reload_connections` can apply those connection edits without
restarting this server. Finish active operations/jobs first, check `list_robots`
and `probe_robot` after refresh, and retrieve fresh evidence. Endpoint changes
discard pending plans. The tool cannot change write/approval policy or credentials.
Do not edit configuration to bypass a refusal. Older server versions need a
normal reconnect before the refresh tool becomes available.

For feature operations, use `list_operations` and `describe_operation`, then pass
the exact returned revision and schema-valid arguments to `run_read_operation`
or `prepare_change`. Existing named tools remain supported. After an operator
stages a trusted local release, `reload_extensions` replaces feature workers
without changing the MCP connection or its tool schemas. Finish active robot
operations/jobs first. A changed reload discards pending plans; retrieve and
prepare them again. Failed reloads preserve the active features and plans.
Extensions cannot introduce arbitrary native write classes; `commit_change`
uses the same exact-proposal approval and core verification as the named tools.

For native data, inspect the `core.controller.*`, `core.registers.*` and
`core.programs.*` operation descriptors. These require an explicitly configured
vendor provider. Preserve a native register/program inspection before preparing
a change. Review robot/endpoint, target, before/proposed values or source hashes
and the exact proposal SHA-256. Native changes use `commit_change` only after
explicit approval and client confirmation. R readback is exact; PR axes use the
stated numerical tolerance with exact metadata. LS deployment never selects or
runs a program. A failed native commit consumes its plan: inspect actual state
and evidence before preparing another change. Do not retry or roll back blindly.

For troubleshooting, retrieve relevant files/logs or start a bounded backup.
Follow job status, inspect capture completeness and unavailable-file errors, and
use artifact IDs for analysis. Retain source hashes and distinguish captured
evidence from live state. A successful transfer or option-text match does not
prove native load, installed options, running state or physical behavior.

For local edits, use separate workspace/source copies. VA/DG listings are
documentation and inspection evidence; editing them is not a live value/frame
write. Checklists and PLC/handshake/project metadata are local, user-authored
evidence. Checklists do not establish commissioning acceptance.

Use `read_captured_file` with a retained read's robot/path/hash for exact base64
byte ranges when local files are inaccessible. Reassemble and verify the full
hash before editing. `stage_file` saves up to 1 MiB of proposed exact bytes with
a required SHA-256 and returns a generated `source_file` for preparation. Preserve
encoding and line endings; neither tool contacts or writes a controller.

For a requested controller change, retrieve the current file/listing and prepare
the exact change through the supported tool. Present the robot, path/target,
original/proposed hashes, and complete proposal or appropriate binary review.
Commit only after explicit approval of that prepared change and honor the
configured client-confirmation step. Do not enable writes, broaden targets or
change approval policy to bypass a refusal. Cancel unused plans.

For requested new TXT/CSV storage files outside MD:, use `prepare_file_create`.
Creation needs its own exact `creatable_files` policy and a successful absence
check. Native LS program deployment uses its separate operation and target policy.
Another client can race a preflight.

If a write or readback fails, inspect the actual controller bytes/comments before
preparing another change. Plans are single-use; do not retry blindly or restore
automatically. Report unsupported native operations using the capability result;
do not invent a controller endpoint or substitute shell/network commands to
bypass the bridge contract.

The local bundle's setup guide covers installation and runtime diagnosis.
Use `fanuc-mcp-server-doctor --config <file>` for an offline MCP check; adding
`--probe <robot-id>` makes an explicit read-only controller connection.
