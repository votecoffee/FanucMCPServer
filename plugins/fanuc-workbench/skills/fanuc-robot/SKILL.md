---
name: fanuc-robot
description: "Inspect FANUC robot files and diagnostic evidence, manage Workbench backups and local workspaces, and prepare reviewed application-file or description changes using the local FANUC Workbench MCP server. Use for this robot integration; not for general robotics questions or unsupported motion commands."
---

# FANUC robot work

Use the FANUC Workbench tools for configured robots and preserved workspace
artifacts. Begin a new robot workflow by inspecting `list_robots` and
`capabilities`; use `probe_robot` only for the user's intended target. An empty
robot list needs local configuration before network work. Never guess addresses
or put passwords into tool arguments or the conversation.

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
check; program-file creation is unsupported. Another client can race a preflight.

If a write or readback fails, inspect the actual controller bytes/comments before
preparing another change. Plans are single-use; do not retry blindly or restore
automatically. Report unsupported native operations using the capability result;
do not invent a controller endpoint or substitute shell/network commands to
bypass the bridge contract.

The local bundle's setup guide covers installation and runtime diagnosis.
Use `fanuc-workbench-doctor --config <file>` for an offline MCP check; adding
`--probe <robot-id>` makes an explicit read-only controller connection.
