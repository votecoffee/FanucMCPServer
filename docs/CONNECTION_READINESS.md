# Connection readiness and capacity

Use the shared [connection-readiness contract](https://github.com/votecoffee/CodexMCPBroker/blob/main/docs/server-development/connection-readiness.md)
with FANUC's standalone profile and exact reviewed pins. This is documentation
adoption; it does not upgrade the framework, SDK or optional host.

## Separate connections from data execution

Normal desktop demand includes many idle chats. A two-client fixture does not
establish that the next chat can initialize. The former eight lifetime slots
blocked the ninth idle connection even though no analysis was running. Never
call legitimate live occupancy a stale lock or kill peers to pass a probe.

The offline deployment supports up to **32 metadata connections** per app root
and evidence state. Each published process has one actor and **128 MiB** private
commit. The normal steady-state bound is **4 GiB**, matching the former eight
512 MiB actors, with no app-created child processes. Metadata, tool schemas,
current guidance and health do not import analysis services or acquire a data
execution lease. The existing SDK and original byte-stream transport are retained.

Data operations admit **eight executing calls** per evidence state and **16
waiting calls**, with a five-second admission deadline. Locks are acquired only
before the protected operation; the body is never automatically retried. They
are released after that call, cancellation at a safe boundary or actual process
exit. A full/expired work queue returns a meaningful tool error while metadata
and guidance remain usable. Each client's own request dispatcher also stays
bounded. An idle metadata connection owns no data-worker slot.

Old live generations retain their original 512 MiB bounds and code. During the
reviewed migration there can be at most eight such legacy actors within the
32-connection ceiling. The temporary worst-case bound is **7 GiB** (eight legacy
actors plus 24 new actors); it declines naturally as old clients close. Their
existing request concurrency is grandfathered, never adopted or modified.
Measure real private commit and process counts at idle and during work; do not
substitute working-set figures or claim this theoretical ceiling was consumed.

Direct development offline clients have the same 32-connection OS lease bound;
the accepted 128 MiB process containment is established by the published stable
launcher. Library embedding is not installed-process acceptance.

## Startup and publication

Policies are generated and parsed through production code. Validate the explicit
zero-robot/write-disabled profile before admission. Do not construct a native,
controller or JobManager owner merely to answer metadata. Preserve uncertain
journals, workspace hashes, atomic catalog publication and shared audit locks.
Offline lifetime audit provenance uses its own ID and actual process identity;
it does not create or close a native resource-ledger owner. Stable process
receipts and OS metadata leases retain admission/lifetime evidence. Native and
local-native profiles keep their existing resource ownership and close barriers.
On Windows, offline status reporting verifies the existing registry directory's
protected user/SYSTEM permissions through retained non-reparse handles. It
requires the current user as directory owner and rejects links in the parent path.
Directory identity and permissions are checked again for later publications.
Existing inheritable ACLs are retained during each start, publication or close.
Creation uses a single parent-handle-relative `NtCreateFile` operation that applies
private ACLs only to a new directory and returns its retained identity handle.
Existing objects are opened without ACL repair. Directory-list read access and
denied delete sharing hold the rename fence from creation through close; there
is no separate path-based permission update before the first pin. Unexpected
permissions disable optional reporting without repairing
peers or blocking MCP; the connection still exposes no restart authority.

The stable entry change is an explicit bootstrap migration. The installer needs
`--expected-bootstrap-sha256` as well as the exact prior selection hash. It first
verifies old bytes, stages/probes the new closure, backs up exact entry/selection
bytes and records intent under the publication lock. Existing generations keep
their loaded code. An interrupted two-file publication refuses new startup;
explicit verified metadata-only `publish` recovery can roll forward with current
hashes. Never restore a stale global config or replay a domain operation.

Reviewed pure Python code lives in `dependencies.zip` and `stdlib.zip` in the
new immutable runtime. Native libraries and guidance remain explicit files.
The manifest covers both archives, all native libraries, guidance, metadata and
the retained interpreter's actual bootstrap import closure. Startup hashes
bytes from retained deny-write/delete Windows handles before importing. Those
handles remain held through process exit, including directory rename fences.
The importer searches only the pinned archives and declared native extensions;
it does not search retained legacy `Lib` or native directories for new modules.
The installer compiles checked-hash bytecode into the archives with paired
source and validates every pair before publication. Startup hashes and pins
every archive byte and validates all member paths; the importer also verifies
interpreter magic, checked-hash flags and the source hash immediately before
executing each module. Unused modules need no repeated decompression during
metadata startup. The archive hash binds the reviewed compiled bytes too. Archive paths,
ambiguous names, links, native members, unpaired/stale bytecode and unsupported
compression refuse startup. This reduces loose-file startup I/O without dropping
integrity checks, runtime cache writes or changing old snapshots.

## Required regression evidence

- Capture exact kernel identities, liveness, private commit and parent for every
  occupied slot before classifying capacity. Unknown ownership remains unknown.
- Test the expected open-chat workload, including a cold ninth connection and a
  reconnect burst through the exact saved command/args/cwd/env. Measure initialize
  against the host deadline; package checks or a warm two-client test are insufficient.
  Record simultaneous idle capacity and simultaneous cold-start burst size
  separately. A connection ceiling does not establish startup throughput. Keep
  failed bursts and their host-load observations; do not increase the timeout
  to reclassify them as passing.
- Saturate data workers and waiters with harmless fixtures. Discovery, current
  guidance and capabilities must still work; a data call must return honest busy.
- Verify idle, active and queued EOF, without pre-cancelling the condition under
  test; record SDK cleanup escalation, exact roots and all helpers/children.
- Test owner/parent death with another process holding the original pipe writer;
  retain full-generation handles, actual triggers, timestamps and exit evidence.
- Exercise failed startup/factory, bootstrap interruption, strict settings,
  stale-selection refusal, original wire handles/Unicode and peer-preserving update.
- Keep source/package, selected deployment, fresh SDK connection, actual host
  namespace activation and native workflow acceptance separate. Native/controller/
  compiler/GUI/shared-host work stays outside this offline profile.

Retain actual policies, identities, logs, hashes and receipts under ignored local
storage. Update this guide and focused tests when adding tools: classify each
tool's metadata/data/native cost and admission before implementing its body.
