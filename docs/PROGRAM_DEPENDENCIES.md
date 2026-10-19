# Selected LS program dependencies

The optional `workbench.programs` 1.0.0 feature inspects selected saved LS files
for call/run references and static reachability from explicitly supplied entry
programs. It retains dynamic targets, missing selected definitions, ambiguity and
unsupported syntax as evidence. Python and MCP share a self-contained
standard-library implementation. The operation opens no controller connection
and modifies no files.

## Activate and call

Stage `install-builtin programs`, then call `reload_extensions` in a compatible
host API 1 session. The catalog gains `workbench.programs.dependencies`; the 48
permanent MCP tool schemas stay unchanged. An older extension-capable installer
can stage the release directory through its existing command:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/programs
```

Describe `workbench.programs.dependencies`, then call `run_read_operation` with
its exact revision and these arguments:

```json
{
  "artifact_id": "<saved-artifact-id>",
  "paths": ["MAIN.LS", "subprograms/CHILD.LS"],
  "entry_programs": ["MAIN"],
  "view": "references",
  "offset": 0,
  "limit": 100
}
```

Only the explicit paths are parsed. They are relative to the selected artifact,
must end in `.LS` ignoring case, and must be distinct ignoring case. Traversal,
absolute paths, redirects, reserved Windows device names and non-normalized
paths are refused. Before dispatch, the host verifies the full artifact's file
set and hashes; per-file report hashes describe exact parsed bytes.
`selection_sha256` hashes a compact JSON array
of `{path, source_bytes, source_sha256}` records sorted by path, using sorted
object keys and ASCII escapes. Caller path order does not change that digest.

## Supported listing subset

Definitions use a single `/PROG` header containing an ASCII name of 1–64 letters,
digits or underscores, followed by one `/MN` and one `/END` in that order. Names
normalize to uppercase. This is the inspector's bounded text grammar; it does
not establish controller naming rules or compiler validity. Filenames never
substitute for a missing or unsupported `/PROG` name.

Only physical lines within the first `/MN` body are inspected. A slash-prefixed
section ends that body. `/POS` and `/END` are recognized boundaries; another
boundary excludes the definition and records an issue. Attribute and position
sections do not contribute references. Supported statements have a decimal
1–6 digit TP line number, a colon, and one final semicolon. Source file line and
TP line number are reported separately. Blank and comment-only lines are ignored.

Quoted text, bracket descriptions and nested argument content are masked before
reference detection. An exclamation mark outside quotes/brackets/parentheses
starts a comment. Malformed delimiters or quotes, missing statement terminators
and multiple statements on one physical line produce issues and no partial edge.

Supported direct forms are `CALL NAME`, `RUN NAME`, and a single-line
`IF condition,CALL NAME` or `IF condition,RUN NAME`, with optional balanced
parenthesized arguments. Arguments and conditions are not semantically checked.
`CALL SR[...]`/`RUN SR[...]` are retained as dynamic references. Other target
forms and call/run tokens in unsupported command contexts remain unresolved;
they do not become guessed direct targets. This feature does not decode TP/PC
binaries or implement the complete LS language.

## Views and graph resolution

`view` selects `programs` (default), `references` or `issues`. Rows preserve
source-relative paths and reference/issue source text. Program rows include
source SHA-256, encoding, bytes, definition status, reference counts, incoming
resolved call-site count and outgoing distinct resolved-target count.

| Reference resolution | Meaning within the selected files |
| --- | --- |
| `resolved` | One matching indexed definition |
| `not_in_selection` | No matching named definition; the controller inventory remains unknown |
| `ambiguous` | Multiple files declare the target name |
| `excluded_definition` | One matching name has unsupported program sections |
| `unresolved_target` | Dynamic or unsupported target/context; no target name is inferred |

Malformed named duplicates still make a target ambiguous. References from an
excluded or ambiguous source definition stay visible but do not enter the graph.
Each direct reference between uniquely indexed definitions adds an edge;
repeated call sites collapse to one graph edge. Self-references and cycles are
handled with a visited set. Calls and runs both contribute possible static
edges; conditional execution is not evaluated.

`entry_programs` is required and can be an empty list. An empty list leaves
reachability fields null. Otherwise, each entry must identify exactly one
indexed definition. Every uniquely indexed program then reports
`reachable_in_static_graph`. A false value describes this selected, partial
graph. Dynamic calls, external tasks, omitted files and unsupported syntax need
review before interpreting that value as evidence about runtime use.

Rows sort by relative path, then source order; issues sort by path, line and code.
`offset` is nonnegative and `limit` is 1–100. Pagination affects the selected view
only; reference/resolution/issue counts, graph totals and the selection hash
always describe all selected files.

## Bounds and evidence limits

Select 1–128 files and up to 16 distinct entry names. Input bounds are 1 MiB per
file, 16 MiB total, 50000 total physical lines, 2048 UTF-8 bytes per decoded line
and 4096 call/run references. Limits are checked before returning a report;
exceeding a bound refuses the operation. Text supports UTF-8 with optional BOM
and strictly decodable CP1252; NULs and unsupported encodings are refused.

`dependency_completeness_verified`, `runtime_reachability_verified`,
`compiler_validation_performed` and `controller_program_inventory_verified`
remain false. No controller files are loaded, selected or executed. The existing
workspace cross-reference/lint tools remain available. The self-contained
release includes the project's MIT license.

```python
from pathlib import Path
from fanuc_workbench.services.program_analysis import analyze_dependencies

report = analyze_dependencies(Path("saved-workspace"),
    ["MAIN.LS", "subprograms/CHILD.LS"], entry_programs=["MAIN"], view="references")
```
