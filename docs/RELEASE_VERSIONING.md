# Release versions

The app, desktop and headless Python distributions, MCP server information and
standalone Codex plugin share one fixed release timestamp. The current source
checkpoint is **26.1022.173617**, selected in **America/New_York**. It identifies
source/package bytes; it does not attest to installation or native acceptance.
Framework, MCP SDK/protocol, file schema and feature-extension versions are
independent and are not restamped with the application.

## Display and package identities

The human display uses `YY.MMDD.HHmmss`, including all leading zeros. Python
PEP 440 and strict SemVer package/plugin identities use the same three numeric
components with leading zeros removed. For example, display `26.0102.030405`
has package/plugin identity `26.102.30405`. Compare numeric identities rather
than padded strings. The current timestamp has the same display and canonical
identity: `26.1022.173617`.

`fanuc_workbench/version.py` holds the chosen timestamp and the normalization
rules. Calendar/time validation includes leap years and rejects impossible dates
or times. Years represent 2000 through 2099. `__version__`, GUI About/application
metadata, CLI `--version`, MCP server information and manager app/plugin display
retain the padded value. Distribution metadata, wheel filenames and the Codex
plugin/cache version use the canonical value. Manager installed and running
versions remain distinct; reading newly installed metadata never changes the
running source version. Unrecognized historical versions remain visible.

Windows assembly/file fields have bounded numeric components. If a Windows
resource or .NET wrapper is built, retain the three-part requested version for
Product/Informational/package metadata and map file/assembly fields to
`YY.MMDD.HHmm.ss`. The example becomes `26.102.304.5`; the current mapping is
`26.1022.1736.17`. Every field fits a 16-bit unsigned integer. This Python app
does not currently produce a .NET assembly or rewrite Python's executable.

## Stamp once, then verify and build

Choose one New York timestamp for a source/release checkpoint. A release
operator can capture it once in PowerShell on Windows:

```powershell
$releaseStamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, 'Eastern Standard Time').ToString('yy.MMdd.HHmmss')
python -B scripts/stamp_release.py --version $releaseStamp
```

The Windows zone ID above observes New York's daylight-saving rules. The stamp
command requires the selected padded value and updates the fixed source value,
both distribution versions and plugin manifest together. It never selects the
time itself. Builds and process startup never run this command or read the clock
to create a version. Review the change on `main`, update the current checkpoint
in this guide and README, then run focused version/package checks before commit.

The bundle builder refuses mismatched source/distribution/plugin identities and
verifies the built bridge's actual wheel metadata. Bundle manifests, installer
pointers/markers and doctor output carry the same release identity. Setup uses
semantic version comparison for the Codex installation response and verifies
the installed doctor's loaded app/package identity. Keep the reviewed framework
wheel hash and SDK pins unchanged unless separately reviewed.

Use a fresh output directory named with this checkpoint. Preserve earlier
artifacts and receipts; record old-to-new mappings and acceptance evidence in
ignored local storage. Build both distributions from fresh staging with installed
`build`, setuptools and wheel tooling, without installing app dependencies:

```powershell
python -B scripts/build_app_wheels.py --output C:/Packages/Fanuc-26.1022.173617
```

The helper preserves its staging directories and refuses an existing output. It
checks actual wheel versions and source bytes, preventing deleted modules left
in an older `build/` directory from reappearing. The plugin bundle builder also
uses a fresh source stage. Packaging and metadata imports do not prove a live MCP,
native controller or Codex connection. Follow the
[launch-path acceptance requirements](RESOURCE_OWNERSHIP.md) before installation
or activation where rollout is held. Keep the app's own plugin and native skills;
no universal plugin is required.
