# Sampled request and acknowledgment analysis

`workbench.recording.handshake` analyzes saved watch samples using explicit
request and acknowledgment states. It identifies observed request edges,
fresh acknowledgments, stale baselines, gaps and open requests. It opens no
controller connection and modifies no source files. Python and MCP use the same
standard-library implementation.

## Activate and call

Stage `install-builtin recording`, then call `reload_extensions`. Version 1.2.0
adds this operation while preserving the summary and bundle operations. A host
API 1 runtime with an older installer can stage the self-contained directory:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/recording_v1_2_0
```

The 48 permanent MCP tool schemas stay unchanged. Versions 1.0.0 and 1.1.0 are
preserved under `extensions/recording` and `extensions/recording_v1_1_0`; select
one and reload to roll back. See [feature reload](ROBOT_EXTENSIONS.md).

Describe `workbench.recording.handshake`, then use `run_read_operation` with its
exact revision and these arguments:

```json
{
  "artifact_id": "<recording-artifact-id>",
  "path": "recording.json",
  "rules": [
    {
      "name": "Cycle request",
      "request_signal": "DO[1]",
      "ack_signal": "DI[1]",
      "request_active": true,
      "request_inactive": false,
      "ack_active": true,
      "ack_inactive": false,
      "timeout_seconds": 5
    }
  ],
  "offset": 0,
  "limit": 100
}
```

Use a saved recording artifact or compatible imported JSON. For a recording
inside an evidence bundle, use that bundle's artifact ID and
`recording/recording.json`. Host manifest checks protect the selected artifact;
the report hashes the exact bytes consumed. Saved derived timelines and cycle
profiles are ignored. The existing mapping-based event correlation API remains
available separately.

## Explicit states and observations

Each rule requires a unique name, distinct request/ack signal names, and an
explicit active/inactive value for each signal. States can be booleans, finite
numbers with magnitude at most 2^31−1, or strings up to 64 characters. Comparison
preserves JSON types: `true`, `1`, `1.0` and `"ON"` differ. To analyze file-provider
strings, configure their exact string states. Active and inactive states must
differ. Signals must occur in a nonempty recording; unknown signal names refuse
analysis. The optional threshold defaults to 10 seconds and must be in (0, 86400].

An error key, including one with an empty error string, makes an observation
unknown. Missing/null values and values outside the configured two states are
also unknown. Per-rule counts retain those reasons. Observed request edges
require adjacent inactive/active request observations. Initial active states and
recovery after a gap do not become new observed edges.

| Status | Observed evidence |
| --- | --- |
| `ack_observed` | Inactive ack baseline, observed request edge, and later active ack with request still active |
| `request_and_ack_coobserved` | Request and ack changes appear in the same polling interval; their order is unresolved |
| `ack_and_request_clear_coobserved` | Ack activation and request clearing appear together; their order is unresolved |
| `ack_not_inactive_at_request_baseline` | Ack was already active at the request baseline; no fresh response is assigned |
| `ack_baseline_unknown` | Ack baseline is unavailable at an observed request edge |
| `evidence_gap` | A pending request encounters missing evidence; a later ack is not attached across that gap |
| `request_cleared_before_ack_observed` | Request becomes inactive without an observed active ack |
| `open_at_recording_end` | Recording ends while a request is pending |
| `request_active_without_observed_edge` | An available segment begins with an active request and no observed inactive/active edge |

Rows retain source indices, including request/ack observation intervals and the
last index through which wait observations are available. `request_edges_observed`
counts observed edges only; boundary rows do not imply additional transactions.
An empty recording returns no events, zero counts and an unavailable clock.

## Nominal timing, pagination and limits

`observed_delay_seconds` subtracts the first active request sample timestamp from
the first active ack sample timestamp. For a later ack with request still active,
`nominal_delay_range_seconds` also accounts for both adjacent observation
intervals. With a request appearing at t=2 after t=0 and ack appearing at t=6
after t=4, the observed timestamp difference is 4 seconds and nominal range is
2–6 seconds. A 5-second threshold is therefore `threshold_ambiguous`.

The range produces `within_threshold` only when its maximum is at most the
threshold, `beyond_threshold` only when its minimum exceeds it, and otherwise
`threshold_ambiguous`. Coobserved, stale, missing and boundary events do not
receive a delay range. Duplicate or regressing timestamps disable all durations
and threshold comparisons for the recording while retaining state observations.

These are nominal ranges on the recorded timestamp grid. File values can be
stale, captures can be sequential, clocks are unverified, and polling can miss
pulses or intermediate states. They are not verified physical response-time
bounds or proof that an acknowledgment was caused by a request. Open requests
can report `observed_wait_exceeds_threshold`; that means the recorded sample
span exceeds the threshold, not a continuously observed controller timeout.
At a gap, waiting stops at the previous known sample. `physical_timing_verified`,
`continuous_monitoring_verified`, `capture_is_atomic`, `causation_verified`,
`rule_semantics_verified`, `capture_provenance_verified` and
`recording_completion_verified` remain false.

Reports include per-rule availability/status counts and global status/threshold
counts. Pagination affects only `items`; summaries cover all rules and samples.
Events sort by ending sample index, input rule order, then starting index.
Limits are 1–16 rules, 300 samples, 256 recorded signals, 8 MiB UTF-8 recording
JSON, and pages of 1–100 events. Duplicate JSON keys, nonfinite data and malformed
recording shapes are refused using the [recording summary](RECORDING_ANALYSIS.md)
input limits. No FANUC software option is needed for offline analysis.

```python
from pathlib import Path
from fanuc_workbench.services.recording_analysis import analyze_handshake

report = analyze_handshake(Path("recording.json"), rules)
```
