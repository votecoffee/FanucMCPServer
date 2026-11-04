# Cooperative offline backend idle release

Status: opt-in source candidate. Existing installations retain their selected
behavior until a matching frontend/backend pair is reviewed and published.
The default remains one backend retained after first activation.

## Current lifetime

The [discovery frontend](LAZY_DISCOVERY.md) starts one Python backend on first
explicit use and retains it until frontend EOF or parent loss. After activation,
it forwards opaque byte chunks. An unexpected backend exit ends the connection;
the frontend never retries an operation. Request executors retire after 30 idle
seconds, releasing their threads while keeping the Python process resident.

The standalone offline service has durable artifact storage and shared audit
locking, with no controller, native-worker, job or review-plan routes. This makes
cooperative backend retirement worth considering. A quiet process or an empty
request dispatcher alone cannot prove protocol quiescence: accepted SDK messages,
response serialization, output flushes and session subscriptions also matter.

## Scope

Limit the first implementation to a newly reviewed, matching frontend/backend
pair using `standalone-offline`. Leave eager clients, previous generations and
native/controller profiles unchanged. Keep the existing frontend, host stdio,
32-connection admission pool, two-actor/128 MiB containment, dependency pins,
policy, evidence directories and uncertain-owner barriers.

Use a finite idle interval, normally 60 seconds since the last
completed and flushed operation. Enable it only through a separately reviewed
immutable frontend descriptor. A chat tool cannot change this behavior. Keep
the current retain-until-EOF behavior when either peer lacks the exact supported
retirement protocol, or session state cannot be shown safe to release.

The frontend must preserve the original initialization capsule for internal
backend handshakes. Retain only those already bounded negotiation frames;
do not retain completed tool frames or results. Preserve existing wire limits.
After first activation, retain opaque stream forwarding even while parked:
new input starts a backend before forwarding its bytes. Do not switch the
already negotiated client back to pre-initialization framing or add a size cap
to previously accepted active-stream requests.

## Retirement protocol

Use a private, owner-bound control channel rather than a new public MCP tool or
injected application-wire method. Bind every checkpoint to the exact frontend
and child identities, immutable snapshot, connection receipt, activation epoch
and fresh nonce. Control messages have a bounded schema, size and deadline.

1. **Propose quiescence.** The backend reports eligibility after its idle interval.
   Require no admitted, queued or running request; no executing notification;
   no pending client or server request; no response waiting for serialization or
   flush; and no owned background operation. Active subscriptions or unsupported
   process-local session state keep the backend resident. Absence of tool jobs
   alone is insufficient. Do not silently discard subscriptions or logging state.
2. **Fence input and validate again.** The frontend takes its own dispatch gate
   at a newline boundary, with no partial frame already forwarded. Abort the
   attempt if new input is already queued. Hold later input in the existing
   bounded queue. Send the exact forwarded byte boundary to the backend, which
   confirms it consumed that boundary and repeats all quiescence checks after
   output flush. Do not infer this fence from a timeout or CPU activity.
3. **Transfer the writer and drain.** Under the existing admission lock, perform
   an exact receipt CAS that represents the still-live frontend and its original
   retiring child. Fence the backend's ordinary receipt writer first: its close
   or parent watcher must never overwrite a receipt transferred to the frontend.
   The backend acknowledges that checkpoint, retains its own terminal outcome
   separately and accepts EOF. Close only that child's input and let the normal
   bridge/SDK shutdown drain accepted work. Do not terminate the child.
4. **Park after proof.** Wait on the original process handle for exit code zero,
   join both output/error pumps and verify their drain and the final checkpoint.
   Then publish the frontend's active, child-free receipt under the same logical
   connection and release only that child's handles and transport objects. Do
   not release the frontend's containment, payload pins or client-parent handle.
   The live frontend must remain represented throughout; a terminal backend
   receipt cannot temporarily make its connection look free.
