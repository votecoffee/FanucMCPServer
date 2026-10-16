# Changelog

## Unreleased

- Extracted remaining GUI connection tests, transfers, live comparisons and
  restore previews into the shared headless `ControllerService`.
- Added an optional local MCP adapter for configured robot files/logs and
  reviewed application-file replacement with byte preservation, stale-file
  checks, backups, bounded reads, readback verification and audit evidence.
- Added local plugin development descriptors and setup/API documentation.
- Physical robot and Codex plugin installation validation remain pending.

## 2.0.0

Added ROBOGUIDE handoff/launch support, SFTP transport, fleet and in-app scheduled backup/change detection, live watch/trend/timeline/cycle tools, cycle profiling/comparison, alarm intelligence, troubleshooting snapshots, commissioning baselines/checklists, Robot Lint, option inventory, EtherNet/IP evidence viewer, PLC handshake analyzer, frame transformation calculator, and Plugin SDK 1.0.

All v1.0 backup, restore, program, register, frame, I/O, diagnostics, project, and documentation features remain available.
