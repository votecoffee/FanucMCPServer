# Launched resources and teardown

This document distinguishes implemented source safeguards from remaining
launch-path acceptance. The source candidate pins shared framework 0.20.0 and
uses its `ResourceLedger`/`ResourceOwner` for private RPC workers. The ledger
owner is the actual long-lived FANUC process, using the same selected registry
as management. The local worker inventory is only a bounded cache of those IDs;
it is not a second recovery database. Installation/process acceptance is pending.

## Worker source safeguards

The standalone and app-host backend use one exclusive worker lease per policy
state directory. A process-local inventory additionally admits at most 16 private
feature/native workers, including retiring generations and uncertain cleanup.
It retains failed owners and exact handles until cleanup is established or the
owning process exits. Status reads use cached bookkeeping, without process scans,
native attachment or controller queries.

RPC launch intent is durable before `Popen`. Both a venv launcher and its actual
Python bootstrap receive separate exact-identity records when their PIDs differ.
Binding must agree with the retained handle's creation time before activation.
Unbound startup failures retain reservations and a recovery barrier; no process
is guessed or adopted. The shared app-wide limit is 32 non-retired records across
supervisor generations, alongside the 16-worker process-local limit.

On Windows, an inert bootstrap reports its process creation FILETIME before
activation. The parent verifies that timestamp on the opened kernel handle
before assigning the worker to a private job. A reused PID fails verification
without assignment or termination. The exact handle stays retained through
teardown. Trusted feature code loads after containment; its subsequently launched
descendants, including shell and Node children, inherit that job's restrictions.
Job-close limits provide owner-death containment once attachment is established.
Each private job admits at most 32 processes, including shell/Node descendants.

Normal close first requests worker shutdown. Forced cleanup targets the private
job and waits for its active process count to reach zero. Failed termination or
readback retains ownership as uncertain. Startup failure before activation uses
the original `Popen` handle and closes the private IPC pipes; no `taskkill`,
process-name scan or remembered-PID termination is used. POSIX process-group
cleanup does not act on a group after its root has exited and ownership cannot
be established. Equivalent cross-platform owner-death containment is pending.

Generation references retain workers while requests use them. Retirement closes
the last leased generation; failures remain retained. Shutdown attempts cleanup
of every eligible native client/generation even when one fails. It does not
silently recreate a failed owner. A timed-out native write remains unverified,
with its consumed plan and preserved evidence; cleanup is not rollback.

Before dispatching a native register write, all its worker/launcher records gain
a durable recovery barrier. Only the native worker's verified readback SHA-256
resolves it. A failed or lost response retains the barrier and blocks another
write on that worker. Process-exit reconciliation attests only exit: it preserves
unresolved barriers and the domain's uncertain-write journal, without replay.

The FRRobot adapter distinguishes an injected object from an app-created
connection. Closing it releases only its own proxy reference. The worker releases
COM proxies on their owning apartment before uninitializing COM. It never issues
application Quit/Exit, saves user documents or closes attached software.

## Launch-path acceptance matrix