5. **Activate on new input.** Start one fresh child only after the previous epoch
   completed the preceding proof. Internally initialize it against the original
   client negotiation and verify the pinned capabilities/catalog before sending
   held input once, in its original order. Keep the external connection open and
   send no second client-visible initialize response. Never replay a completed
   request or an operation with an unknown outcome.

Before writer transfer, a failed or expired proposal can withdraw its own fence
and continue on the same original child. After transfer or EOF, an incomplete
drain, changed identity, nonzero exit, lost checkpoint or uncertain outcome keeps
the epoch protected and prevents another child from starting. Report the real
failure. No automatic kill, stale-selector rollback, reconciliation or retry is
part of idle release. Bound proposal/validation and graceful drain separately;
expiration is not proof of departure.

## Implementation and focused verification

| Source | Integration |
| --- | --- |
| `native/FanucMcpFrontend/Program.cs` | Add an activation-epoch state machine, dispatch fence, retained negotiation capsule and own-child retirement. Capture child-specific pump references so an old task cannot read a new child's stream. |
| `fanuc_workbench/bridge/frontend_stdio.py` and `protocol.py` | Observe full protocol acceptance, pending messages, response flush and session-state eligibility without changing SDK source or wire bytes. |
| `fanuc_workbench/bridge/offline_service.py` and `requests.py` | Expose a consistent eligibility view; preserve normal draining and shared data-admission leases. Thread-pool retirement remains independent. |
| `fanuc_workbench/bridge/stable_launcher.py` | Add a nonce-bound writer relinquishment/terminal checkpoint for this exact child and prevent stale close/watch writers. Preserve old-generation admission. |
| `scripts/build_lazy_frontend.py` | Pin the optional idle protocol and finite bounds to the reviewed closure. |

The existing frontend's single-activation rule is an explicit compatibility
boundary. Each successor needs a new reviewed release; existing clients keep their
original behavior. Check exact previous-generation participant and writer-fence
compatibility before publication. Incompatible or UNKNOWN owners stay protected.

Focused controls cover the receipt writer, unsupported session state and response
flush boundaries. Use a small synthetic control for work arriving at the retirement boundary,
including queued work, cancellation, a blocked output flush, subscriptions and
an uncertain child. Demonstrate one connection serving a request, retiring its
own child naturally, then serving a new request without losing its admission
slot or replaying the old call. Retain original handles and failed checkpoints.
An eventual publication requires one useful saved-route read and natural drain;
no broader host, model or capacity matrix is part of this proposal.

## Build and limits

Use the existing candidate builder with a catalog captured from that exact
protocol-1 backend:

```powershell
python scripts/build_lazy_frontend.py `
  --runtime-root C:/Applications/FanucMCPServer/MCP `
  --catalog-capture C:/Packages/fanuc-discovery/CATALOG_CAPTURE.json `
  --output C:/Packages/fanuc-discovery/idle-candidate `
  --idle-seconds 60
```

The backend's hash-bound runtime metadata must declare `idle_backend_protocol=1`.
The descriptor pins its interval and separate five-second checkpoint and
ten-second drain bounds. The idle clock starts at the last accepted message or
completed response flush. The checkpoint clock starts before the first fence
write; the drain clock starts before closing the original child's input.

Control files are private, nonce/epoch/identity-bound checkpoints under the
installation's sessions directory. They contain no tool payloads. Retained
terminal files preserve the original outcome; this implementation adds no
automatic archival or recovery. A failed transferred checkpoint keeps the
frontend and original child protected and prevents new dispatch or relaunch.
Normal EOF still drains that original child; parent-loss containment is unchanged.

Resource subscriptions, logging changes, unsupported client notifications,
server requests/notifications, malformed messages or unmatched responses retain
the original child. Noncanonical transport line endings can fail the exact byte
boundary check and also retain it. These messages still use the existing wire
path; they are not rejected to make idle release possible. The backend uses
shared durable workspaces and policy rather than a new empty evidence store.
