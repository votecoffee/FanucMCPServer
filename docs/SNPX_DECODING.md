# Offline SNPX word decoding and comparison

The optional `workbench.snpx` extension interprets preserved raw-word capture
JSON from [SNPX reads](SNPX_READS.md). It supplies
`workbench.snpx.decode_words` and, in version 1.1.0,
`workbench.snpx.compare_words` through the existing operation catalog. It reads
local evidence only and has no controller connection or write operation.

This is a self-contained host API 1 release with Python standard-library
dependencies. An extension-capable host can add or update it with
`reload_extensions` in the current MCP session. Its 48 permanent tools stay
unchanged. A host without the extension interface needs that host update first.
Decoding itself does not require the live SNPX provider or vendor PC software.

## Stage and activate

Use the source/headless runtime interpreter containing this bundled feature:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install-builtin snpx
```

For an existing extension-capable runtime whose CLI does not yet list `snpx`,
stage the self-contained release directory with its existing installer:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/snpx_v1_3_0
```

Then call `reload_extensions`, `list_operations` and
`describe_operation("workbench.snpx.decode_words")`. Use the returned exact
revision with `run_read_operation`. Staging does not execute code, contact a
controller or overwrite a preserved release. Keep published versions immutable;
changed releases need a new version. See [feature development](ROBOT_EXTENSIONS.md).

`install-builtin snpx` selects 1.3.0, which also supplies
[wire-evidence inspection](SNPX_EVIDENCE.md). The original 1.0.0 release remains preserved
under `fanuc_workbench/extensions/snpx`; select that directory with `install` and
reload to roll back. The current release lives in the separate
`snpx_v1_3_0` directory; 1.1.0 and 1.2.0 remain in their corresponding
`snpx_v1_1_0` and `snpx_v1_2_0` directories. It also supplies
[named I/O payload comparison](SNPX_IO_EVIDENCE.md) under a caller-supplied read
order. No previous release bytes are edited or overwritten.
After any version switch, describe operations again to obtain their new exact
revisions; stale revisions are refused.

## Decode a captured span

Use the `artifact_id` from `core.snpx.read_words` and `path: "sample.json"`.
A compatible capture JSON imported as a local workspace also works. The host
checks its saved-artifact manifest before dispatch; the result hashes the exact
JSON bytes consumed. Imported data is evidence, not authenticated provenance.

Pass these operation arguments, replacing the placeholder artifact ID:

```json
{
  "artifact_id": "<saved-artifact-id>",
  "path": "sample.json",
  "fields": [
    {
      "name": "measurement",
      "word_offset": 0,
      "type": "f32",
      "byte_order": "little",
      "word_order": "low_first"
    },
    {
      "name": "counter",
      "word_offset": 2,
      "type": "i32",
      "count": 2,
      "byte_order": "little",
      "word_order": "low_first"
    }
  ]
}
```

The capture must contain at least six words for this example. `word_offset` is
zero-based relative to the captured span; returned `word_addresses` are one-based
SNPX `%R` addresses. `count` defaults to one and counts decoded elements. Types
`u16`/`i16`, `u32`/`i32`/`f32`, and `u64`/`i64`/`f64` consume one, two and four
words per element respectively. Array elements use consecutive words. Distinct
field labels may overlap to compare interpretations of the same bytes.

Every field requires explicit byte and word order. `byte_order` chooses the
significance of the two bytes inside each 16-bit word. `word_order` chooses
whether the least or most significant word arrives first within each element.
It does not reverse the order of array elements. These four raw byte sequences
all represent IEEE-754 binary32 `1.0` under the corresponding selections:

| Raw bytes in captured address order | Byte order | Word order |
| --- | --- | --- |
| `3f800000` | `big` | `high_first` |
| `803f0000` | `little` | `high_first` |
| `00003f80` | `big` | `low_first` |
| `0000803f` | `little` | `low_first` |

No controller-specific layout is inferred. Labels, formats and order choices
are caller assumptions. Results keep `mapping_verified: false`,
`capture_provenance_verified: false` and `scaling_applied: false`. The decoder
does not identify robot R/PR variables, validate controller assignments, convert
engineering units or establish an atomic snapshot.

## Results and bounds

