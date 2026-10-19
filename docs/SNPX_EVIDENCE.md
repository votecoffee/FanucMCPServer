# Offline SNPX evidence inspection

Version 1.2.0 of the optional `workbench.snpx` extension adds
`workbench.snpx.inspect_evidence`. It checks a saved `sample.json` and `wire.json`
against this application's narrow read protocol. It opens no socket, contacts no
controller and supplies no write or assignment operation.

The inspector helps distinguish a reply containing zero bytes from unavailable
data, a failed handshake or a rejected frame. Matching saved bytes do not prove
controller compatibility, physical identity, transmission or assignment meaning.
The feature leaves those claims explicitly unverified.

## Activate the feature

Use `install-builtin snpx` with the current source/headless runtime, then call
`reload_extensions` in an extension-capable MCP session. The permanent 48 tools
remain unchanged. An older host with the extension interface can stage the
self-contained release directory through its existing CLI:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/path/to/FanucMCPServer/fanuc_workbench/extensions/snpx_v1_3_0
```

Releases 1.0.0, 1.1.0 and 1.2.0 remain preserved for rollback. Their source directories
are `extensions/snpx`, `extensions/snpx_v1_1_0` and `extensions/snpx_v1_2_0` under `fanuc_workbench`.
Select a preserved directory with `install`, reload, and describe operations
again to obtain their exact selected revisions. Changing the selected feature
does not restart the MCP host. See [reloadable features](ROBOT_EXTENSIONS.md).

## Inspect a capture

Describe `workbench.snpx.inspect_evidence`, then invoke `run_read_operation` with
the returned revision and these arguments:

```json
{
  "artifact_id": "<saved-artifact-id>",
  "capture_path": "sample.json",
  "wire_path": "wire.json",
  "offset": 0,
  "limit": 100
}
```

Use the artifact ID returned by a SNPX raw-word read or I/O sample. Compatible
files imported into one local workspace also work. The host validates both
artifact paths and their manifest hashes before dispatch. The inspector hashes
the exact bytes it consumes and compares the wire file's SHA-256 with the
capture's `wire_sha256`. A missing/invalid hash is unknown; a different hash is a
mismatch. Both prevent `supported_trace_valid` from becoming true. They do not
prevent inspection of the recorded frame shapes.

## Checks and results

Each exchange reports its request category and address/count, reply header,
recorded transport error type, issue codes, whether it matches the supported
read contract, and an accepted read payload in hex. Checks include:

- INIT acknowledgement and session handshake before reads.
- Sequential request/reply IDs and supported packet/message types.
- Exact recorded frame lengths, bounded read sizes and supported segments.
- Rejection of multipart replies, nonzero status and extended service/segment
  mismatches.
- An explicit failure state: later valid-looking replies are not accepted after
  an earlier exchange fails.
- Reported transport errors, even if accompanying bytes look complete.
- Requests outside the read contract, including recognizable write services.

The session handshake uses a protocol `Writing` header flag and is recognized
separately; it is not counted as an application-memory write request. An observed
write service only describes bytes in the saved input. The inspector neither
sends it nor proves it was transmitted or acted upon.

`successful_read_exchanges` and `zero_payload_read_exchanges` count recorded
replies matching the supported frame/state checks. A bad status, missing/partial
reply or broken handshake never becomes a successful zero. Hash binding and
capture consistency are separate result fields; check `supported_trace_valid`
and source hashes before using the overall evidence. Even a true result checks
this application's contract only and does not authenticate the capture.

For raw-word captures, `word_payload_matches_wire` checks that the saved span
and exact raw payload agree with one accepted register read. Contradictory word
arrays or availability metadata are reported. Named I/O sample values are not
correlated to individual reads: `word_payload_matches_wire` is null and
`io_value_binding_checked` is false. Protocol checks still apply to their trace.
Version 1.3.0 adds a separate [named I/O payload comparison](SNPX_IO_EVIDENCE.md)
operation requiring caller-supplied read order and saved format metadata. The
original frame inspector's result and limits remain unchanged.

Root `issues` describe capture/trace consistency; `exchange_issue_counts`
summarizes frame/state issues across all exchanges. Pagination affects only
`items`, with `total` and `next_offset`; summaries always cover the whole trace.
There are no round-trip timing records, so `timing_available` is false. A saved
`TimeoutError` remains a reported type, not a verified network cause. An empty
trace cannot establish that a connection was refused or an option is missing.

## Bounds and licensing

Capture JSON is limited to 256 KiB and wire JSON to 1 MiB, with at most 256
exchanges and 512 bytes per request/reply. Hex must be exact, even-length text;
duplicate JSON keys, nonfinite constants and unsupported metadata shapes are
refused. Pagination uses `offset >= 0` and `limit` 1–100. Saved files are not
modified. This is not a general packet-capture decoder or support for arbitrary
SNPX variants; unsupported frames remain explicit.

The original decoder/comparison code remains MIT licensed. The wire inspector
adapts the existing pinned Apache-2.0 protocol reference; its full license and
notice ship inside the self-contained release. See
[third-party notices](../fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md).

Local Python uses the same implementation:

```python
from pathlib import Path
from fanuc_workbench.services.snpx_evidence import inspect_evidence

result = inspect_evidence(Path("sample.json"), Path("wire.json"))
```
