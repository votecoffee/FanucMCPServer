# Recording evidence bundles

`core.recordings.bundle_evidence` copies a saved recording and a selected range
of its referenced sample artifacts into a new local artifact. It opens no robot
connection and works with controller writes disabled. Original artifacts stay
unchanged. The host owns reference resolution; feature workers receive only the
resulting saved bundle.

## Create a bundle

Describe `core.recordings.bundle_evidence`, then use `run_read_operation` with
its exact returned revision and these arguments:

```json
{
  "artifact_id": "<recording-artifact-id>",
  "sample_offset": 0,
  "sample_limit": 20
}
```

This core operation needs a server runtime containing the bundle builder.
Updating feature workers alone cannot add it to an older host. The permanent 48
MCP tool schemas remain unchanged; upgrading/reconnecting the server is separate
from reloading feature code. No runtime is installed or restarted by this command.

Supply a retained artifact of kind `recording`, including a partial recording
preserved by a failed or cancelled watch. Each recorded sample must have one
distinct, valid sample artifact reference. Selected references must exist, have
kind `sample`, and name the same configured robot as the recording. Unknown IDs,
changed file sets, changed bytes, ambiguous capture files and exceeded limits
abort creation without registering a bundle. Copying rechecks exact byte lengths
and hashes, including changes after preflight. Failed creation can leave an
unregistered local output directory for inspection.

The result supplies an artifact ID, `path: "bundle.json"`, exact file/byte counts
and `selection` pagination. `sample_offset` is nonnegative; `sample_limit` is
1–100 and defaults to 20. `selection.total` counts samples in the full recording;
`count` describes copied samples, and `next_offset` selects the next range. An
offset beyond the recording creates an explicit empty selection.

## Contents and limits

The bundle contains the exact original `recording.json` under `recording/`,
the complete file set of each selected sample under `samples/<index>/`, and a
`bundle.json` manifest. Original CSV/cycle exports from the recording are not
copied. Sample folders preserve file captures, native captures or SNPX captures
and wire traces, according to their original provider. Sample records use the
original zero-based recording index and artifact ID.

The manifest binds each copied path, original sample-relative path, byte count
and SHA-256. The new artifact catalog also hashes every bundle file. The builder
checks existing source manifests; these are local consistency checks, not capture
authentication, physical robot identity, mapping validation or proof that a watch
completed. The bundle can contain sensitive controller data; keep it in private
state or ignored local storage and review it before sharing.

Limits are 8 MiB for recording JSON, 4 MiB per sample file, 1 MiB for the bundle
manifest, 32 MiB total including the manifest, and 1000 files including the
recording and manifest. Configured `max_file_bytes`, `max_backup_bytes` and
`max_backup_files` can lower these limits. Select fewer samples when a bundle
exceeds a limit. Creating a bundle does not establish that recorded values match
their referenced sample contents or wire replies; those require offline analysis.

The shared Python entry point uses the same policy and artifact store:

```python
from pathlib import Path
from fanuc_workbench.bridge.service import RobotBridge

with RobotBridge.from_config(Path(".local/robot_bridge.local.json")) as bridge:
    result = bridge.operations.bundle_recording("<recording-artifact-id>", sample_limit=20)
```