Each item retains its decoding choices, one raw hex string and value per element,
starting word addresses, value encoding and element-level errors. Values are
always arrays, including single elements. 64-bit integers are decimal strings
so JSON clients cannot round them through floating-point numbers. Other finite
values are JSON numbers. Floating-point NaN/infinity becomes null with a
`nonfinite_float` error; raw bytes remain available. Negative zero is preserved.

Failed captures return `available: false` with null values and
`capture_unavailable` errors. They never become zero-filled data. Contradictory
raw bytes, word arrays, counts or unavailable status are refused, as are duplicate
JSON keys and nonfinite JSON constants.

Input is bounded to 256 KiB, 128 captured words, 32 fields and 512 decoded
elements. Each field must remain within the actual captured span. Names must
be distinct bounded labels. Invalid specifications are refused before file
access. The release ships its MIT license and exact support-file hashes.

Local Python uses the same implementation:

```python
from pathlib import Path
from fanuc_workbench.services.snpx_decode import decode_capture

result = decode_capture(Path("sample.json"), fields=[{
    "name": "measurement", "word_offset": 0, "type": "f32",
    "byte_order": "little", "word_order": "low_first",
}])
```

## Compare two captures

Describe `workbench.snpx.compare_words`, then pass its revision and arguments
through `run_read_operation`:

```json
{
  "before_artifact_id": "<before-artifact-id>",
  "before_path": "sample.json",
  "after_artifact_id": "<after-artifact-id>",
  "after_path": "sample.json",
  "fields": [
    {
      "name": "measurement",
      "word_offset": 0,
      "type": "f32",
      "byte_order": "little",
      "word_order": "low_first"
    }
  ],
  "absolute_tolerance": 0.001,
  "relative_tolerance": 0,
  "changes_only": true,
  "offset": 0,
  "limit": 100
}
```

Both captures must have the same starting address and word count, and complete
matching `robot_id`, `host` and SNPX `port` metadata. Robot IDs/ports must match
exactly; hostnames compare case-insensitively without DNS resolution. Missing
metadata, different endpoints and different spans are refused. These checks
establish equality of configured metadata only. Physical identity, assignments
and provenance remain unverified; an endpoint match cannot prove unchanged
controller mappings. Files may be different artifacts or two paths in one
imported artifact. Each file is read once so values, metadata and source hashes
refer to the same consumed bytes.

Results include both source hashes, per-element before/after values and bytes,
raw changed-word addresses across the **entire captured span**, numeric deltas
(`after minus before`), and changed/unchanged/unavailable element counts. Overlapping
fields count as separate interpretations. Differences outside selected fields
still appear in the raw address list.

Integers compare exactly. 64-bit deltas are decimal strings, including deltas
outside the signed 64-bit range. Float comparisons use
`abs(a-b) <= max(absolute_tolerance, relative_tolerance * max(abs(a), abs(b)))`;
both tolerances default to zero and apply only to floats. Absolute tolerance is
bounded to 0–1e100 and relative tolerance to 0–1, with finite values required.
Raw changes remain visible regardless of tolerance. Numerically equal signed
zero values can therefore have an unchanged decoded status and changed raw words.
An overflowing float delta is null with `nonfinite_delta`, while its changed
status and raw evidence remain available.

Unavailable captures and nonfinite float interpretations produce `unavailable`
rows with explicit reasons and `comparison_complete: false`. They are never
counted as equal or changed values. If either complete raw payload is missing,
`raw_changed_word_addresses` is null. Valid raw bytes containing NaN/infinity can
still be compared as bytes while their float interpretation is unavailable.

`changes_only` defaults to true: it omits unchanged decoded rows and retains all
unavailable rows. Use false to include unchanged rows and inspect raw-only
differences. Summaries cover all requested decoded elements; `total` counts the
filtered rows. Pagination uses `offset >= 0` and `limit` 1–100, with `next_offset`
when more filtered rows remain. The existing 32-field/512-element bounds apply.

Local Python uses the same shared implementation:

```python
from pathlib import Path
from fanuc_workbench.services.snpx_decode import compare_captures

result = compare_captures(
    Path("before.json"), Path("after.json"),
    fields=[{"name": "counter", "word_offset": 0, "type": "u64",
             "byte_order": "little", "word_order": "low_first"}],
)
```
