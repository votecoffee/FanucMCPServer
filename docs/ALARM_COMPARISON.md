# Saved alarm log comparison

`workbench.alarms.compare` compares code counts and complete severity/message
pair distributions in two explicitly selected text files. It reports differences
in parsed records; a count delta does not identify new physical alarm occurrences,
an alarm being cleared, or a change in failure rate. It opens no controller
connection and preserves both inputs. Python and MCP share the standard-library
implementation.

## Activate and call

Stage `install-builtin alarms`, then call `reload_extensions` in a compatible
host API 1 session. Version 1.1.0 preserves `workbench.alarms.analyze` and adds the
comparison operation. The 48 permanent MCP tool schemas stay unchanged. An older
extension-capable installer can stage the self-contained release directory:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/alarms_v1_1_0
```

Version 1.0.0 remains unchanged in `extensions/alarms`. Select that release and
reload to roll back; the comparison operation becomes unavailable. Described
operation revisions change with the selected release, so describe them again
after an upgrade or rollback. See [feature reload](ROBOT_EXTENSIONS.md).

Describe `workbench.alarms.compare`, then call `run_read_operation` with its exact
revision and these arguments:

```json
{
  "baseline_artifact_id": "<baseline-artifact-id>",
  "baseline_path": "ALARM.TXT",
  "candidate_artifact_id": "<candidate-artifact-id>",
  "candidate_path": "ALARM.TXT",
  "baseline_clock": {
    "date_order": "ymd",
    "utc_offset_minutes": 0,
    "source_order": "oldest_first"
  },
  "candidate_clock": {
    "date_order": "ymd",
    "utc_offset_minutes": 0,
    "source_order": "newest_first"
  },
  "changes_only": true,
  "offset": 0,
  "limit": 100
}
```

Both paths are relative to their selected saved artifacts. They can refer to
different files within one artifact or files in separate artifacts. The host
checks both artifact manifests. The report separately hashes the exact bytes
consumed, retaining each file's full parse, encoding and timestamp summaries.
Artifact selection does not authenticate a shared controller or comparable
capture windows.

Each clock requires an explicit `date_order` and integer `utc_offset_minutes`;
`source_order` defaults to `oldest_first`. Different exports can use different
clock settings. Supported text grammar, timestamp errors, encoding, limits and
recurrence rules are the same as [single-log analysis](ALARM_ANALYSIS.md).

## Comparison scope

Codes normalize to uppercase. For every code in the union of both files, the
operation counts each parsed `(severity, message)` pair, preserving text and case.
It compares the full distributions, including duplicate counts and association
between severity and message. Looking only at a modal message would miss changes
to less common records or swapped severity/message associations.

| Status | Saved-record comparison |
| --- | --- |
| `only_in_baseline` | Code has parsed records only in the baseline |
| `only_in_candidate` | Code has parsed records only in the candidate |
| `changed` | Code is in both files and its pair distribution differs |
| `unchanged` | Code is in both files and its pair distribution is equal |

`count_delta` is candidate count minus baseline count. A distribution can change
while the total count, message set, severity set and most common message all stay
the same. `record_text_distribution_changed` compares pair counts;
`message_set_changed` and `severity_set_changed` compare unique parsed texts.
Per-side distribution SHA-256 values hash canonical UTF-8 JSON arrays of
`[severity, message, count]`, sorted by missing severity first, then severity and
message, using compact separators. An absent code uses the empty array. These
hashes describe parsed distributions; they do not authenticate alarm occurrence
identity. Source hashes separately identify complete input bytes.

Timestamps, line order, blank lines, unrecognized text and raw formatting are
excluded from matching. Therefore `unchanged` does not mean byte-identical files
or equal timing evidence. `source_bytes_identical` reports source hash equality
separately. The per-code `baseline` and `candidate` sections retain their original
recurrence summaries, timestamp coverage and file-order diagnostics; an absent
code has a null section. Each file's clock assumptions remain explicit.

Rows sort alphabetically by normalized code. `changes_only` defaults to true and
hides `unchanged` rows; an optional `code` filter is exact and case-insensitive.
`offset` is nonnegative and `limit` is 1–100. Filters and pagination affect
`items`, `total` and `next_offset`; `status_counts`, `codes_compared`,
`parsed_record_count_delta` and per-file summaries always describe both full files.

## Evidence limits

Duplicate records are counted and never deduplicated into physical events.
Timestamp/order changes alone do not make a code `changed`. Log rotation,
different capture durations, copied records and unsupported text can affect
counts. No occurrence matching, causal attribution, time alignment, rate
normalization or clock synchronization is performed. Inspect unrecognized lines
through single-log record view before drawing conclusions.

`physical_occurrence_identity_verified`, `same_controller_verified`,
`capture_windows_comparable_verified`, `timing_verified`,
`alarm_occurrences_verified`, `code_catalog_verified`,
`capture_provenance_verified` and `log_completeness_verified` remain false.
Both files independently retain the 16 MiB, 50000-line and 2048-decoded-UTF-8-byte
line bounds. Pages expose at most 100 code rows, including both summaries.
The self-contained release includes the project's MIT license.

```python
from pathlib import Path
from fanuc_workbench.services.alarm_analysis import compare_alarms

report = compare_alarms(
    Path("baseline/ALARM.TXT"), Path("candidate/ALARM.TXT"),
    baseline_clock={"date_order": "ymd", "utc_offset_minutes": 0},
    candidate_clock={"date_order": "ymd", "utc_offset_minutes": 0},
)
```
