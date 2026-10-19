# Offline program diagnostics

Shared services retain source hashes, physical file lines and TP line numbers.
They read explicitly selected preserved files and do not execute LS or contact a
controller. The original call-graph release remains preserved.

## Logic and access tracing

```python
from pathlib import Path
from fanuc_workbench.services.program_analysis import trace_logic

report = trace_logic(Path("backup"), ["MAIN.LS"], view="accesses")
```

Views are `instructions`, `accesses`, `signals`, `edges` and `issues`, with
`offset` and `limit` (1–100). IF/THEN/ELSE/ENDIF, SELECT cases, JMP/LBL, WAIT,
assignments, CALL/RUN and motion statements have explicit instruction categories.
Only a recognized assignment target is a writer; conditions, RHS operands and
indirect indexes are readers. Dynamic references remain `unresolved_indirect`.
Control-flow edges describe possible paths, including unresolved labels and WAIT
self-loops; they do not establish complete control flow or execution reachability.
Unsupported/malformed statements remain issues instead of inferred instructions.
Listings retain the original dependency reader's byte, path and line limits,
plus at most 10,000 instruction nodes.

## Why a task may be waiting

`explain_wait(root, path, observation, max_age_seconds=5)` matches one observed
task/program/TP line and exact `program_sha256` to the listing. It evaluates only
supported WAIT comparisons using fresh sampled scalar R/I/O/F values, ON/OFF,
numeric constants, parentheses, AND/OR/NOT and comparison operators. Evaluation
has three outcomes: true, false or unavailable. Indirection, arithmetic, timed
waits and unsupported grammar remain unresolved. The report retains individual
comparison operands and unsatisfied terms, plus separately supplied alarms.
It identifies a sampled unsatisfied condition without asserting a verified
blocking cause or simultaneous acquisition.

An observation is explicit source-bound data:

```json
{
  "timestamp": 100.0,
  "source_id": "test-controller",
  "task": {
    "name": "T1", "program": "MAIN", "line": 1, "state": "WAITING",
    "program_sha256": "<exact lowercase SHA-256 of captured MAIN.LS bytes>"
  },
  "sample": {"timestamp": 100.0, "values": {"DI[1]": false}, "errors": {}},
  "alarms": [],
  "alarms_complete": true
}
```

The supplied hash is a claimed revision binding, not independent proof of the
controller's current program. The caller must capture/bind the actual revision;
choosing a local file never supplies that evidence automatically. Alias values
must first be projected onto exact canonical reference names. Task state and
active alarms must be established by their provider; alarm history is not an
active-alarm list. Missing or incomplete alarm reads cannot establish a new alarm.

## Triggered observations

`TriggeredRecorder(before=20, after=20, signals=["DI[1]"])` accepts observations
through `add`. It detects newly observed active alarms, RUNNING→PAUSED task
transitions, same-task program/revision changes and selected valid signal changes.
Initial states and signal changes spanning unavailable samples do not trigger.
The caller owns acquisition; constructing a recorder starts no polling.

`document()` retains timestamps, the trigger row, available pre-history and
remaining post-count. `triggered_captures(root, paths, observation_path, ...)`
applies the same recorder to preserved observations and matches exact LS revisions.
Its `captures` view pages summaries; `observations` pages rows of an explicit
`capture_index`. Unmatched program revisions and unfinished post-windows remain
explicit. It reads version 1 `fanuc-diagnostic-observations` JSON containing an
`observations` array. Sources must match and timestamps must not move backwards.

Bounds apply together: 8 MiB JSON, 2000 observations, 16 KiB ASCII-serialized data
per observation, 100 pre/post samples each, 20 captures, 100 trigger signals,
100 explicit active alarm strings per row, nesting 24 and 200,000 JSON separators/
containers/text tokens. Missing observations do not establish continuous sampling,
physical timing, causal relationships or controller identity.

## Semantic comparison

`semantic_compare(root, before_path, after_path)` pages categorized changes to
instructions, positions, speeds, terminations, offsets, frames/tools, comments,
attributes and generated metadata. TP line renumbering, whitespace and equivalent
numeric spelling are normalized. String literals remain instruction data.
Position blocks retain configuration/axes; UF/UT changes are reported separately.
Unsupported position sections remain unclassified evidence. Comparison is limited
to 2000 instructions per input and does not establish runtime equivalence.

