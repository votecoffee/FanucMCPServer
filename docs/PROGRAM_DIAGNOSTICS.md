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
