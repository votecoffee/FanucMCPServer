# Stable MCP host and reloadable features

The MCP connection and its tool schemas stay fixed while versioned feature
workers change behind them. Existing named tools remain supported. New features
are discovered through `list_operations` and `describe_operation`, then invoked
through `run_read_operation` or `prepare_change`. `commit_change` retains the
core confirmation and readback workflow. `reload_extensions` takes no paths or
code from chat; it reads an operator-managed registry under bridge state.

## Boundaries

- The host owns configured robots, credentials, path limits, immutable plans,
  confirmation, audit, jobs and controller readback.
- Extensions provide offline analysis or compute proposals. They do not receive
  controller credentials. They are trusted local Python code, not a security
  sandbox; process separation provides lifecycle and failure isolation.
- Read operations cannot dispatch prepared changes. Extension proposals support
  only the existing file replacement/storage creation and description contracts.
  New native controller write classes require a core implementation and policy.
- Every operation has a namespaced ID, description, effect, input/output JSON
  schemas and exact release revision. Generic calls must supply that revision.
- The existing GUI continues using the shared services directly.

## Releases and reload

An extension directory contains `extension.json`, its entrypoint and manifest-
listed support files. The manifest identifies its extension/version, host API,
entrypoint, operation metadata and SHA-256 of every support file. The registry
pins the manifest hash and optionally a separate local Python interpreter.
Remote schemas, links, external paths and unlisted files are refused.

The local installer validates and stages a release without overwriting existing
code. It atomically selects a registry revision; it does not contact a robot or
activate code in an already-running host. The bundled analysis release supplies
the initial operation catalog when no registry exists.

On `reload_extensions`, the host validates the complete candidate registry,
copies exact release bytes to private generation snapshots, starts new workers,
and checks their protocol and optional self-tests before switching. Any failure
keeps the active workers and prepared plans unchanged. A successful changed
registry is audited before activation and invalidates pending file/comment plans.
Core robot operations and queued/running read jobs prevent the switch.

Already-running feature reads finish on their original worker. New calls use
the new revision. An old worker is closed after its final call. The host process,
MCP connection and advertised tool schemas remain unchanged. A no-op reload
preserves workers and plans. Evidence and prior releases remain preserved.

## Worker contract

The worker bootstrap uses bounded JSON lines over private stdin/stdout. It loads
one captured release and exposes `handle(operation_id, arguments, context)`;
an optional `self_test()` runs before activation. Standard output from extension
code is redirected away from the protocol. Inputs, outputs and wait times are
bounded. Worker errors return sanitized failure types; calls are never retried
automatically. Workers have their own process lifetime and minimal environment.

Workspace contexts contain only host-verified saved artifact paths. File/comment
proposal contexts contain a preserved read selected by robot/path/hash. The host
validates worker output, binds proposals to the caller's exact target, and calls
its existing preparation services. Approval never executes an extension callback:
it commits the frozen core proposal after client confirmation and preflight.

## Compatibility

This is host protocol version 1. Changing extension operations within this
contract does not require Codex tool discovery. Changing the permanent MCP
schemas, host protocol, plugin metadata or skills still requires the appropriate
client/plugin update. The existing 0.6/0.7 host must be updated once to acquire
this extension interface. No Codex restart is performed by extension reload.

## Operator commands

The 0.8 host advertises 48 permanent MCP tools. Its bundled release moves all
20 workspace analyses into the feature worker. The remaining controller,
storage and approval services stay in the host. `analyze_workspace` preserves
its existing arguments/results and delegates to that release.

Use the headless runtime's interpreter for the local staging command:

```powershell
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json list
python -B -m fanuc_workbench.bridge.extensions_cli --config C:/RobotTools/bridge.json install C:/RobotTools/MyFeature/1.1.0
```

Then ask the chat to call `reload_extensions`, followed by `list_operations`
and `describe_operation`. New operations appear in this catalog during the
same MCP session. No plugin reinstall, tool-list refresh or Codex restart is
needed for features compatible with host API 1. Reinstalling a previously
preserved release selects that version for the next reload, providing rollback.
Keep versions immutable; changed bytes require a new version.

The bundled optional [VQLOG feature](VQLOG_ANALYSIS.md) can be staged with
`install-builtin vqlog` using the same CLI/configuration, then activated through
`reload_extensions`. It adds `workbench.vqlog.analyze` to the operation catalog
without changing the 48 permanent tools. It is self-contained and read-only.

`install ... --python C:/RobotTools/FeatureVenv/Scripts/python.exe` selects an
independent interpreter for that release's dependencies. Install dependencies
into that dedicated environment before staging; neither staging nor reload runs
pip. This keeps feature dependency updates out of the host environment.

Use `fanuc_workbench/extensions/analysis/extension.json` as a complete manifest
example. A small read-only handler looks like:

```python
def self_test():
    return True

def handle(operation_id, arguments, context):
    return {"message": arguments["message"]}
```

Give it a namespaced ID such as `cell.report`, a new release version and a
`cell.report.echo` operation with `effect: "read"`. Declare object JSON Schemas
for input/output and SHA-256 hashes of exact support-file bytes. Contexts can
bind required artifact IDs to verified workspace/recording/snapshot paths.
The handler receives no bridge object, controller connection or credentials.
Prepared operations use `effect: "prepare"` and one of `file_replace`,
`file_create`, or `comment_update` as `proposal_kind`. File proposals return
only `data_base64` and `sha256`; description proposals return only `comment`.
The host fixes robot/path/target from the validated request and freezes the
result in its existing prepared plan. For replacement, required arguments are
`robot_id`, `path`, `expected_sha256`; creation omits the hash. Description
preparation requires `robot_id`, `listing_path`, `target`, `expected_sha256`.

Requests are limited to 1 MiB including their envelope; responses to 4 MiB.
Worker startup/self-test is bounded at 15 seconds and each call at 30 seconds.
A crashed/timed-out worker remains unavailable until explicit reload. Reloading
the same selected release recovers an unhealthy worker and invalidates plans.
An unchanged healthy selection keeps its PID and pending plans.

Release snapshots and prior versions remain in state for inspection. Remove
obsolete versions only while the host is stopped and after preserving needed
evidence. Extension execution is trusted local code; minimal environment and
separate processes do not prevent it from accessing local OS resources.

Codex separately documents `config/mcpServer/reload` for refreshing its MCP
configuration. That client mechanism is separate from feature-worker reload:
https://learn.chatgpt.com/docs/app-server
