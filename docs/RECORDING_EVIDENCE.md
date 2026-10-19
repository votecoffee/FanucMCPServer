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
their referenced sample contents or wire replies; use the offline analyzer below.

## Inspect a bundle

The optional `workbench.recording` 1.1.0 feature adds
`workbench.recording.inspect_bundle`. Stage it with `install-builtin recording`,
then call `reload_extensions`. A host API 1 runtime with an older installer can
stage the self-contained `fanuc_workbench/extensions/recording_v1_1_0` directory
through its existing `install` command. The original 1.0.0 release remains in
`extensions/recording` for rollback and keeps its unchanged summary operation.

The analyzer requires no host update beyond host API 1. Creating bundles through
the core catalog requires the updated host described above. A compatible bundle
created elsewhere can be imported through `import_workspace` on an older host,
then inspected with the imported artifact ID. Copy/import the entire bundle
folder: `bundle.json` must be directly inside the artifact root and the file set
must match the manifest exactly. Paths cannot leave that root or use links.

Describe `workbench.recording.inspect_bundle`, then use `run_read_operation` with
its exact revision:

```json
{
  "artifact_id": "<bundle-artifact-id>",
  "path": "bundle.json",
  "offset": 0,
  "limit": 20
}
```

The analyzer rechecks every declared file's exact size and SHA-256, normalized
paths, unique file set, selection metadata, sample indices and referenced IDs.
Missing or changed files and malformed bundle structure refuse analysis. Each
selected capture's timestamp, values and errors are compared with its original
recording row. Native values use the same R/PR/I/O projection as watch creation;
file and SNPX samples use their recorded values directly. Comparison preserves
JSON types, including `true` versus `1`. Malformed captures and mismatched rows
produce explicit per-sample issues rather than available zeros.

For identified SNPX samples, the feature uses the retained
[wire inspector](SNPX_EVIDENCE.md) to check capture/wire hash binding and the
supported handshake/reply contract. A missing trace, malformed evidence or failed
protocol check remains explicit. Unknown providers with retained wire data also
prevent the aggregate trace check from passing. Named I/O values are not bound
to individual wire payloads: `io_value_binding_checked` and `mapping_verified`
remain false. Matching a recording row to `sample.json` does not close that gap.

`bundle_hashes_verified` describes consistency against the supplied bundle
manifest. `source_manifest_claim_verified` is false: the portable analyzer cannot
authenticate the original catalog or the builder's declaration that it checked
source manifests. Hashes cannot authenticate a bundle whose manifest and data
were both replaced. Capture provenance, physical identity and recording
completion remain unverified.

`all_selected_samples_match_recording` covers row/capture equality.
`all_selected_snpx_traces_valid` covers the selected SNPX/unknown-trace rows;
it is null when none exist. `all_selected_evidence_consistent` requires no
per-sample issues. Empty selections return null aggregate checks. Provider and
issue counts summarize all selected samples. Analysis pagination affects only
`items`; `selection` describes the range copied into this bundle, and
`selection_covers_recording` distinguishes a complete reference range from a
partial bundle. It does not assert watch completion.

The analyzer uses the same fixed byte/file limits as the builder. It accepts
at most 100 selected samples and pages of 1–100 rows. It opens no socket and
modifies no source files. The original summary/bundle code is MIT licensed;
the retained wire adaptation includes its Apache-2.0 license and notice inside
the self-contained feature.

The shared Python entry point uses the same policy and artifact store:

```python
from pathlib import Path
from fanuc_workbench.bridge.service import RobotBridge

with RobotBridge.from_config(Path(".local/robot_bridge.local.json")) as bridge:
    result = bridge.operations.bundle_recording("<recording-artifact-id>", sample_limit=20)
```

Local offline inspection needs only the saved folder and Python's standard
library:

```python
from fanuc_workbench.services.recording_analysis import inspect_bundle

report = inspect_bundle(Path("private-evidence/bundle.json"))
```
