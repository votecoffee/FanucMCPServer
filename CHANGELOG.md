# Changelog

## Unreleased

- Unified app, desktop/headless package and standalone plugin identities at
  timestamp checkpoint `26.1022.173617` (America/New_York). Preserve padded
  human display, canonical PEP 440/SemVer identities and bounded Windows file
  mappings; stamp only during explicit release preparation. Report loaded app
  versions independently of installed metadata and bind installer/bundle records
  to the same checkpoint. Build both app wheels from fresh staging and reject
  stale or changed package payloads. Framework/SDK pins and feature/schema versions stay
  independent; native/runtime acceptance remains separately required.

- Added an installed-runtime stdio supervisor with durable report-only ancestor
  records, bounded activation, opaque SDK byte relay and private job containment.
  Generate the runtime connection from a hash-bound immutable bundle recipe;
  explicitly propagate private registries through app-host environment allowlists.
  Older unmanaged publications require explicit republish. Runtime acceptance
  remains pending; no installed registration is migrated by source changes.

- Routed offline MakeTP/PrintTP and host-policy helpers through activated owned
  workers with durable records, descendant teardown and bounded private logs.
  Refuse desktop closure during active/uncertain worker cleanup. Disable
  unaccepted ROBOGUIDE auto-launch, retaining manual backup handoff without
  overwriting an existing handoff folder. Process/native acceptance is pending.

- Aligned source pins with reviewed framework 0.20.0 while retaining MCP SDK
  2.3.0 and the app's independent plugin/native skills. Integrated shared durable
  RPC launch records under the actual supervisor, exact creation binding and
  native-write recovery barriers. Source/package checks do not migrate an
  installation or establish remaining launch-path/native acceptance.

- Bound private workers to verified Windows creation identities and retained
  handles; removed PID-based `taskkill` fallback. Added a 16-worker inventory,
  degraded status for uncertain cleanup, close-all handling and explicit COM
  proxy release without closing attached applications. Durable ownership-ledger,
  remaining launch-path and real process acceptance remain pending.

- Added an exclusive MCP worker lease per policy state directory, shared by the
  standalone entrypoint and app-host backend. Retain ownership through bridge
  drain and until process exit after drain failure; no automatic restart,
  duplicate owner or PID-based cleanup. Process acceptance remains pending.

- Staged explicit backup/recording Tasks, bounded job notifications, stage/unit
  progress, resolver-based approval and hash-bound artifact resource views.
  Tasks, caching and resource views remain disabled in normal startup pending
  app-owned acceptance; the downstream legacy-profile guard remains in place.
  Added a read-only feature status report and documented release requirements.

- Replaced the Windows exec-overlay launcher with an owning stdin-forwarding
  parent that waits for the actual runtime. EOF and loss of the launcher close
  the worker's input and permit app-owned draining, preventing detached stdio
  children. Added explicit process roles and isolated test management registries.

- Added a stable packaged FANUC bootstrap, one canonical runtime guide/reference
  tree, three hash/revision-bound read tools and real Skills-over-MCP catalogs on
  the standalone and app-owned frontends. Explicit data-only publication updates
  connected clients without replacing worker code; stale pins and invalid
  publications fail closed. Native skill activation remains host acceptance.

- Updated the shared framework to 0.18.0, retaining MCP SDK 2.3.0 and the standalone
  plugin. Added cached, authenticated tray-manager reporting for owning MCP/host
  processes, canonical plugin identity, component versions and local work/plan
  counts. Restart stays disabled without a surviving app-owned supervisor; no
  robot probe, automatic recovery, route switch or duplicate manager installation
  occurs. Management startup failure does not interrupt the MCP service.

- Updated the pinned shared framework to 0.17.0 with its complete required
  dependency lock, retaining MCP SDK 2.3.0 and the independently usable standalone
  plugin. Assessed optional Tasks/notifications, Skills, protocol relay, Apps,
  OAuth and Redis deployment profiles without enabling them. The app-owned host
  retains one owning process and the current review/recovery contracts.

- Aligned source/packages with shared framework 0.16.0 and MCP SDK 2.3.0. Exposed
  structural argument depth/node budgets in the GUI and preserved them across
  publication. Optional Tasks/Skills remain unregistered; existing jobs, approval
  and recovery contracts are unchanged. Installed integrations are not migrated.

- Aligned source/packages with shared framework 0.15.0 while retaining MCP SDK
  2.3.0. Assessed modern frontends with the existing reviewed FANUC backend;
  require its legacy back-channel and reject unsupported downstream modern/watch
  profiles before startup or publication. Normalized policy revision hashes so
  numeric settings survive republishing. Existing standalone installations are
  not migrated by source updates.

- Removed Universal Tools broker registration, adapter entrypoints, routed skill
  guidance and broker policy GUI pages. Setup supports only the standalone
  plugin, direct MCP or runtime-only installation. Kept the shared framework and
  app-owned host with internal discovery metadata and host policy controls.
  Legacy broker runtimes are preserved for explicit reconciliation.

- Made offline setup install the app's standalone Codex plugin by default, with
  no separate routing plugin requirement. Direct MCP registration and runtime-only
  setup remain explicit choices.

