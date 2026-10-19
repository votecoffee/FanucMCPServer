# Reloadable VQLOG analysis

The `workbench.vqlog` extension analyzes preserved FANUC Visual Line Tracking
VQLOG text files. Its event formats are adapted from
[ONE Robotics vqlog.js](https://github.com/onerobotics/vqlog.js), with the upstream
MIT notice retained. It uses only Python's standard library and makes no
controller connection or write.

## Stage and reload

Use the runtime's interpreter and the same local configuration as the MCP host:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install-builtin vqlog
```

This stages a hash-pinned release and selects it in the local registry. It does
not activate code. A new registry also retains the standard workspace analyses;
existing registries retain their other selected releases. Then call
`reload_extensions` in the MCP session and describe `workbench.vqlog.analyze`.
The permanent MCP tools and host protocol are unchanged. An existing host with
extension API 1 can also stage the self-contained release through its existing
`install` command using a copied `extensions/vqlog` release directory.

## Analyze retained evidence

Import a workspace containing the log, or use a captured artifact with the file
in its manifest. Pass its artifact ID and manifest-relative path through
`run_read_operation`, using the exact revision returned by `describe_operation`:

```json
{
  "artifact_id": "<saved-artifact-id>",
  "path": "VQLOG.TXT",
  "event_type": "Skip",
  "area_id": 1,
  "offset": 0,
  "limit": 100,
  "tick_seconds": 0.002
}
```

Optional filters are `event_type`, `area_id` and `work_id`. Supported event types
are Acknowledgement, Addition, Allocation, Clear, LoadBalance, Overlap, Request,
SetLBD, Skip and Timeout, plus Unknown, Malformed and Empty. Unsupported event
formats and malformed records remain visible rather than being silently dropped.
Wildcard request fields are `null`, not zero or NaN. Skip events retain model ID
and optional signed position/boundary values.

Results include source-byte SHA-256, encoding, total/matching records, paginated
events, event counts, unknown/malformed counts, tick regressions and duration.
The file is never edited. Limits are 16 MiB, 50000 source lines, 4096 characters
per line and 100 events per output page.

## Timing limits

The default tick period is **0.002 seconds**, following the upstream convention.
It is an explicit analysis assumption, not firmware detection or a verified
sampling period. Set `tick_seconds` for the captured source and check the
returned `tick_period_verified: false`. Clock regressions or invalid timestamps
make elapsed times/duration unavailable; the analyzer does not guess wraparound
or synchronize with PLC/vision clocks. These are offline event observations,
not causal or commissioning conclusions.

## Shared Python API

```python
from pathlib import Path
from fanuc_workbench.services.vqlog import analyze_file

result = analyze_file(Path("VQLOG.TXT"), event_type="Timeout", tick_seconds=0.002)
```

The release contains its parser and MIT license so compatible updates can be
reloaded independently of the host package. Keep release versions immutable.
