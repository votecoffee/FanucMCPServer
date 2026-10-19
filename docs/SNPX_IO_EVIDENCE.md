# Offline SNPX I/O value comparison

The optional `workbench.snpx` 1.3.0 feature adds
`workbench.snpx.inspect_io_evidence`. It compares saved named I/O values with
retained wire payloads using an explicit caller-supplied expression order. It
opens no connection, modifies no files and performs no assignment or write.

This comparison checks consistency under the supplied order and recorded value
formats. It cannot authenticate that order, the format metadata, the capture,
physical controller identity or controller mapping. These remain separate,
explicitly unverified claims even when every value matches.

## Activate

Stage `install-builtin snpx`, then call `reload_extensions` in an existing
extension-capable MCP session. An older host API 1 runtime can use its existing
installer with the self-contained release directory:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/snpx_v1_3_0
```

The permanent 48 MCP tools stay unchanged. Releases 1.0.0, 1.1.0 and 1.2.0 are
preserved for rollback, under `extensions/snpx`, `extensions/snpx_v1_1_0` and
`extensions/snpx_v1_2_0`. The previous decoder, comparison and frame inspector
operations keep their schemas. Describe operations again after a version switch
to obtain the selected exact revisions. See [feature reload](ROBOT_EXTENSIONS.md).

## Compare saved values

Describe `workbench.snpx.inspect_io_evidence`, then call `run_read_operation`
with its revision and these arguments:

```json
{
  "artifact_id": "<sample-artifact-id>",
  "capture_path": "sample.json",
  "wire_path": "wire.json",
  "expressions": ["DI[1]", "GI[1]", "AO[1]"],
  "offset": 0,
  "limit": 100
}
```

Supply the original capture expression spellings in the intended sequential
read order. JSON object key order is not used as evidence. The expression set
must exactly match the saved `values` and `value_formats` keys, with no duplicate
normalized targets. Case, whitespace and leading zeros can normalize to the same
target; the original spelling remains the capture lookup key. Indices use up to
five ASCII digits and must remain inside the supported port's range.

Use a saved SNPX I/O sample or compatible files imported into one workspace. A
sample copied into a [recording bundle](RECORDING_EVIDENCE.md) also works: use
the bundle artifact ID and its `samples/<index>/sample.json` and `wire.json`
paths. The host verifies both artifact files before dispatch. The inspector
reads each source once and uses those same bytes for protocol checks, values
and source hashes.

## Checks

The existing [wire inspector](SNPX_EVIDENCE.md) checks initialization, session,
sequences, frame/status/size fields, supported segments and failure propagation.
Capture `wire_sha256` must match the exact saved trace bytes before a value is
compared. An additional or missing read prevents the aggregate result passing.
Read requests are paired by ordinal with the supplied expressions, then their
segment, start index and count must match the fixed client layout:

| Ports | Read shape | Value decoding |
| --- | --- | --- |
| DI/DO, RI/RO, UI/UO | Containing byte, eight bits | Select only the requested bit; require a captured boolean |
| GI/GO, AI/AO | One 16-bit word | Explicit saved `u16` or `i16`, little endian; require an integer in that range |

DI/DO indices are 1–5000; RI/RO/UI/UO and GI/GO/AI/AO are 1–1000. No units,
engineering scaling, assignment setup or R/PR variable interpretation is inferred.
`scaling_applied` must be exactly false, and each expression needs exact format
metadata. Unsupported formats and malformed capture/trace shapes refuse analysis.

`shared_request_addresses` flags targets whose reads use the same byte address.
For example, DI[1] and DI[8] request the same containing byte. The trace alone
cannot distinguish their intended order, and repeated reads may observe changing
bytes. Each row exposes `shared_request_address` and its selected `bit_offset`;
`expression_order_verified` stays false for every report.

## Results and limits

Each expression reports expected request fields, wire exchange index, captured
value/error, decoded wire value, issues and `matched`, `mismatch` or `unavailable`
status. A null value, an explicit error (including an empty string), a wrong
boolean/integer type or an unavailable payload cannot become a verified zero.
Valid false/zero payloads can match. A valid prefix can still be compared when a
later read fails; later exchanges remain unavailable under the protocol failure
state, and the aggregate result stays false.

`all_values_match_under_supplied_order` requires a valid complete trace, matching
read count/order and all values matching. It is conditional on the supplied order
and recorded formats. `request_order_matches_supplied_expressions` checks request
addresses/counts and does not authenticate semantic ordering. `io_value_binding_checked`
means this comparison was attempted; `expression_order_verified`,
`value_format_metadata_verified`, `mapping_verified`, `capture_provenance_verified`,
`physical_identity_verified` and `controller_compatibility_verified` remain false.

`wire_evidence` summarizes the frame inspector. Root `issues` include read-count
differences. Pagination affects only expression `items`; `summary`, trace counts
and aggregate checks always cover all expressions. The default page limit is
100; `offset` is nonnegative and `limit` is 1–100. Captures support 1–100 expressions,
256 KiB capture JSON, 1 MiB wire JSON, 256 exchanges and 512 bytes per frame.

This feature needs only Python's standard library. The original decoder and
entrypoint retain the MIT license; protocol and fixed-port adaptations retain
Apache-2.0 attribution, license and notice inside the release. No controller
option is needed to analyze saved files; collecting them still requires a
compatible configured SNPX service. Controller acceptance remains pending.

```python
from pathlib import Path
from fanuc_workbench.services.snpx_evidence import inspect_io_evidence

report = inspect_io_evidence(Path("sample.json"), Path("wire.json"),
                             ["DI[1]", "GI[1]", "AO[1]"])
```
