# FANUC Workbench 2.0

A modular Python/PySide6 engineering workbench for FANUC robot backup, offline analysis/editing, live troubleshooting, commissioning, fleet maintenance, integration documentation, and controlled restore.

## New in 2.0

This release adds the requested follow-on features **1–12, 14, 16, 17, 21, 22, 27, and 39** on top of the v1.0 feature set.

1. **ROBOGUIDE integration** — discover/browse the ROBOGUIDE executable, stage an active FANUC backup into a virtual-controller handoff folder, optionally open a workcell, and launch ROBOGUIDE. Workbench intentionally does not automate unverified private ROBOGUIDE UI/COM interfaces.
2. **SFTP backend** — robot profiles can use FTP or SFTP. SFTP uses Paramiko/SSH, system host keys, unknown-host rejection by default, and explicit TOFU opt-in.
3. **Fleet backup manager** — load configured robot profiles, select multiple robots, and back them up as one operation with per-robot status/change counts.
4. **Scheduled backups + change alerts** — in-app scheduler with per-robot minute intervals; each completed backup is compared to the prior backup and marked `CHANGED` when differences are detected. The scheduler runs while Workbench is open.
5. **Live Watch table** — watch `R[]`, `PR[]`, and common FANUC I/O tokens. Source can be the active workspace or bounded live polling of controller-generated `NUMREG.VA`, `POSREG.VA`, and `IOSTATE.DG` files.
6. **Signal/register trend recorder** — in-app numeric plot plus CSV export of captured samples.
7. **I/O timeline recorder** — records value transitions with millisecond timestamps and exports CSV.
8. **Cycle recorder** — record state transitions from any watched signal/value, add manual event marks, and export JSON.
9. **Cycle profiler** — totals elapsed time by captured state and shows percent of recorded cycle.
10. **Cycle comparison** — compare two exported cycle recordings and show per-state timing deltas.
11. **Alarm intelligence** — recurring alarm summary plus correlation with captured signal changes that occurred shortly before timestamp-compatible alarms.
12. **Troubleshooting snapshots** — capture active workspace health/comments/positions/alarms/mastering/DCS evidence plus latest live values; compare two snapshots.
14. **Commissioning baseline + checklist** — create a protected commissioned copy, compare future workspaces against it, and persist a per-workspace commissioning checklist.
16. **Robot Lint** — engineering quality checks for missing CALL targets, missing/duplicate labels, undocumented R/PR/frames, and suspicious UF outliers.
17. **Robot option inventory** — evidence-based scan for ASCII Program Loader, KAREL, iRVision, DCS, EtherNet/IP, BG Logic, OPC UA, SFTP, MQTT, Collision Guard, etc. `Not determined` is used when readable backup files do not prove status.
21. **EtherNet/IP viewer** — read-only extraction of Adapter/Scanner evidence, connection/IP/slot, sizes, RPI, and status from human-readable controller data.
22. **PLC/FANUC handshake analyzer** — persistent handshake map (`robot signal ↔ PLC tag ↔ expected peer`) and timing analysis against the captured live timeline.
27. **Frame transformation calculator** — convert Cartesian X/Y/Z/W/P/R poses between user-frame coordinate systems while preserving physical pose. Uses the documented/common FANUC W/P/R matrix convention `Rz(R)·Ry(P)·Rx(W)` and includes a verification warning.
39. **Plugin SDK 1.0** — manifest/API version, service registry, legacy compatibility, entry-point discovery, and a complete template. See `PLUGIN_SDK.md` and `examples/plugin_sdk_template.py`.

## Existing v1.0 capabilities retained

- Recursive backup + ASCII companion attempts.
- Backup history and semantic comparison.
- Selective restore with live preview and explicit write confirmation.
- LS program editor/validation/cross-reference/dependency/flow analysis.
- R/PR/frame/comment editing and live supported comment writes.
- I/O browser and PLC mapping CSV.
- Alarm history, health, mastering read-only comparison, DCS read-only comparison.
- Robot projects/cell comparison/golden deployment packages.
- HTML/XLSX/PDF documentation generation.

## Live data model

Workbench deliberately avoids undocumented controller web-page scraping. The built-in live provider polls human-readable controller-generated files at a **minimum two-second interval** and reports unavailable signals instead of fabricating data. The provider interface is designed so an OPC UA or verified KAREL/HTTPS implementation can be installed later without changing the UI.

## Safety model

- Back up before writes.
- Unknown/binary controller files are preserved, not reverse-engineered.
- Mastering and DCS remain inspection/diff only.
- Restore/upload requires explicit confirmation.
- Commissioning baselines are copies, never controller writes.
- EtherNet/IP and option inventory are evidence/read-only tools.
- Frame calculations are engineering aids; verify results on the pendant/virtual controller before production use.
- SFTP rejects unknown host keys unless the user explicitly enables TOFU.
- Passwords are not persisted in the normal profile JSON. Scheduled backups that need password authentication require credentials in the current session; SSH key/agent authentication can be used for SFTP.

## Install on Windows

Python 3.11+ is recommended. Double-click `install_and_run.bat`, or:

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
python run.py
```

Dependencies: PySide6, XlsxWriter, ReportLab, and Paramiko.

## Codex / MCP robot bridge (development)

The GUI and the optional local MCP adapter now share a headless controller
service. The expanded server exposes 33 tools for backup jobs, workspace
analysis/editing, live samples/recordings, snapshots/packages, audit evidence,
and reviewed application-file/comment changes. Install the adapter with
`python -m pip install -e ".[mcp]"` and see
[Robot bridge setup and API guide](docs/ROBOT_BRIDGE.md) for configuration,
file/log tools, reviewed application-file replacement, and verification limits.
Writes default to disabled; the example requests client-mediated confirmation.
See the [service coverage matrix](docs/ROBOT_BRIDGE_COVERAGE.md) for unsupported
native operations. The development plugin descriptors have not been
installed into Codex or tested against a physical controller.

For development/testing:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Suggested workflow

1. Configure **Robot → Connection…** and choose FTP or SFTP.
2. Take/open a backup and establish a commissioning baseline.
3. Run **Robot Lint**, option inventory, and EtherNet/IP evidence scan.
4. Configure a **Live / Recorder** watch set for the signals relevant to the problem.
5. Capture trend/timeline/cycle evidence or a troubleshooting snapshot.
6. Use the PLC handshake analyzer when diagnosing interface timing.
7. Stage changes for ROBOGUIDE before production deployment when appropriate.
8. Compare against the commissioned baseline and use Selective Restore for deliberate writes.
9. Add robots to **Fleet / Scheduler** for recurring backups/change detection.

## Validation

The original v2 release contains 26 baseline regression tests. The development
bridge adds shared-service, policy, transfer-failure and MCP protocol coverage.
Run `python -m pytest -q` from the project root; install the `mcp` extra to include
the SDK protocol tests.
