# Concurrent bridge requests

The MCP host dispatches blocking service calls through a bounded worker pool.
Independent controller requests and preserved-file analyses can overlap. Requests
to the same configured host share a controller lease, including aliases with the
same host string. A write holds that lease through preflight, the controller
change and readback. File, description and native changes retain their exact
approval hashes, expiry checks and single-use plans.

The lease also coordinates bridge processes sharing the same `state_dir`.
Different spellings or DNS aliases for the same physical controller do not
establish the same lock identity. Other controller clients do not participate in
these advisory locks. Ordering across competing requests is not guaranteed FIFO.

## Capacity and settings

These optional top-level settings belong in the operator's local bridge JSON:

```json
{
  "max_concurrent_requests": 4,
  "max_pending_requests": 16,
  "robot_lock_timeout_seconds": 15
}
```

The defaults admit up to 20 normal requests, with at most four executing worker
functions. `max_concurrent_requests` accepts 1–16, `max_pending_requests` 0–128 and
`robot_lock_timeout_seconds` 0–60 seconds. Requests waiting for controller access
occupy worker slots. A full queue refuses additional work before invoking its
service; a controller wait that exceeds its bound reports a busy error. Neither
case triggers an automatic write retry.

A separate worker admits up to nine bookkeeping requests so robot lists,
capabilities, job status, cancellations and request status remain available when
the normal pool is occupied. Background backup/watch jobs keep their separate
two-worker pool and bounded job queue, while sharing controller leases. Compatible
feature workers retain their own serialized RPC channels.

Apply capacity-setting changes by reconnecting the MCP host. Connection-only
reload refuses changes to these settings. The fixed 48 MCP tool schemas are
preserved; this host implementation requires an update/reconnect, rather than a
feature-worker reload.

Accepted requests retain their configuration identity. If connections change
before a queued request reaches controller access, it is refused rather than
silently redirected to the new endpoint. Inspect the refreshed settings and
submit a new request.

## Request status

`capabilities` includes a `concurrency` section with active requests, configured
limits and the most recent 64 outcomes. The operation catalog also exposes the
read-only `core.requests.status` operation. Describe it, then pass `{}` to
`run_read_operation` with the returned revision. Request arguments and results
are omitted from status history. Normal controller audit records include their
generated `request_id`; the durable audit journal coordinates concurrent writers
and readers across bridge processes.

History is local to the running host and bounded in memory. It is not a durable
queue or a guarantee that a disconnected client received an operation's result.
Existing read jobs and preserved controller evidence keep their durable records.

## Shared Python API

Use the same dispatcher from a Python client that needs bounded queuing:

```python
from pathlib import Path
from fanuc_workbench.bridge.service import RobotBridge

with RobotBridge.from_config(Path(".local/bridge.json")) as bridge:
    first = bridge.requests.submit(bridge.read_file, "robot1", "md:MAIN.LS")
    second = bridge.requests.submit(bridge.read_file, "robot2", "md:MAIN.LS")
    print(first.request_id, first.result())
    print(second.request_id, second.result())
```

Async callers can use `await bridge.requests.run(bridge.read_file, ...)`.
Calling a synchronous service directly retains fail-fast controller locking;
independent hosts can still run concurrently from separate caller threads.
Close the bridge from its owning thread after requesting work. Shutdown drains
accepted requests and read jobs before closing native clients and feature workers.

## Cancellation and changes

Cancelling a queued request prevents its callable from running. Cancelling a
request waiting for a controller lease interrupts that wait. A cancelled queue
entry retains its admission slot until its executor drains it, keeping the queue
bounded during repeated cancellations.

Once controller access starts, cancelling an MCP call or Python future does not
terminate the vendor/file operation. The worker completes the current operation
and readback, retaining its audit and evidence. A lost client response therefore
requires inspecting current state before another change. Explicit plan
cancellation can win before a native commit claims its plan; claimed plans cannot
be reused. There is no automatic rollback or grouped multi-operation transaction.

Controller locks prevent bridge operations from interleaving; they do not make
controller reads simultaneous or establish physical/runtime acceptance.
