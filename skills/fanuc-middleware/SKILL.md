---
name: fanuc-middleware
description: Inspect saved FANUC LS programs on Windows, trace logic and I/O, compare revisions, and publish local dependency-reviewed packages through the FANUC CLI.
---

# FANUC saved program review

Use the installed `RunMiddleware.cmd` or the launcher at the root of the
FanucMCPServer checkout. Start with user-selected saved inputs and use an
absolute local workspace path. Select individual files relative to that
workspace using forward slashes.

```powershell
& $fanuc --version
& $fanuc positions --input 'C:\path\backup\MAIN.LS' --limit 25
& $fanuc trace --workspace 'C:\path\backup' --path MAIN.LS --view signals
& $fanuc compare --workspace 'C:\path\revisions' --before old/MAIN.LS --after new/MAIN.LS
$selection = @('--workspace', 'C:\path\backup', '--path', 'MAIN.LS', '--path', 'CHILD.LS', '--entry', 'MAIN')
& $fanuc dependencies @selection --view requirements
& $fanuc package @selection --destination 'C:\path\review'
```

- Set `$fanuc` to the launcher's full path. Check `$LASTEXITCODE` and the JSON
  `ok` or `error` field. Use `next_offset` with `--offset` for another page and
  check that the source hashes match between pages.
- For dependency gaps, use `--view missing` and `--view issues`. An explicitly
  supplied `--inventory` is evidence from the saved workspace. Ask before
  choosing `--allow-incomplete` for a requested complete package.
- Publish only to an agreed new directory under an existing parent. The package
  command reviews the current sources again. Retain its manifest path and
  SHA-256, and compare the manifest's source hashes with the reviewed results.
  After an interruption, inspect the destination before retrying.
- Treat results as saved evidence. Package receipts require separate native
  validation and provide no authorization to deploy.
