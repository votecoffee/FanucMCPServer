# Codex config publication

Bundle setup delegates plugin/marketplace or direct MCP registration to the Codex
CLI. It does not serialize `config.toml` in Python. `--codex-home` selects an
explicit separate profile; `--register none` avoids registration entirely. Do
not run installation/registration against a live profile during source or fixture
testing. Documentation changes require no reinstall or restart.

For local automation that edits FANUC's two plugin/server loader flags, use
`fanuc_workbench.bridge.codex_config.plugin_flags_bytes` for preparation and
`set_plugin_flags` only with an explicitly selected file and reviewed SHA-256.
This helper is not an MCP tool and has no default config path. It changes only
the app's two enabled flags, keeps comments and unrelated parsed settings, and
refuses unsupported source layouts rather than reserializing the entire file.
Table-looking text inside multiline strings is data.

The writer validates UTF-8 TOML before preparing a change. Windows output uses
explicit CRLF; existing CRLF remains CRLF on other systems. LF input is converted
once on Windows. Encoding occurs once and the temporary file is opened in binary
mode, so Windows text translation cannot turn CRLF into CRCRLF. Parsed values are
compared before replacement, including Unicode and multiline values. Lone CR and
CRCRLF input are refused without automatic repair. Retain an external backup
before a reviewed publication.

The target is checked for stale/concurrent bytes before atomic replacement;
failures preserve the target and remove only the helper's temporary file. This
is not a distributed configuration lock or authorization to change a live
profile. A hash binds reviewed bytes, not human approval.

Run the disposable checks without contacting a controller:

```powershell
python -B -m pytest -q tests/test_codex_config.py tests/test_root_installers.py
```

The tests cover LF/CRLF inputs, exact raw endings, UTF-8 Unicode/basic and literal
multiline values, unrelated settings, missing sections, stale/concurrent edits,
write failures and malformed input. Installer tests use temporary CMD/Python
peers and inert install-call spies. They do not install a plugin or prove native
or Codex-host behavior. Historical local publication packets are evidence; do
not rerun old copied scripts as a current installer.