| Path | Ownership and current source behavior | Remaining release requirement |
| --- | --- | --- |
| Direct standalone offline | Explicit `--offline` dispatch allowlist, zero robots/writes, actual frontdoor identity audit and exclusive state lease; stable app-owned base Python serves in process, one-actor/512 MiB job, no child/native/extension routes; kernel generation and exact parent-loss receipts | Retain per-install stable-entry SDK discovery, reads, EOF/crash/client-loss, immutable update and identity evidence; desktop activation remains separate |
| Direct standalone local-native candidate | Separate `--local-native-policy` selection; saved-evidence tools plus five pinned local compilation tools; same zero-robot/write refusal; one single-use validation pipeline; atomic private utility jobs and retained uncertainty | Source/package and harmless fixture checks, followed by separately coordinated actual native compiler/foreground cleanup acceptance |
| Full installed plugin/plain MCP stdio | Installed-runtime supervisor, durable report-only launcher/worker records, retained handles, activation before MCP startup, opaque SDK streams, private job and EOF drain | Real startup/duplicate/EOF/owner-death, package and Codex acceptance |
| App-host backend | New publications use the same supervisor; explicit registry allowlist propagation and exclusive state-directory lease; unmanaged older publications refuse startup pending explicit republish | Real host/retained-worker lifecycle and registry acceptance |
| Feature/native RPC worker | Shared durable launch records, private Windows job, verified creation timestamp, retained handles, activation barrier, bounded inventory and uncertain-cleanup refusal | Real child-tree/owner-death and installed/native acceptance |
| Offline MakeTP/PrintTP | Common atomic suspended background launch; actual caller owns the job; activation after exact worker/job membership; durable compiler step intent and retained-handle identity; bounded file IPC/logs; one job/eight actors/512 MiB | Harmless child/owner-loss fixtures, then separately coordinated real compiler acceptance |
| GUI ROBOGUIDE launch | Automatic launch refuses until native application/workcell ownership is accepted; backup handoff remains available and preserves existing folders | Explicit native ownership/dirty-state acceptance before enabling automatic launch; user-opened sessions remain protected |
| Host-policy validation helper | Fixed metadata script in an owned activated worker using the selected interpreter; bounded IPC, durable records and redacted errors; never starts the host | Real selected-runtime/EOF/owner-death acceptance |
| Doctor | Explicit administrative SDK client with a private registry; exercises the same supervised stdio entrypoint; default performs no controller probe | Real installed package/EOF acceptance |
| Build/install helpers | Explicit administrative pip/CLI commands; not server-owned runtime/native resources | Package/registration checks in a disposable profile before installation |

Compiler utilities use the shared background resource launcher, with no inherited
stdio or job handle. Its memory limit is installed while the group is suspended,
before the common ledger binds and resumes it. Only fixed MakeTP/PrintTP paths
and executable hashes are accepted; no shell or arbitrary command route exists.
The parent retains the exact activated worker and confirms membership in its own
job before granting execution. Each compiler step journals launch intent before
creation and binds the OS identity from its retained process handle. Job cleanup
covers descendants and preserves bounded outputs and uncertain receipts. This
background protocol does not replace the client's MCP SDK stdio transport.

Policy helpers retain their separate RPC lifetime implementation. The GUI
refuses closure while private workers or uncertain cleanup remain. Do not install
or enable the candidate on the strength of stub tests alone. Existing user-owned
native software remains untouched while ownership is unestablished.

The [direct standalone offline profile](STANDALONE_OFFLINE.md) excludes those
worker/helper paths rather than treating their pending acceptance as evidence.
Its frontdoor is client-owned; it does not create a post-launch private child
claim for itself or enable manager PID termination. Its identity audit is a
durable lifecycle receipt, not a second resource-recovery database.

Stdio uses its own activation/pipe implementation because the framework's
background launcher does not inherit SDK streams. The activation reader uses
exact unbuffered bytes so the SDK's fd ownership cannot lose prefetched input.
No MCP message or tool argument is interpreted by the relay. Report-only ancestor
records prevent manager cleanup from terminating an MCP tree around an unresolved
native barrier; abnormal loss still leaves domain journals and exact resource
records for inspection. History is never cleared or replayed automatically.

## Durable ledger contract to integrate

Record intent before launch and completion/uncertainty afterward. Preserve owner
instance identity, resource kind, private/attached/shared classification, OS
creation identity and containment identity. Shared resources need explicit leases
and last-owner cleanup. A manager must revalidate exact identities before any
recovery action; expired leases and PIDs alone are insufficient. Never remove
unresolved records merely because their owner is unreachable.

Use the shared package's reviewed API; do not implement a parallel recovery
database or infer compatibility from a package version. Keep its records
outside public Git. Native dirty state and uncertain writes need an explicit
refusal/reconciliation path, preserving app journals, source hashes and receipts.
There is no automatic restart, route switch, retry, rollback or unsaved-data loss.

## Evidence boundary

Pure unit tests use inert process objects, stub kernels and local synthetic files.
They cover identity mismatch, bounded admission, retained uncertainty, cancellation,
cleanup ordering, close-all behavior, attached-object preservation and bounded
resource readers. They start no MCP server or native application and create no OS job object.

Subsequent isolated process tests must establish real EOF, startup failure,
owner death, child/grandchild cleanup, console/shell teardown, dirty-state refusal,
shared last-owner behavior and preservation of attached user applications. Package,
full-suite and installed-runtime checks remain separate. A stopped management
registration records endpoint closure; it is not a native-resource cleanup receipt.
