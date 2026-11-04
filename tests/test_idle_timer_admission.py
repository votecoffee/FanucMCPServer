"""An idle timer startup error must not masquerade as an uncertain work drain."""
import json
import threading

import pytest

from fanuc_workbench.bridge import offline_service
from fanuc_workbench.bridge.offline_service import owned_offline_bridge
from fanuc_workbench.bridge.requests import RequestDispatcher
from fanuc_workbench.bridge.storage import RobotBusyError, RobotLocks


def fail_idle_timer(monkeypatch, lane):
    original = threading.Timer.start
    failed = threading.Event()
    rearmed = threading.Event()
    timers = []
    def start(timer):
        if timer.name == "fanuc-idle-" + lane and not failed.is_set():
            timers.append(timer)
            failed.set()
            raise RuntimeError("Synthetic idle timer startup failure")
        result = original(timer)
        if timer.name == "fanuc-idle-" + lane:
            rearmed.set()
        return result
    monkeypatch.setattr(threading.Timer, "start", start)
    return failed, timers, rearmed


def test_idle_timer_start_failure_does_not_poison_metadata_lease(tmp_path, monkeypatch):
    policy = tmp_path / "offline.json"
    policy.write_text(json.dumps({"state_dir": "state", "source_dir": "inputs", "robots": {}, "allow_writes": False}))
    source = tmp_path / "inputs/synthetic"
    source.mkdir(parents=True)
    raw = b'/PROG CHECK\n/ATTR\n/MN\n   1: WAIT DI[7]=ON ;\n/POS\n/END\n'
    (source / "CHECK.LS").write_bytes(raw)
    uncertain_before = len(offline_service._uncertain_leases)
    close_error = None
    with monkeypatch.context() as patch:
        failed, timers, _ = fail_idle_timer(patch, "requests")
        try:
            with owned_offline_bridge(policy) as first:
                item = first.requests.submit(first.run_data, first.operations.import_workspace, "synthetic").result(timeout=5)
                assert failed.wait(2)
                # The timer-start hook runs after completion under this lock.
                with first.requests.lock:
                    pass
        except RuntimeError as error:
            close_error = str(error)
    assert close_error is None, f"Completed work close failed: {close_error}; uncertain slots: {len(offline_service._uncertain_leases) - uncertain_before}"
    assert len(offline_service._uncertain_leases) == uncertain_before
    assert first._closed and not first.requests.status()["active"]
    assert first.requests.status()["idle_timer_errors"] == {"requests": "RuntimeError"}
    assert not any(timer.is_alive() for timer in timers)
    locks = RobotLocks(tmp_path / "state/mcp-owner/metadata-clients")
    with locks.acquire("metadata-client-0"):
        pass  # The exact original metadata permit is available again.
    with owned_offline_bridge(policy) as second:
        with pytest.raises(RobotBusyError):
            with locks.acquire("metadata-client-0"):
                pytest.fail("The next client did not acquire the released first slot")
        result = second.requests.submit(second.run_data, second.operations.inspect_program,
                                        item["artifact_id"], "CHECK.LS").result(timeout=5)
        assert result["validation"] == [] and [row[1] for row in result["flow"]] == ["WAIT"]
    assert (source / "CHECK.LS").read_bytes() == raw


@pytest.mark.parametrize("control", [False, True])
def test_later_request_rearms_idle_timer_after_start_failure(monkeypatch, control):
    dispatcher = RequestDispatcher(workers=1, pending=1)
    lane = "control" if control else "requests"
    try:
        with monkeypatch.context() as patch:
            failed, timers, rearmed = fail_idle_timer(patch, lane)
            assert dispatcher.submit(lambda: "completed", control=control).result(timeout=2) == "completed"
            assert failed.wait(2)
            with dispatcher.lock:
                assert lane not in dispatcher.idle_timers
            assert dispatcher.status()["idle_timer_errors"] == {lane: "RuntimeError"}
            assert dispatcher.submit(lambda: "subsequent useful request", control=control).result(timeout=2) == "subsequent useful request"
            assert rearmed.wait(2)
            # Wait for _arm_idle after the Future has delivered its result.
            with dispatcher.lock:
                pass
    finally:
        dispatcher.close()
    assert dispatcher.status()["idle_timer_errors"] == {}
    assert not dispatcher.status()["active"] and not any(timer.is_alive() for timer in timers)
