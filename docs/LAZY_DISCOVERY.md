# Lazy discovery frontend

The optional Windows frontend answers reviewed discovery methods without starting
Python. The ordinary installer and selected route remain unchanged until a
separate guarded publication selects this candidate. The frontend requires
Windows x64 and .NET Framework 4.8; building uses the Windows C# compiler and
does not download packages.

## Build a candidate

Capture `initialize`, `tools/list`, `prompts/list`, `resources/list` and
`resources/templates/list` results through one verified standalone-offline SDK
session. Save the public results in a JSON object keyed by method name. Review
the catalog against that exact backend selection; retain the capture hash and
the original process/EOF evidence outside Git. Do not include tool results,
guidance contents or revisions, credentials, live health, occupancy or plans.

```powershell
python scripts/build_lazy_frontend.py `
  --runtime-root C:/Applications/FanucMCPServer/MCP `
  --catalog-capture C:/Packages/fanuc-discovery/CATALOG_CAPTURE.json `
  --output C:/Packages/fanuc-discovery/candidate
```

The builder verifies the existing app ownership and immutable backend closure,
checks the captured offline catalog and release, and emits a hash-bound
`frontend.json`, executable, retained bootstrap and descriptors. It prints the
exact candidate connection. It never edits Codex settings or selects a runtime.
Keep the candidate bytes unchanged while reviewing and publishing them through
the shared [settings publication contract](CONFIG_PUBLICATION.md).

## Protocol and lifetime

At startup the frontend hashes and retains deny-write/delete handles for its
payload, the selected interpreter and immutable runtime. Directory handles fence
renames and links. Its manifest binds a specific backend snapshot, rather than
following a mutable selector on the first call. A later selection or bootstrap
publication cannot substitute different backend bytes into an open connection.

The handshake and the four reviewed list methods use public descriptors from
that selection. List parameters may omit the cursor, set it to null and carry
only an optional progress token. Resource descriptors contain names and URIs;
resource contents, guidance revisions and live observations require the backend.
Semantic cursors, unsupported metadata and other methods activate Python. A
static response makes no assertion about current health or available worker slots.

Activation starts one original backend, verifies its initialization and tool
catalog, and forwards the pending request once. The original host initialization,
opaque request IDs, UTF-8, media, metadata and subsequent frames are retained.
Cancellation observed before dispatch prevents that operation from running.
Failure ends the connection without another launch or operation replay. Once
activated, the backend stays for the connection; no idle restart, native context
migration or grant persistence is introduced. This frontend supports only the
existing handshake-based standalone-offline profile and MCP SDK 2.3.0.

There are at most 16 pending frames before activation, each limited to 8 MiB;
after activation the transport forwards byte chunks without reserializing or
imposing that frame limit. Completed input nodes and frames are cleared before
waiting for more input. Backend replies are drained before normal EOF completes.

The existing **32-connection** limit remains. Frontend and backend share a
two-actor **128 MiB** job, retaining the same aggregate per-connection memory
ceiling. This leaves less memory for analysis after activation than a Python-only
connection; assess real workloads before selecting the optional frontend.
Admission transfers the frontend's own receipt to its exact child under the same
lock, without changing another connection's record or reserving another slot.
An additional closing receipt conservatively covers frontend residence during
backend drain. Capacity is not released while a represented actor remains alive.
Live and uncertain legacy records keep their original accounting and barriers.

The frontend watches its original parent through a query-only retained handle.
Its own kill-on-close job bounds its original child if that parent disappears.
Normal EOF closes the backend input and waits on the original creation handle.
Uncertain launch intent and failed handoffs remain available for inspection.
No controller/native operations or execution extensions are exposed.

## Validation scope

Use one actual Codex discovery with its real startup parameters and verify zero
backend starts, exact catalog and frontend private commit. Separately verify one
useful explicit read and original-handle EOF. Focused synthetic Windows controls
cover discovery parameters, cancellation, pinned drift, Unicode, large frames,
media/metadata, parent death with a retained pipe writer and the single activation.
Retain failed cases and report source, candidate, publication and existing-chat
activation separately. This is not an eight-start throughput or native acceptance
claim. See [connection readiness](CONNECTION_READINESS.md).