## Dependency review and packages

`dependency_review(root, paths, entry_programs=["MAIN"], inventory_path=...)`
follows selected direct subprograms and lists program, R/PR/SR/AR, position,
frame/tool and I/O requirements. Dynamic calls/indexes, unresolved jumps, missing
position definitions and unsupported syntax prevent a complete scoped review.
Motion also requires explicit current UFRAME_NUM/UTOOL_NUM state evidence; a
static frame assignment alone does not establish the active runtime context.

The explicit inventory uses this shape, with nonempty evidence objects:

```json
{
  "format": "fanuc-dependency-inventory", "schema_version": 1,
  "resources": {"R[1]": {"source": "NUMREG.VA", "index": 1}},
  "io_mappings": {"DI[1]": {"rack": 0, "slot": 1, "start": 1}}
}
```

Inventory entries are supplied evidence, not authenticated controller
configuration. `view` is `requirements`, `missing` or `issues`. A complete
explicit selection does not establish physical compatibility or dependency
completeness across unsupported LS features.

`build_change_package` in `services.program_packages` copies exact reachable
program bytes and inventory into a new destination, verifies hashes and writes
`package.json`. Existing destinations are refused. Missing/unresolved requirements
block creation unless `allow_incomplete=True` is explicitly chosen; that package
is labeled `incomplete_review_only`. Packages never authorize deployment and still
require native validation and the existing fresh controller preflight/review.

## Native offline validation

`validate_offline` in `services.native_validation` runs explicit local MakeTP and
PrintTP executables with an explicit `robot.ini`, robot-context directory and
WinOLPC core version. It preserves original inputs, copies the robot context and
rebinds Robot/Support/Output paths into a new isolated job directory. Version and
configuration must agree. It does not run SetRobot, change installed tools or
load/select/run a controller program.

The job preserves source, original/effective INIs, context hashes, compiler and
printer hashes, merged stdout/stderr logs, diagnostics, TP output and printed LS
readback when available. Both tool stages must exit successfully with nonempty
outputs; source/readback rows and tokens must match after generated metadata is
excluded. Readback differences and failures retain their evidence. Exceptions
after compiler invocation retain a failed validation report. A copied context is
sequential evidence, not an atomic capture or exact core-build authentication.

Limits: 1 MiB LS/config inputs, 4 MiB TP, 10,000 context files/directories each,
512 MiB copied context, 1 MiB merged tool output per stage and 1–60 seconds per
process (default 30). A successful offline report remains separate from controller
LS/TP readback, native deployment preflight and physical/runtime acceptance.
`review_validation` checks explicitly selected preserved source/report/TP/readback
files and their hashes; it never executes a command from a report or authenticates
the reported compiler execution.

## CLI

Run `python -B -m fanuc_workbench.program_tools --help` for all commands. Examples:

```powershell
python -B -m fanuc_workbench.program_tools --workspace C:/RobotBackups/Test trace MAIN.LS --view accesses
python -B -m fanuc_workbench.program_tools --workspace C:/RobotBackups/Test wait MAIN.LS observations.json
python -B -m fanuc_workbench.program_tools --workspace C:/RobotBackups/Test capture MAIN.LS --observations observations.json --signals DI[1]
python -B -m fanuc_workbench.program_tools --workspace C:/RobotBackups/Test compare before.LS after.LS
python -B -m fanuc_workbench.program_tools --workspace C:/RobotBackups/Test dependencies MAIN.LS CHILD.LS --entry MAIN --inventory inventory.json --view missing
python -B -m fanuc_workbench.program_tools compile C:/RobotBackups/Test/MAIN.LS --destination C:/RobotChecks/Run01 --maketp C:/FanucTools/maketp.exe --printtp C:/FanucTools/printtp.exe --configuration C:/RobotChecks/robot.ini --robot-context C:/RobotChecks/Context --version V8.33-1
```

Keep actual configurations, robot data, output jobs and acceptance reports outside
the public checkout or in its ignored `.local/` directory.
