# Offline watch recording analysis

The optional `workbench.recording` 1.0.0 feature summarizes a saved watch's
`recording.json`. It reads preserved samples, opens no controller connection and
does not modify the input. Local Python and the reloadable MCP operation use
the same implementation.

## Activate

Stage the bundled feature with the runtime's interpreter, then call
`reload_extensions` in the existing MCP session:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install-builtin recording
```

An older host that already supports host API 1 can stage the self-contained
release through its existing installer:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/recording
```

The operation catalog gains `workbench.recording.summarize`; the permanent 48
MCP tools remain unchanged. See [extension staging and reload](ROBOT_EXTENSIONS.md).
Staging or reloading the feature does not install dependencies or contact a robot.

## Use

Describe `workbench.recording.summarize`, then call `run_read_operation` with
that exact revision and these arguments:

```json
{
  "artifact_id": "<recording-artifact-id>",
  "path": "recording.json",
  "gap_threshold_seconds": 5,
  "offset": 0,
  "limit": 100
}
```

Use the artifact ID returned by a completed or partially retained watch job.
Compatible JSON imported into a workspace also works. The host verifies the
artifact manifest and selected file before dispatch. The report records the
SHA-256 and length of the exact bytes consumed. An optional `signal` argument
selects an exact, case-sensitive signal name; an unknown name returns no rows.
The empty filter selects all signals.

Each signal reports available/unavailable sample counts, availability fraction,
available segments, longest unavailable run, first/last available values and
indices, numeric sample count and numeric minimum/maximum. Signals are the union
of keys in sample `values` and `errors`, sorted by name. A missing value, a null
value or any explicit error makes that observation unavailable. Errors take
precedence even when a value is present; the first reported error is retained.

`adjacent_changes` compares consecutive available samples. `changed_after_gap`
counts differing endpoints separated by unavailable observations. It does not
locate a transition within a gap, establish its count or reconstruct missed
states. Recovery with the same value does not prove the signal stayed constant.
Comparison preserves JSON types, so `true`, `1` and `1.0` are distinct; object
key order does not affect equality. Stored `events`, `cycle` and
`profile_seconds` are ignored and never treated as independent evidence.

Only JSON numbers contribute to numeric ranges. Booleans, numeric-looking
strings and structured PR values are retained as their original types. Integers
above the exact IEEE-double range, including nested values, are returned as
decimal strings; `large_integer_encoding` states the rule. Units, scaling and
controller mappings are not inferred.

Pagination applies to signal rows only. `total`, `next_offset` and `offset`
describe the filtered rows; `signals_total`, `availability` and timestamps always
summarize the whole recording. A filter does not change the global totals.

## Timing and sample references

Timestamps are recorded host evidence. The report counts adjacent equal
timestamps and regressions. Duration and interval minimum/maximum/median are
available only for nonempty, nondecreasing timestamps. Empty recordings and
clock regressions return null timing summaries; a single sample has duration
zero and no interval statistics. An optional positive `gap_threshold_seconds`
counts intervals strictly above that threshold. It is a caller-supplied review
threshold, not a verified sample period or network diagnosis.

The recording contains `sample_artifacts` IDs, not their contents. The report
retains those IDs, their count, unique count and whether their count matches the
samples. It does not resolve the IDs, verify their ordering or read referenced
files. A matching count does not prove provenance or recording completion.
`contents_verified`, `wire_evidence_checked`, `capture_provenance_verified` and
`recording_completion_verified` remain false. For a retained SNPX sample,
explicitly inspect its own `sample.json` and `wire.json` using the separate
[wire evidence feature](SNPX_EVIDENCE.md).

## Limits and Python

Input is UTF-8 JSON, optionally with a BOM, up to 8 MiB, 300 samples and 256
distinct signals. Duplicate JSON keys, nonfinite values, malformed sample shapes
and invalid artifact IDs are refused. Signal names are 1–128 characters; errors
and strings are at most 1024 characters. Values are bounded to 4096 ASCII JSON
bytes, four container nesting levels, 64 elements per container and integers
with magnitude below 2^64. Timestamps must be finite numbers with magnitude at
most 10^12 seconds. `offset` is nonnegative and `limit` is 1–100. A timing gap
threshold must be finite and in (0, 86400] seconds.

The self-contained release requires only Python's standard library and includes
the project's MIT license. No FANUC software option is needed for this offline
analysis. Collecting a watch still requires its chosen provider's controller
interfaces and options.

```python
from pathlib import Path
from fanuc_workbench.services.recording_analysis import summarize_recording

report = summarize_recording(Path("recording.json"), gap_threshold_seconds=5)
```
