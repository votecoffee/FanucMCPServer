# Shared-service and MCP coverage

This mapping describes implementation coverage, not physical acceptance. The
expanded development server exposes 42 tools. Unsupported native operations are
reported through `capabilities`.

| Existing area | Chat/API access | Boundary |
| --- | --- | --- |
| Controller, FTP/SFTP | Listing, exact retrieval, reviewed application-file replacement and TXT/CSV storage creation | Creation outside MD: with separate exact allowlist; no native load/run acceptance |
| API file exchange | Hash-bound captured-byte ranges and bounded base64 proposal staging | Generated local source paths; no host filesystem access required by the client; existing approval/write policy applies |
| Connection refresh | Apply endpoint-only edits from the bound local configuration without restarting this server | Refuses policy changes and active work; invalidates pending plans; no controller contact during refresh |
| Backup | Full/selected jobs, ASCII companions, retained manifests | Bounded capture; partial/cancelled states explicit |
| Restore | Manifest-bound per-file recovery through upload plans | System/variable restore outside chat policy |
| Compare, workspace, parsers | Workspace comparisons, programs, positions, variables, comments, R/PR/frame/I-O evidence | Preserved/hash-checked workspace IDs |
| Comment/data editing | Separate source copies through existing setters | Generated listings stay offline; no live value/frame writes |
| Live comments | ComSet preparation/approval/readback with preserved references and target description staleness checks | Exact target/origin, byte/encoding policy; numeric-register descriptions tested on two ROBOGUIDE controllers |
| Diagnostics | Health, alarms, recurrence, mastering and DCS evidence | Mastering/DCS read-only |
| Lint and cross references | Lint, dependencies, unused programs, register usage, LS validation and flow | Structural evidence; no compiler |
| Option inventory | Matches and unknown status | Does not authenticate installed options |
| EtherNet/IP | Adapter/scanner evidence | No configuration write |
| Monitoring | Samples, watch jobs, trend/timeline/cycle exports and profiles | Two-second minimum; source files and availability errors retained |
| Handshake | Mapping authoring and recording analysis | New local workspace versions; timestamp evidence |
| Snapshots | Capture and compare | Offline troubleshooting evidence |
| Commissioning | Checklist versions, baseline packages and comparisons | User-authored evidence; no acceptance approval |
| Transformations | Finite six-axis frame conversion | Calculation only; physical verification pending |
| Documentation | HTML/XLSX/PDF packages | Offline artifacts |
| ROBOGUIDE | FTP/TCP file access, analysis and preserved handoff package | Explicit virtual-controller endpoints; no Windows automation required |
| Fleet | Configured robots, reachability probes and grouped independent backup jobs | Scheduling stays in Workbench/host; no scheduler installed |
| Project/cell | Project metadata versions, backup association, PLC tag maps and deployment packages | New local artifacts; originals preserved |
| Local plugin setup | Offline wheels, stable runtime, launcher, skill, CLI registration and MCP doctor | Windows/Python-version-specific bundle; public distribution unverified |
| New jobs/artifacts | History, cancellation, interruption after restart, artifact ranges and audit | No blind write retry or automatic recovery |

New program-file creation, controller load/compile, program start/stop, motion, live
register-value writes, frame writes, I/O forcing, controller mode/running-state
interlocks and DCS/mastering writes require a verified controller adapter and
are unsupported. File service acceptance does not establish them.

Elicitation provides client-mediated confirmation, not independent human
authentication. Local Python, shell and configuration are trusted. Advisory
locks only coordinate bridge clients sharing state and configured host strings.

Physical use still needs checks of controller file paths, generated listings,
transport options, credentials/host keys, client confirmation, loader behavior
and ComSet/security settings if enabled. Offline tests establish none of those
native acceptance claims.
