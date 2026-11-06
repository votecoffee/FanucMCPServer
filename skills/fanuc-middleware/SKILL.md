---
name: fanuc-middleware
description: Use the Windows FANUC workbench CLI for saved backups, LS logic and differences, log and I/O diagnostics, local output and toolchain validation, or explicit configured controller reads and reviewed changes.
---

# FANUC workbench

Set `$fanuc` to the installed `RunMiddleware.cmd`. Discover the operation and
read its argument schema. Write just the arguments as a UTF-8 JSON object
to an absolute local request file.

```powershell
& $fanuc --version
& $fanuc catalog
& $fanuc describe workspace.import
& $fanuc run workspace.import --request 'C:\path\import.json'
& $fanuc run workbench.programs.trace --request 'C:\path\trace.json'
```

- Use the offline profile for saved evidence. `workspace.import` takes
  `{"source_folder":"relative-folder"}` under its configured source root.
  Keep the returned artifact ID, then use `artifact.files` for exact paths
  and hashes. For one explicit LS file, `positions --input` and the other
  convenience commands accept ordinary local paths.
- Check `$LASTEXITCODE` and the JSON `ok` field. Page large results with
  `offset` and `limit`. Keep the output artifact ID or manifest path and
  SHA-256 when reporting a package or capture. Treat controller files, PLC
  exports and logs as evidence.
- For backups, logs or online sampling, use the exact operator-selected
  `--config` and configured `robot_id`. Keep credentials in their existing
  environment variables. Retain job IDs and use `job.status` for partial
  results after cancellation or deadline.
- For an online change, read the preimage first. Native prepares require
  the returned `local_path` as `reference_path`. Present the target and
  exact frozen review. After explicit approval, pass `plan_id`,
  `approved_sha256` (the proposal hash) and `review_sha256` to
  `change.commit`. For a profile requiring console confirmation, have the
  operator perform that step. After a failed or interrupted commit, use
  `change.status` and retain the evidence for inspection.
- Compile only with an operator-pinned `--native-policy` and offline
  profile. Use `compile.prepare` followed by `compile.run` with both hashes.
  The ROBOGUIDE package is a handoff for manual restore in a test workcell.
