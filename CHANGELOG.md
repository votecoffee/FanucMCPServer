# Changelog

## Unreleased

- Added **Settings → MCP access…** with a default read-only controller checkbox
  backed by `allow_writes`, stale-file protection and explicit reconnect guidance.
  Capabilities, doctor and the Native Controller policy summary expose the loaded
  access mode. Enabling reviewed writes retains target and approval policy.
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
