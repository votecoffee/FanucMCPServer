# Local native program validation

The optional direct `--local-native-policy` profile restores local MakeTP/PrintTP
compilation separately from controller connections and ROBOGUIDE GUI ownership.
It extends the 15 saved-evidence tools with five local tools. It requires the
ordinary bridge configuration to contain zero robots and `allow_writes: false`.
Its presence in source does not establish native compiler or installed acceptance;
keep an accepted offline installation unchanged until a candidate pilot passes.

## Pin the existing toolchain

Select explicit installed MakeTP/PrintTP executables, a version-compatible
`robot.ini`, an offline robot context with its `support` directory, and the
explicit WinOLPC core version. The configuration must contain exactly the
`WinOLPC_Util` section with `Robot`, `Version`, `Path`, `Support` and `Output`.
Select the supporting files explicitly from the chosen vendor distribution or
wrapper configuration and record the source of that selection. The application
does not infer a complete dependency set from fixed DLL names or PE imports.
Vendor utilities can resolve components through COM, configuration, registry or
dynamic loading. Installation, dependency completeness and native compatibility
must be verified separately; no vendor files are redistributed by this project.

Capture their exact paths and hashes without executing a utility:

```powershell
python -B -m fanuc_workbench.program_tools toolchain `
  --maketp C:/RobotTools/bin/MakeTP.exe `
  --printtp C:/RobotTools/bin/PrintTP.exe `
  --configuration C:/RobotTools/robot.ini `
  --robot-context C:/RobotTools/disposable-context `
  --component C:/RobotTools/selected-core/supporting-library.dll `
  --component-evidence "Supporting files selected from the vendor distribution manifest" `
  --version V9.40-1 --destination C:/RobotTools/toolchain.json
```

This refuses an existing destination. It hashes both executables, the original
configuration, each explicitly selected supporting file and every context file.
Repeat `--component` for all reviewed supporting files (1–128 files).
The example filename is a placeholder, not a claimed FANUC dependency.
The capture always records `dependency_completeness_verified: false`. It
is not an atomic filesystem snapshot. Preparation and execution recheck the exact
manifest and source bytes rather than treating that capture as permanent proof.

Create a private policy containing the exact SHA-256 returned by the command:

```json
{
  "schema_version": 1,
  "toolchains": {
    "selected-core": {
      "manifest": "toolchain.json",
      "sha256": "<exact SHA-256 from the capture command>"
    }
  }
}
```

The policy allows 1–8 fixed selections and a 4 MiB toolchain-manifest limit. It does not accept arbitrary command,
argument, environment, robot or GUI settings. Paths in the toolchain manifest
are explicit canonical local paths. Context limits also obey the bridge's file,
file-count and aggregate artifact limits. A changed policy requires an explicit
reconnect; chat tools cannot broaden it.

## Select the candidate profile

Use the candidate environment's absolute Python executable and this argument
sequence in a separate candidate registration:

```text
-I -B -m fanuc_workbench.bridge.mcp_server --config C:/RobotTools/zero-robot.json --local-native-policy C:/RobotTools/native-tools.json
```

`--offline` and `--local-native-policy` are mutually exclusive. The original
15-tool offline profile continues to exclude native execution. Both restricted
profiles refuse protocol preview, controller tools, extension execution/reload,
shared-host routing, native controller APIs and GUI launch. An incompatible
robot/write policy refuses before state-directory service construction.

## Prepare, review and execute once

1. Import the intended saved LS workspace and obtain its exact source SHA-256.
2. Call `list_native_toolchains` and select an administrator-pinned ID.
3. Call `prepare_native_validation` with the artifact ID, relative LS path,
   selected toolchain ID and exact source hash. This performs no native execution.
4. Review the source, compiler/printer/configuration/profile/context hashes, core
   version, timeout and generated `plan_sha256`.
5. Call `run_native_validation` with that exact plan ID and plan hash. Inputs are
   checked again, copied inputs are compared with the reviewed manifest before any
   compiler starts, and the single-use plan is consumed before dispatch. Source and
   context copies, compiler diagnostics, TP output and PrintTP listing comparison
   are retained as a new local artifact. Originals are checked for preservation.
6. Inspect `native_validation_status` after a lost response. It never retries,
   restarts or adopts a process. A retained dispatch without a current owner is
   reported as an unverified outcome. Use `discard_native_validation` only for an
   unused current review plan; it preserves the files and does not kill an active
   utility or reconcile uncertainty.

There are at most 16 current review plans and one active validation pipeline.
A busy second pipeline leaves its plan prepared. Utility jobs also have
cross-process admission and refuse a retained nonterminal resource instead of
replaying it. Cancellation or EOF does not authorize retrying lost native work.

## Ownership and pilot boundary

The actual calling MCP process owns each utility job through the shared durable
resource API. An atomic suspended launch attaches the background worker and its
Python/console descendants to a kill-on-owner-exit Windows job before execution.
The parent retains the actual bootstrap identity and verifies exact membership
and executable hashes before activation. Compiler creation intent is durable,
and its OS identity comes from the retained process handle. Bounded file IPC
does not inherit MCP streams or the owner's job handle. An inert result reporter
stays alive until identity-bound group cleanup, avoiding an exit/query race.

Each utility job permits at most eight processes and 512 MiB of private commit,
with a 1–60 second compiler timeout and 1 MiB output/log bound. The foreground MCP
client owns its ordinary SDK launch separately. A pilot must inventory and bound
the combined foreground/utility trees; the utility limit alone is not an
aggregate foreground-memory guarantee. Cleanup acts on the exact private job,
preserves outputs and uncertainty, and never targets a shared/user application.

The smallest real pilot compiles one disposable comment-only LS program and
compares its PrintTP listing using one explicitly selected compatible context.
Acceptance must include exact installed package/tool hashes, actual diagnostics,
EOF/cancellation, timeout, worker/compiler crash and caller-death cleanup,
including descendants. Pure or Python-only fixtures do not prove vendor tool
behavior. No controller load, program selection/run, GUI/workcell action or
deployment permission follows from a successful local compile.

ROBOGUIDE backup handoff remains available separately. Automatic application
launch still needs explicit native ownership and dirty-workcell/save/refusal
acceptance; existing user-opened sessions are never adopted or closed.
