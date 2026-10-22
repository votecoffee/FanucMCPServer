# Shared-service and MCP coverage

This mapping describes implementation coverage, not physical acceptance. The
expanded source catalog has 51 established tools and four staged job tools.
Optional protocol features remain [gated](PROTOCOL_FEATURES.md). Unsupported native operations are
reported through `capabilities`.

| Existing area | Chat/API access | Boundary |
| --- | --- | --- |
| Native controller data | R/PR/I/O, current position, tasks, alarms and program metadata through FRRobot TCP API | Configured provider; sequential captures; per-section availability errors |
| Experimental SNPX | Exact digital/group/analog I/O samples and raw `%R` words through the existing catalog | Explicit read allowlists and word decoding; no scaling; hashed evidence; no assignments/writes or typed R/PR decoding; controller acceptance pending |
| Native changes | Reviewed R/PR values and LS loading/compilation through core plans | Exact target policy; native readback; PR tolerance explicit; no program execution |
| Feature extensions | Stable MCP catalog, exact-revision read/proposal calls and atomic worker reload; all 20 workspace analyses delegated | Trusted local hash-pinned releases, no arbitrary native write classes; failed reload preserves active features and plans |
| Controller, FTP/SFTP | Listing, exact retrieval, reviewed application-file replacement and TXT/CSV storage creation | Creation outside MD: with separate exact allowlist; no native load/run acceptance |
| API file exchange | Hash-bound captured-byte ranges and bounded base64 proposal staging | Generated local source paths; no host filesystem access required by the client; existing approval/write policy applies |
| Connection refresh | Apply endpoint-only edits from the bound local configuration without restarting this server | Refuses policy changes and active work; invalidates pending plans; no controller contact during refresh |
| Backup | Full/selected jobs, ASCII companions, retained manifests | Bounded capture; partial/cancelled states explicit |
| Restore | Manifest-bound per-file recovery through upload plans | System/variable restore outside chat policy |
| Compare, workspace, parsers | Workspace comparisons, programs, positions, variables, comments, R/PR/frame/I-O evidence | Preserved/hash-checked workspace IDs |
| Comment/data editing | Separate source copies through existing setters | Generated listings stay offline; no live value/frame writes |
| Live comments | ComSet preparation/approval/readback with preserved references and target description staleness checks | Exact target/origin and byte/encoding policy; controller-specific ComSet compatibility must be verified |
| Diagnostics | Health, alarms, recurrence, optional explicit-clock text log analysis/comparison, mastering and DCS evidence | Offline count differences do not identify physical occurrences or comparable capture windows; mastering/DCS read-only |
| Lint and cross references | Lint, dependencies, unused programs, register usage, LS validation/flow and optional selected-LS static graph | Explicit graph entry names and unresolved references; structural evidence; no compiler or runtime reachability verification |
| Option inventory | Matches and unknown status | Does not authenticate installed options |
| EtherNet/IP | Adapter/scanner evidence | No configuration write |
| Monitoring | Samples, watch jobs, trend/timeline/cycle exports, profiles, offline summaries and recording evidence bundles | Two-second minimum; sample links and retained SNPX traces can be inspected offline; physical provenance and I/O wire-value mapping stay unverified |
| Handshake | Mapping authoring, event correlation and optional sampled request/ack analysis | Explicit states and gaps; nominal timestamp-grid ranges; no verified physical timing or causation |
| Snapshots | Capture and compare | Offline troubleshooting evidence |
| Commissioning | Checklist versions, baseline packages and comparisons | User-authored evidence; no acceptance approval |
| Transformations | Finite six-axis frame conversion | Calculation only; physical verification pending |
| Documentation | HTML/XLSX/PDF packages | Offline artifacts |
| ROBOGUIDE | FTP/TCP file access, analysis and preserved handoff package | Explicit virtual-controller endpoints; no Windows automation required |
| Fleet | Configured robots, reachability probes and grouped independent backup jobs | Scheduling stays in FanucMCPServer/host; no scheduler installed |
| Project/cell | Project metadata versions, backup association, PLC tag maps and deployment packages | New local artifacts; originals preserved |
| Local plugin setup | Offline wheels, stable runtime, launcher, skill, CLI registration and MCP doctor | Windows/Python-version-specific bundle; public distribution unverified |
| New jobs/artifacts | History, cancellation, interruption after restart, artifact ranges and audit | No blind write retry or automatic recovery |

Native LS creation/loading and R/PR value edits are available through the explicit
provider and target policy described in [native API setup](ROBOT_NATIVE_API.md).
Program start/stop, motion, frame writes, I/O forcing, controller mode/running-state
interlocks and DCS/mastering writes require a verified controller adapter and
are unsupported. File service acceptance does not establish them.

Elicitation provides client-mediated confirmation, not independent human
authentication. Local Python, shell and configuration are trusted. Advisory
locks only coordinate bridge clients sharing state and configured host strings.

Physical use still needs checks of controller file paths, generated listings,
transport options, credentials/host keys, client confirmation, loader behavior
and ComSet/security settings if enabled. Offline tests establish none of those
native acceptance claims.
