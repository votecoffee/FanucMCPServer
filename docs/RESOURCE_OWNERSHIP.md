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
| Plugin stdio launcher | Retained child handle, explicit non-inherited input writer, client EOF/parent-loss propagation, owning wait | Durable launch record, atomic startup containment and abnormal-exit reconciliation with the common API |
| Direct/app-host backend | Exclusive state-directory lease through native drain | Durable owner identity and cross-process recovery integration |
| Feature/native RPC worker | Shared durable launch records, private Windows job, verified creation timestamp, retained handles, activation barrier, bounded inventory and uncertain-cleanup refusal | Real child-tree/owner-death and installed/native acceptance |
| Offline MakeTP/PrintTP | Explicit executables, isolated inputs/output and bounded output/time; currently tracks a root `Popen` | Common owned launch/tree teardown and durable compiler/descendant records |
| GUI ROBOGUIDE launch | Explicit user action; existing path does not establish exclusive versus shared application ownership | Classify ownership, retain launch identity and apply dirty-workcell refusal/recovery; never close an already user-owned session |
| Host-policy validation helper | Fixed metadata script, selected interpreter, bounded synchronous call; never starts the host | Common ownership record and cancellation/owner-death cleanup |
| Build/install/doctor helpers | Explicit administrative commands; excluded from runtime startup | Adopt applicable ownership helpers and isolated acceptance before registration/install |

The compiler and ROBOGUIDE paths are not accepted as managed launch workflows
by this source-only checkpoint. Do not install or enable the candidate on the
strength of worker unit tests alone. Existing user-owned native software must
remain untouched while ownership is unestablished.

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
resource readers. They create no MCP server, job object or native application.

Subsequent isolated process tests must establish real EOF, startup failure,
owner death, child/grandchild cleanup, console/shell teardown, dirty-state refusal,
shared last-owner behavior and preservation of attached user applications. Package,
full-suite and installed-runtime checks remain separate. A stopped management
registration records endpoint closure; it is not a native-resource cleanup receipt.
