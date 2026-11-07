# Windows workbench CLI

`RunMiddleware.cmd catalog` lists installed operations. Use
`RunMiddleware.cmd describe <operation>` for the argument schema and revision.
Write the arguments alone as a UTF-8 JSON object to an explicit local file.
Run the operation with `run <operation> --request <absolute path>`. Optionally
pin the described revision with `--revision`. Successful requests emit
`{"ok":true,"result":...}`. Failures return JSON with `ok:false` and a nonzero
exit status. Help and catalog discovery load no vendor software or target.

## Operations

| Area | Catalog routes |
| --- | --- |
| Saved backups, files and logs | `workspace.import`, `workspace.analyze`, `workspace.compare`, `workspace.edit`, `artifact.*`, `workbench.analysis.*`, `workbench.alarms.*` |
| Programs and reviewed local output | `program.inspect`, `program.change_package`, `workbench.programs.*` |
| Diagnostics, I/O mapping and recordings | `snapshot.*`, `plc.tags`, `workspace.metadata_*`, `recording.*`, `workbench.recording.*`, `workbench.snpx.*`, `workbench.vqlog.*`, `pose.transform` |
| Documentation, project, baseline and ROBOGUIDE handoff | `workspace.project`, `artifact.package` with `kind` of `documentation`, `baseline`, `deployment` or `roboguide` |
| Local native validation | `compile.capture_toolchain`, `compile.toolchains`, `compile.prepare`, `compile.run`, `workbench.programs.review_validation` |
| Controller reads and captures | `robots.*`, `files.list`, `files.read`, `logs.collect`, `backup.*`, `monitor.*`, `core.controller.*`, `core.registers.read`, `core.programs.inspect`, `core.snpx.read_words` |
| Reviewed controller changes | `files.stage`, `core.files.*`, `core.comments.update`, `core.registers.update`, `core.programs.deploy`, `change.status`, `change.cancel`, `change.commit` |
| Existing operator-registered engineering extensions | `feature.list`, `feature.run` at an exact revision |

The latest bundled revision of each of the six engineering extensions is
available directly. An already registered operator-trusted release can be
invoked by exact revision through `feature.run`. The CLI closes any owned
workers when the request ends. Host registration, reload and reconnect are
session administration. Each controller call gets a new one-shot session.

## Saved inputs and external targets

Use the app's default offline profile or select an operator-owned profile
with `--config`. See the [example profile](../examples/robot_bridge.example.json)
for controller access policy. `workspace.import` copies a selected folder
under `source_dir` into immutable evidence. For example, with `review` under
that root, submit `{"source_folder":"review"}` and keep the artifact ID.
Use `artifact.files` to obtain exact paths and hashes.

Controller reads require an explicit configured target and the applicable
FTP/SFTP, optional HTTP, FRRobot or SNPX provider. Credentials follow the
operator's existing policy and environment. Backup and monitoring jobs
finish inside the owning request. Their deadlines request cooperative
cancellation and allow an in-flight transfer to close. `job.status` reads
retained progress and artifacts. `job.cancel` applies to CLI-owned read jobs.
Monitor diagnostics bind sequential samples and captured LS revisions.

With Workbench CLI 1.0.1, `core.controller.sample` also accepts
`UFRAME[group,index]` and `UTOOL[group,index]` on the FRRobot provider.
For example, send `{"robot_id":"example","expressions":["UFRAME[1,3]","UTOOL[1,2]"]}`
with a configured robot ID. The result includes Cartesian XYZ/WPR values in
millimetres and degrees, keyed by expression. The provider reads the specified
table entries without selecting them. An unavailable entry is reported under
`errors`.

MakeTP/PrintTP requires a compatible installed toolchain and a pinned
`--native-policy` under an offline profile. `compile.prepare` returns a
reviewed plan and `compile.run` takes both review and compiler-plan hashes.
The ROBOGUIDE handoff generates files and instructions for an operator to
restore in a test workcell. Opening the workcell and running the simulation
are manual.

## Controller changes

Read the exact target and keep its preimage hash. Native R/PR and program
prepares also take the `reference_path` from the read's `local_path`.
Prepare the change, then inspect the returned target, preimage, proposed
bytes or value, `plan_id`, `proposed_sha256` and `review_sha256`. Use
`change.status` to retrieve the review after exit. Commit only after the
operator approves that exact proposal. The approval request is
`{"plan_id":"…","approved_sha256":"…","review_sha256":"…"}`. A profile
using elicitation requires the operator to enter the exact confirmation
token in a Windows terminal.

The app binds durable plans to the profile bytes and reopens the target
under its configured policy. It consumes the plan before controller
preflight, then uses the existing write and readback implementation.
Interruption or ambiguous readback retains evidence and blocks further
CLI writes to the affected host in that state directory. A retained
native-worker outcome likewise requires app-owned inspection before reuse.
Use the same state directory for access to the same controller. Inspect
the receipt and controller state. An operator can record the inspection and
release the matching controller hold with interactive `change.reconcile`.
If native worker exit is unresolved, ownership must first be reconciled
with its retained process evidence. Program load verifies compiled and listing
readback. Motion, program execution, I/O forcing, and safety-system
changes are outside this interface.
