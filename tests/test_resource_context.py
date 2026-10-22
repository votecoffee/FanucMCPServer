"""Real shared ledger with a stub process driver; no process/software launches."""
import os
import threading
from types import SimpleNamespace

import pytest
from universal_tools.resources import ResourceLedger

from fanuc_workbench.bridge.resource_context import APP_ID, ResourceContext
from fanuc_workbench.bridge.extension_process import RpcProcess


class Driver:
    def __init__(self):
        self.live = {os.getpid(): self.identity(os.getpid(), 100)}
        self.terminations = []

    @staticmethod
    def identity(pid, created):
        return {"host_id": "a" * 64, "pid": pid, "created": str(created),
                "executable": os.path.normcase(os.path.normpath("C:/Test/python.exe"))}

    def capture(self, pid):
        if pid not in self.live:
            raise ProcessLookupError("Inert process is absent")
        return dict(self.live[pid])

    def state(self, identity):
        current = self.live.get(identity["pid"])
        return "absent" if current is None else ("alive" if current == identity else "different")


@pytest.fixture
def context(tmp_path):
    driver = Driver()
    ledger = ResourceLedger(tmp_path / "private-registry", driver=driver)
    context = ResourceContext(ledger)
    root, child = os.getpid() + 10000, os.getpid() + 10001
    driver.live.update({root: driver.identity(root, 200), child: driver.identity(child, 201)})
    return context, driver, root, child


def test_reservation_precedes_binding_and_uses_actual_supervisor(context):
    resources, driver, root, child = context
    claim = resources.begin("native-worker")
    initial = resources.ledger.get(claim.resource_id)
    assert initial.state == "launching" and initial.process is None
    assert initial.owner.pid == os.getpid() and initial.app_id == APP_ID
    claim.bind(root, 200)
    claim.bind_bootstrap(child, 201)
    claim.bind_bootstrap(child, 201)
    records = resources.ledger.records()
    assert len(records) == 2 and len(claim.identifiers) == 2
    assert {record.process.pid for record in records} == {root, child}
    assert {record.owner_id for record in records} == {resources.owner.owner_id}
    assert driver.terminations == []


def test_creation_mismatch_retains_a_barrier_without_terminating_replacement(context):
    resources, driver, root, _ = context
    claim = resources.begin("feature-worker")
    with pytest.raises(ValueError, match="creation identity differs"):
        claim.bind(root, 999)
    record = resources.ledger.get(claim.resource_id)
    assert record.recovery_barrier and record.process.created == "200"
    assert driver.terminations == []


def test_native_barrier_precedes_effect_and_only_verified_hash_resolves(context):
    resources, driver, root, child = context
    claim = resources.begin("native-worker")
    claim.bind(root, 200)
    claim.bind_bootstrap(child, 201)
    claim.begin_native_write()
    before = [resources.ledger.get(identifier) for identifier in claim.identifiers]
    assert all(record.recovery_barrier for record in before)
    with pytest.raises(ValueError, match="Prior native operation"):
        claim.begin_native_write()
    with pytest.raises(ValueError, match="exact value SHA"):
        claim.verified_native_write("unverified")
    assert all(resources.ledger.get(record.resource_id).recovery_barrier for record in before)
    claim.verified_native_write("b" * 64)
    for previous in before:
        record = resources.ledger.get(previous.resource_id)
        assert record.recovery_barrier is None
        assert record.barrier_resolution.previous_revision == previous.revision
        assert "b" * 64 in record.barrier_resolution.evidence
    assert driver.terminations == []


def test_exit_reconciliation_preserves_unknown_write_barrier_and_domain_journal(context, tmp_path):
    resources, driver, root, _ = context
    claim = resources.begin("native-worker")
    claim.bind(root, 200)
    claim.begin_native_write()
    journal = tmp_path / "domain-write.json"
    journal.write_bytes(b'{"status":"uncertain","original":"preserved"}')
    original = journal.read_bytes()
    with pytest.raises(ValueError, match="inspect/stop"):
        claim.exited()
    driver.live.pop(root)
    claim.exited()
    record = resources.ledger.get(claim.resource_id)
    assert record.state == "released" and record.recovery_barrier
    assert record.barrier_resolution is None and record.reconciliation
    assert journal.read_bytes() == original and driver.terminations == []


def test_unknown_launch_remains_reserved_and_cannot_be_adopted_by_a_new_generation(context):
    resources, driver, _, _ = context
    claim = resources.begin("feature-worker")
    with pytest.raises(RuntimeError, match="unresolved"):
        claim.exited()
    record = resources.ledger.get(claim.resource_id)
    assert record.state == "launching" and record.recovery_barrier
    other = ResourceContext(resources.ledger)
    with pytest.raises(ValueError, match="another recorded owner"):
        other.owner.reconcile(record.resource_id, record.revision, "No owner adoption")
    assert resources.ledger.get(record.resource_id) == record
    assert driver.terminations == []


@pytest.mark.parametrize("verified", [True, False])
def test_rpc_write_dispatch_is_fenced_by_durable_barrier_and_unknown_reply_cannot_replay(context, verified):
    resources, driver, root, _ = context
    claim = resources.begin("native-worker")
    claim.bind(root, 200)
    rpc = object.__new__(RpcProcess)
    rpc.lock, rpc.stopped, rpc.broken = threading.Lock(), threading.Event(), False
    rpc.process = SimpleNamespace(poll=lambda: None)
    rpc.native_worker, rpc.resource_claim, rpc.operation_timeout = True, claim, 2
    dispatched = []
    def send(request):
        record = resources.ledger.get(claim.resource_id)
        assert record.recovery_barrier  # Independent durable read before effect.
        dispatched.append(request)
    rpc._send = send
    rpc._receive = lambda timeout: {"request_id": dispatched[-1]["request_id"], "result":
        {"ok": True, "result": {"status": "verified", "value_sha256": "c" * 64}} if verified else
        {"ok": False, "error_type": "UnverifiedReadback"}}
    rpc.call("write_register", {"target": "R[1]"}, {})
    assert len(dispatched) == 1
    record = resources.ledger.get(claim.resource_id)
    if verified:
        assert record.recovery_barrier is None and record.barrier_resolution
    else:
        assert record.recovery_barrier
        with pytest.raises(ValueError, match="Prior native operation"):
            rpc.call("write_register", {"target": "R[1]"}, {})
        assert len(dispatched) == 1
    assert driver.terminations == []
