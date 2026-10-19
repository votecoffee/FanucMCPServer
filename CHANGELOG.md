# Changelog

## Unreleased

- Added a self-contained MIT `workbench.programs` feature for bounded selected-LS
  call/run references, comment/string masking, unresolved and ambiguous targets,
  and explicit-entry static reachability. Controller inventory, compiler validity
  and runtime reachability remain unverified; the 48 MCP tools stay unchanged.
- Added `workbench.alarms` 1.1.0 saved-log comparison with independent explicit
  clocks, code presence/count deltas and complete severity/message pair checks.
  Count changes do not identify physical events or comparable capture windows;
  the original 1.0.0 release and 48 permanent MCP tool schemas are preserved.
- Added a self-contained MIT `workbench.alarms` feature for bounded selected-log
  analysis, unrecognized/malformed evidence, explicit date order/UTC offsets,
  exact microseconds and paginated code recurrence. Parsed records do not verify
  physical occurrences, clock assumptions or log completeness; 48 tools remain.
- Added `workbench.recording` 1.2.0 with sampled request/ack analysis, explicit
  scalar states, stale/coobserved/gap/open outcomes and nominal polling-interval
  timing ranges. Physical timing, continuous observation and causation remain
  unverified. Preserved prior releases, operation schemas and component licenses.
- Added `workbench.snpx` 1.3.0 with offline named I/O payload checks under an
  explicit supplied read order, exact signed/unsigned formats, shared-address
  diagnostics, partial/unavailable handling and source snapshots read once.
  Preserved earlier releases, existing operation schemas and component licenses.
- Added `workbench.recording` 1.1.0 with portable bundle hash/path checks,
  recording/sample equality, native value projection, retained SNPX wire
  inspection, explicit unknown/unavailable evidence and paginated reports.
  Preserved 1.0.0 unchanged for rollback and retained all component licenses.
- Added `core.recordings.bundle_evidence` to copy selected recording/sample
  evidence locally with source manifest checks, exact hashes, paging, copy-time
  verification and bounded resource use. The catalog operation requires an
  updated host runtime; the 48 permanent MCP tools stay unchanged.
- Added the self-contained MIT `workbench.recording` 1.0.0 feature for offline
  watch availability, numeric ranges, gap-aware change counts, bounded timestamp
  summaries, source hashes and signal pagination. Sample reference contents and
  recording completion remain unverified. Compatible hosts reload it through the
  existing operation catalog without changing the 48 permanent MCP tools.
- Added `workbench.snpx` 1.2.0 with offline `inspect_evidence`: capture/wire hash
  binding, handshake/reply diagnostics, bounded frame checks, raw-word payload
  correlation and explicit limits for I/O binding, timing and physical identity.
  Prior feature releases remain intact; component licenses ship with the release.
- Added `workbench.snpx` 1.1.0 with offline `compare_words`, same-endpoint/span
  checks, exact integer/string deltas, explicit float tolerances, raw changes,
  unavailable/overflow handling, source hashes and pagination. Preserved the
  immutable 1.0.0 release; both upgrades and rollback use the existing reload
  command and 48 permanent MCP tools. Shared Python uses the current release.
- Added a self-contained reloadable `workbench.snpx.decode_words` offline feature
  for captured 16/32/64-bit integers and IEEE floats, with explicit per-field
  byte/word order, bounded arrays, source hashes and unavailable/nonfinite errors.
  Mapping/provenance remain unverified; 64-bit integers use decimal strings.
  `install-builtin snpx` stages it without adding MCP tool schemas.
- Extended experimental SNPX sampling to allowlisted GI/GO/AI/AO ports, with
  mandatory per-target signed/unsigned 16-bit decoding, retained sample format
  metadata and no engineering-unit scaling. Existing digital reads and the
  48-tool MCP interface remain compatible; controller acceptance is pending.
- Added experimental opt-in SNPX digital I/O sampling and catalog-based raw
  assigned-word reads with exact read policy, bounded framing/deadlines, audited
  hash-manifested wire evidence and no automatic fallback. Assignment meaning is
  explicitly unverified; no write/CLRASG/SETASG API is provided. Retained the
  adapted Apache-2.0 protocol license. Controller acceptance remains pending.
