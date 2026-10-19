# Offline text alarm analysis

The optional `workbench.alarms` feature analyzes an explicitly selected
saved text file. It preserves unrecognized lines, groups parsed alarm codes and
converts supported timestamps under explicit clock assumptions. It opens no
controller connection and modifies no input. Local Python and MCP share the
same implementation, which needs only Python's standard library.

Version 1.1.0 also adds [two-log comparison](ALARM_COMPARISON.md), preserving the
single-log result contract. The original 1.0.0 release remains unchanged under
`extensions/alarms`; the current release is `extensions/alarms_v1_1_0`.

## Activate and call

Stage the feature with the runtime interpreter, then call `reload_extensions`
in an existing host API 1 MCP session:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install-builtin alarms
```

An older extension-capable installer can stage the self-contained directory
through its existing `install` command:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/alarms_v1_1_0
```

The catalog gains `workbench.alarms.analyze` and `workbench.alarms.compare`;
the permanent 48 MCP tools stay unchanged. Staging does not activate code,
install dependencies or contact a robot. See
[feature reload](ROBOT_EXTENSIONS.md) for activation and versioning.

Describe `workbench.alarms.analyze`, then call `run_read_operation` with its
exact revision and these arguments:

```json
{
  "artifact_id": "<saved-artifact-id>",
  "path": "ALARM.TXT",
  "date_order": "ymd",
  "utc_offset_minutes": 0,
  "source_order": "oldest_first",
  "view": "codes",
  "offset": 0,
  "limit": 100
}
```

Saved controller file/log artifacts or compatible imported workspaces work.
The filename does not select the parser: the operation reads only the explicit
artifact-relative path. The host checks its manifest, and the report hashes the
exact bytes consumed. Existing workspace alarm scanning remains available.

## Records and supported text

`view: "records"` returns nonempty source lines in file order. An alarm record
contains the first matching ASCII code on that line: 2–10 letters, a hyphen and
3–5 digits, with token boundaries. Codes normalize to uppercase. This recognizes
a text pattern, not a verified vendor alarm catalog. A line containing two codes
produces one record and retains its complete `raw_line` for inspection.

An optional parenthesized severity up to 64 characters is retained with the
message. Unsupported severity syntax stays in the message and produces an issue.
Severity meaning is not inferred. Unrecognized headers or other lines remain
`record_type: "unrecognized"`; blank lines are counted separately. Raw lines
exclude CR/LF endings. Messages and severity variants preserve text and case.

The source must be UTF-8, optionally with a BOM, or strictly decodable Windows
CP1252. Unsupported byte sequences and NUL-containing files are refused.
Limits are 16 MiB per source, 50000 lines including blanks, and 2048 UTF-8 bytes
per decoded line excluding CR/LF. The decoded byte limit also bounds escaped
JSON pages for localized or control-character text. This is a bounded text
export parser; binary alarm formats and arbitrary controller log variants are
not decoded.

## Explicit clocks

Both `date_order` (`ymd`, `mdy` or `dmy`) and `utc_offset_minutes` (integer
−840 to 840) are required. Supported timestamps end immediately before the code,
optionally after a prefix such as a sequence number. They use consistent `/` or
`-` date separators, a four-digit year, and a time with minutes, optional seconds
and optional 1–6 fractional-second digits. A space or `T` separates date/time.

Unzoned timestamps use the supplied offset. An inline `Z` or `±HH:MM` takes
precedence and is identified as `offset_source: "inline"`; disagreements with the
supplied offset are reported. Offsets are fixed numeric values. No local-machine
timezone, named-zone rules, DST correction or clock synchronization is inferred.

Each alarm record reports `valid`, `missing`, `unsupported` or `invalid` timestamp
status. Unsupported/invalid dates keep the alarm and raw text but produce no
instant. Ambiguous dates follow only the supplied order; two-digit years are not
expanded. Valid timestamps expose UTC ISO text, recorded precision and exact
`utc_epoch_microseconds` as decimal strings, including times before the epoch.
The converted values remain conditional on the supplied and recorded clock data.

## Recurrence and pagination

`view: "codes"` groups parsed records by code, sorted by descending count then
code. It reports source line span, message/severity variant counts, most common
message/severity, and available/unavailable timestamp counts. Ties for most
common text use first appearance. `first_valid_timestamp_utc` and
`last_valid_timestamp_utc` refer to file order, not a repaired chronological sort.

Intervals compare adjacent parsed occurrences of the same code. A missing,
invalid or unsupported timestamp breaks that adjacency. `source_order` defaults
to `oldest_first`; choose `newest_first` explicitly for descending logs. Records
are not reordered to conceal an order conflict. Any interval opposite the
declared order disables minimum/maximum intervals for that code. Repeated
timestamps are counted and can produce an interval of zero.

Interval minima/maxima are exact decimal microsecond strings over available
adjacent comparisons. `incomplete_intervals` counts comparisons lost to unavailable
timestamps; `interval_coverage_complete` describes coverage of parsed records.
Partial comparisons do not establish complete alarm history. Duplicate log
entries are not deduplicated or authenticated as separate physical occurrences.

The optional `code` filter is one exact supported code, case-insensitive.
`include_unrecognized: false` hides non-alarm rows in record view; code view has
no unrecognized groups. Pagination affects `items`, `total` and `next_offset`;
file totals, distinct-code counts and timestamp/parse-issue summaries always
cover the full file. `offset` is nonnegative and `limit` is 1–100.

`timing_verified`, `alarm_occurrences_verified`, `code_catalog_verified`,
`capture_provenance_verified`, `log_completeness_verified` and clock
`assumptions_verified` remain false. This feature does not infer an active alarm,
fault cause, alarm-clear state, or signal correlation. No FANUC software option
is needed to analyze a saved file; collecting it uses the selected controller
file interface. The self-contained release includes the project's MIT license.

```python
from pathlib import Path
from fanuc_workbench.services.alarm_analysis import analyze_alarms

report = analyze_alarms(Path("ALARM.TXT"), date_order="ymd", utc_offset_minutes=0,
                        source_order="oldest_first", view="codes")
```
