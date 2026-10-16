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

Codex separately documents `config/mcpServer/reload` for refreshing its MCP
configuration. That client mechanism is separate from feature-worker reload:
https://learn.chatgpt.com/docs/app-server
