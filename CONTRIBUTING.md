# Contributing

Questions, bug reports and pull requests are welcome through the
[GitHub repository](https://github.com/votecoffee/FanucMCPServer). For bugs, include
the Python version, relevant package versions, reproduction steps and a minimal
synthetic example. Review reports and attachments before posting publicly.

## Development

Use Python 3.11 or newer. From the repository root on Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install C:/Packages/universal_codex_tools-0.18.0-py3-none-any.whl
.\.venv\Scripts\python.exe -m pip install -c packaging/framework-requirements.txt -e ".[dev,mcp]"
.\.venv\Scripts\python.exe -B -m pytest -q
```

Use the verified local framework wheel and tested dependency constraints in
[the framework guide](docs/APP_FRAMEWORK.md). No separate routing plugin is
required, and development must use an isolated environment.

Tests use synthetic files, simulated controller transports and local MCP
sessions. Running the suite does not establish controller or commissioning
acceptance. Add focused regression coverage for behavior changes and update the
README, relevant guides and changelog when public behavior changes.

## Public repository hygiene

Keep reusable code and general documentation in Git. Put private work outside the
checkout or in its ignored `.local/` directory. For example:

```text
.local/
  robot_bridge.local.json
  notes/
  reports/
  captures/
```

Configuration paths resolve relative to the configuration file, so state and
source directories configured in `.local/robot_bridge.local.json` also stay there
when relative paths are used. The existing `robot-evidence/`, `robot-edits/` and
`.robot-bridge/` locations remain ignored for compatibility.

Do not commit credentials, private addresses or robot identifiers, personal paths,
chat transcripts, customer programs/backups, captured logs, local test reports,
vendor binaries, virtual environments, bytecode or generated bundles. Use reserved
documentation addresses such as `192.0.2.1`, generic paths and synthetic fixtures
in examples. An ignore rule does not remove a file that is already tracked.

Before committing, inspect the complete staged diff, including new files:

```powershell
git status --short
git diff --cached --stat
git diff --cached
```

Only commit files intended for public distribution. Avoid broad staging commands
when local controller work is present. Never use `git add -f` for private files.

## Controller changes

Preserve original bytes and hashes, keep write targets explicit, and retain the
review and readback workflow. Keep controller tests separate from the ordinary
offline suite. Record machine-specific endpoints, approvals and captures locally;
public documentation should describe the procedure and its limits.