- Upgraded source/packages to MCP SDK 2.3.0 and shared framework 0.14.0, retaining
  the existing 48-tool standalone contract and standalone plugin. Added an explicit
  experimental app-owned host with versioned backend publications, verified app
  deployment files and policy, GUI host policy controls and a fixed discovery
  front door. Existing installed integrations are not migrated.

- Added an operator-selectable `all_supported` write scope for broad local access
  without exact write lists. Exposed it in the GUI and current policy metadata;
  public defaults remain allowlisted. Preserved read roots, provider selection,
  supported write classes, proposal approval, preflight and readback.

- Added a complete GUI editor for robot access policies and app-owned host
  permissions/settings, including online-write targets, approval mode, plan
  expiry, transfer/concurrency limits and HTTP/SNPX read scopes. Added validated
  review, stale-file protection, original-policy backups and stopped-host
  administrative locking. Existing sessions retain their loaded permissions.

- Added bounded concurrent MCP/shared-API requests, a reserved bookkeeping lane,
  configurable controller-lock waiting and request status through the existing
  operation catalog. Independent controllers can overlap while each write keeps
  its lease through preflight/readback. Coordinated audit access across processes
  and preserved the 48 permanent MCP tool schemas and existing feature releases.
- Required successful offline validation status before GUI deployment preparation,
  including when a failed report contains matching source, TP and readback hashes.
- Included the program diagnostics guide in offline plugin bundles.
- Fixed offline compiler jobs to keep WinOLPC staging output separate from
  preserved inputs and final readback. Added selected-profile translator checks,
  component hashes and actionable reports when the native toolchain is incomplete.
- Added the Program diagnostics GUI and opt-in triggered native capture bundles
  with exact LS revisions read after triggers, timestamps, hashes and explicit
  partial/error/gap evidence. Added optional offline-report checks before GUI
  deployment preparation and a self-contained programs 1.1.0 feature with six
  reloadable read analyses. Preserved the original release and 48 MCP schemas.
- Added categorized semantic LS comparisons, transitive explicit dependency
  inventories, hash-bound local change packages and an offline program-tools CLI.
  Added isolated MakeTP/PrintTP jobs with copied robot contexts, explicit versions,
  bounded diagnostics and preserved normalized readback checks. No controller
  load or deployment authorization follows from these local checks.
- Added shared LS instruction/access tracing, explicit indirect-reference evidence,
  conservative branch edges, revision-bound sampled WAIT evaluation and bounded
  pre/post diagnostic capture models. Unsupported syntax and unavailable/stale
  observations remain unresolved; these services do not contact a controller.
- Added a separate offline desktop recording viewer with paged samples/errors,
  gap-aware trends, supplied timeline/cycle/pause history, metadata and file hash.
  Its shared Qt-free reader validates bounded versioned JSON; opening captures
  preserves active collection and connection settings. Viewer columns/tabs have
  separate local preferences. The stable MCP host and tool schemas are unchanged.
- Split desktop trend lines at unavailable values and pauses, rebased timeline
  comparisons after gaps, and retained explicit unavailable/paused cycle periods
  plus pause/sequence metadata in recording exports.
- Added source/watch-bound desktop recordings, explicit new-recording reset,
  complete atomic JSON export and saved watch drafts. Late workspace results,
  changed-source collection, duplicate watch keys and invalid/backwards samples
  are rejected; recordings stop at 10,000 samples while retaining captured data.
- Fixed Fleet jobs to update the same records written to local fleet history and
  leave the scheduler available to retry after a setup/state-save failure.
- Added staged connection management, explicit new-profile creation, validated
  unique names and retained custom ports/timeouts. Renames update the original
  entry and remembered identity. Active-target changes pause live file polling
  and discard previous-target completions; failed saves retain the old UI state.
- Bound Fleet rows/jobs to displayed connection settings, honored unchecked rows
  in scheduled backups, preserved choices on reload and deferred row replacement
  until active jobs finish. Added a shared offline profile catalog for validated
  edits, unique names, copies and remembered-connection migration.
- Saved connection profiles and desktop preferences now share flushed atomic JSON
  replacement, preserving the previous saved file when serialization or I/O fails.
- Added desktop layout reset and a saved horizontal dependency-view divider.
  Preserved intended wide default columns, including Live / Recorder's 520-pixel
  value column, instead of shrinking them during layout setup.
- Added local rotating desktop startup/error logs, visible failure reporting for
  console-free launches, and Help menu entries for logs and version/settings
  details. Corrected the Python module version to match desktop package 2.1.0.
- Added the supplied application icon, a console-free Windows GUI launcher and
  per-user Start menu shortcut creation through the desktop installer, with
  explicit commands to refresh or remove the shortcut.
- Added CSV/L5X PLC tag import with scopes, aliases, UDT/member comments, catalog
  filtering, manual I/O assignment and reviewed explicit-reference suggestions.
  Original exports remain intact; assignments are staged until the local map is saved.
- Added a GUI policy authoring form with read-only defaults, exact write targets,
  offline validation, JSON review, exclusive file creation and remembered policy
  paths. Creating a policy never connects or elevates a loaded controller session.
- Added backup discovery status and file-based percentage progress through the
  shared service and GUI, with retained failure/warning counts and manifest-first
  completion reporting.
- Added local desktop persistence for successful robot/profile selection, table
  widths, draggable pane sizes, and tab order/visibility. Live PR axes wrap into
  readable rows with full-value tooltips; watch rows can be added or removed.
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
