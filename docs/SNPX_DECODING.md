# Offline SNPX word decoding

The optional `workbench.snpx` extension interprets preserved raw-word capture
JSON from [SNPX reads](SNPX_READS.md). It supplies
`workbench.snpx.decode_words` through the existing operation catalog. It reads
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
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/snpx
```

Then call `reload_extensions`, `list_operations` and
`describe_operation("workbench.snpx.decode_words")`. Use the returned exact
revision with `run_read_operation`. Staging does not execute code, contact a
controller or overwrite a preserved release. Keep published versions immutable;
changed releases need a new version. See [feature development](ROBOT_EXTENSIONS.md).

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