- Added an optional self-contained `workbench.vqlog` feature with ten event
  formats, source hashes, filters/pagination, retained unknown/malformed rows and
  explicit tick/clock limits. `install-builtin vqlog` stages it for same-session
  reload without adding MCP tool schemas. Retained upstream MIT notices and
  documented the controller-mapping boundary for SNPX integration.
- Added optional same-host HTTP(S) reads of exact allowlisted MD text files for
  reads, logs and file samples. Preserved byte hashes, bounded requests, separate
  optional credentials, redirect/HTML rejection and origin-bound receipts;
  FTP/SFTP still owns listings, backups and write verification. Included upstream
  MIT notices for the adapted ONE Robotics retrieval convention.
- Made masked, malformed, duplicate and nonfinite PR axes explicit as unavailable;
  retained external axes and prevented configuration text from swallowing the
  next line. File-based samples reject incomplete PRs and invalid numeric values.
- Added **Settings → MCP access…** with a default read-only controller checkbox
  backed by `allow_writes`, stale-file protection and explicit reconnect guidance.
  Capabilities, doctor and the Native Controller policy summary expose the loaded
  access mode. Enabling reviewed writes retains target and approval policy.
- Documented the controller interfaces and per-feature FANUC licensing needs,
  including the distinction between FTP reads, HTTP description writes, native
  Robot Object access, SFTP Server and ASCII Program Loader.
- Renamed the project and repository to FanucMCPServer. New installs use the
  `fanuc-mcp-server` desktop/shared-service distribution and
  `fanuc-mcp-server-bridge` headless distribution. Legacy Python imports and
  commands remain compatible; existing runtime upgrades preserve configuration.
- Added a stable 48-tool MCP host with a versioned operation catalog and
  reloadable feature workers. All 20 workspace analyses use the bundled feature
  release; compatible extensions can reload within an existing MCP session.
- Added optional FANUC Robot Object native data over TCP, reviewed R/PR value
  changes, and LS deployment through controller ASCII loading with native
  preflight and TP/LS verification. Native access defaults to disabled; native
  program deployment validation and physical-controller acceptance remain pending.
  Motion, program execution/start/stop, I/O forcing, frame and safety writes
  remain unsupported.
- Added durable backup/watch jobs, hash-manifested artifacts, separate offline
  edits, recordings, snapshots, offline packages, per-file recovery and audit
  access.
- Added MCP client elicitation, bridge locks, shared sampling limits and
  disabled-by-default ComSet changes with fresh target-description readback.
- Added hash-checked captured-byte ranges, base64 proposal staging, exact-policy
  TXT/CSV storage creation and endpoint-only connection reload.
- Preserved text encodings, corrected multigroup PR/flag decoding, excluded
  unavailable samples from timelines, closed failed FTP/SFTP connections and
  refused controller write redirects.

- Extracted remaining GUI connection tests, transfers, live comparisons and
  restore previews into the shared headless `ControllerService`.
- Added an optional local MCP adapter for configured robot files/logs and
  reviewed application-file replacement with byte preservation, stale-file
  checks, backups, bounded reads, readback verification and audit evidence.
- Added local plugin development descriptors and setup/API documentation.
- Documented public contribution guidance and ignored private `.local/` storage;
  removed tracked Python bytecode and local test narratives from public guides.
- Physical-controller acceptance and client confirmation UI acceptance remain
  separate from offline and SDK protocol checks.

## 2.0.0

Added ROBOGUIDE handoff/launch support, SFTP transport, fleet and in-app scheduled backup/change detection, live watch/trend/timeline/cycle tools, cycle profiling/comparison, alarm intelligence, troubleshooting snapshots, commissioning baselines/checklists, Robot Lint, option inventory, EtherNet/IP evidence viewer, PLC handshake analyzer, frame transformation calculator, and Plugin SDK 1.0.

All v1.0 backup, restore, program, register, frame, I/O, diagnostics, project, and documentation features remain available.
