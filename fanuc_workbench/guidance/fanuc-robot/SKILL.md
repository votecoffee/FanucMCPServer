---
name: fanuc-robot
description: "Inspect FANUC robot files and diagnostic evidence, manage FanucMCPServer backups and local workspaces, and prepare reviewed application-file or description changes using the local FanucMCPServer MCP server. Use for this robot integration; not for general robotics questions or unsupported motion commands."
---

# FANUC robot work

Check `capabilities` for the selected profile first. For
`standalone-local-native`, follow [local native validation](references/local-native.md)
for pinned MakeTP/PrintTP preparation, one-time execution and receipt inspection.
That profile has no controller or GUI routes. `standalone-offline` continues to
exclude native utilities. Profile and guide content do not grant authorization
to change connections, broaden policy or execute a pending native pilot.

Use the selected FanucMCPServer connection. Call named tools on the standalone route; on the app-owned host, discover and describe them before invocation. Do not switch routes during retained work. Keep the same connection through
prepare/review/commit and job completion. Closing the server loses its in-memory
plans. After a lost write response, inspect retained app evidence and current
controller state before preparing another change; never replay automatically.

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

For native register/program work, read [native changes](references/native-changes.md).

For troubleshooting, retrieve relevant files/logs or start a bounded backup.
Follow job status, inspect capture completeness and unavailable-file errors, and
use artifact IDs for analysis. Retain source hashes and distinguish captured
evidence from live state. A successful transfer or option-text match does not
prove native load, installed options, running state or physical behavior.

For local edits, use separate workspace/source copies. VA/DG listings are
documentation and inspection evidence; editing them is not a live value/frame
write. Checklists and PLC/handshake/project metadata are local, user-authored
evidence. Checklists do not establish commissioning acceptance.

When captured files must pass through chat, read [exact file bytes](references/file-bytes.md).

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
