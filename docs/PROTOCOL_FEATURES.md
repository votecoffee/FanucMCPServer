# MCP protocol feature status

The standalone plugin remains the default route, using MCP SDK 2.3.0 and
`universal-codex-tools==0.18.0`. The optional app-owned host has one native-owning
backend. It does not automatically relay extension protocols or switch routes.
The read-only `capabilities` tool and doctor output include `protocol_features`.

## Current source profile

The original 48 tool schemas and three workflow readers remain available. Four
additional job tools bring the source catalog to 55 tools. Their exposure does
not grant new controller write classes or authorize an extension negotiation.

| Feature | Default startup | Validation status and boundary |
| --- | --- | --- |
| Ordinary workflow readers and canonical Skills catalog | Enabled | Established byte/hash/revision coverage; native host skill activation is separate. |
| Exclusive worker lease | Required | Source implementation and regression cases; process acceptance pending. One worker per policy state directory, held through drain. |
| `wait_job` progress and request cancellation | Available | End-to-end validation pending. Progress counts actual reported stages; structured completed/total units retain their real meaning. Cancellation requests a cooperative stop between operations. |
| Explicit backup/recording Tasks | Disabled | Opt-in embedding preview only. Owner, duplicate, lost-start, cancellation and notification acceptance remains pending. A previously observed ownership-test failure must be resolved before enablement. |
| Modern tool discovery cache | Disabled | Opt-in embedding preview only: connection-private hints with a 2-second TTL. Measurements and notification-driven invalidation acceptance pending. Invocation still checks current schemas, policies and revisions. |
| Artifact resource views | Disabled | Opt-in embedding preview only. Scope/hash/range, notification and reconnect acceptance pending; ordinary readers remain available. |
| Modern downstream approval/discovery profile | Blocked | Resolver-based handlers are staged. Legacy downstream publication guard remains until positive and negative continuation acceptance passes. |
| Redis replicas, remote OAuth/DPoP and MCP Apps | Disabled | Outside the current app workflow; no automatic installation or activation. |

The embedding-only `create_server(..., protocol_preview=True)` selects candidate
extensions for isolated acceptance. Normal CLI/plugin startup does not set it.
Tasks additionally require an app-selected trusted `frontend_identity`, modern
protocol negotiation and explicit client Task support. A model argument or
`clientInfo` value cannot select that identity or enable the preview.

## Job and artifact contracts

Task admission records the operation before dispatch and derives a stable app
job ID from the Task ID. Exact duplicate operation keys return the existing Task;
changed arguments reject. An uncertain start is reconciled against admitted job
evidence and is never replayed automatically. Job cancellation is acknowledged
as a request while an in-flight transfer continues; it is not rollback.

Durable records surviving a restart do not establish that a worker survived.
The standalone identity is process-scoped. A replacement process cannot claim
another process's Tasks by knowing their IDs. Reconnection with a retained owner
requires a trusted embedding and app reconciliation. Generic app-host tool
routing does not forward Tasks.

Artifact resources expose existing app-local captures only, with manifest
revision, file SHA-256 and an explicit byte range. They do not acquire controller
data. The catalog is bounded, and an oversized view directs clients to paginated
ordinary readers. Its scope is the configured local state directory, not a
multi-tenant remote authorization service. Captures describe preserved bytes,
not current robot state. Notifications are refetch hints, without replay or
guaranteed delivery; every read verifies its identity and bytes again.

## Release acceptance

Before enabling the preview, verify modern/legacy negotiation, cache isolation
and measured invalidation, bounded notification lifetime, real job progress,
owner revocation, duplicate and ambiguous admission, cancellation and shutdown.
Approval coverage must include accept, false, decline, cancel, invalid answers,
unsupported clients, expiry, foreign continuations and duplicate submissions.
Preserve exact proposal/hash, preflight, readback and single-use core checks.

Use only disposable zero-robot policies, synthetic transports, isolated
registries and owned process handles. Full tests, source/wheel parity, RECORD,
dependency checks and isolated installed acceptance precede release. Never infer
native controller behavior or host UI acceptance from protocol/package evidence.
