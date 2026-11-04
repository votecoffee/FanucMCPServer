"""Quiescence must include accepted wire work and exact receipt authority."""
import threading
from types import SimpleNamespace

import pytest

from fanuc_workbench.bridge import stable_launcher as launcher
from fanuc_workbench.bridge.idle_backend import IdleControl, WireState


def request(identifier, method="tools/list", params=None):
    return SimpleNamespace(id=identifier, method=method, params=params)


def response(identifier):
    return SimpleNamespace(id=identifier)


def ready_wire():
    wire = WireState()
    wire.received(request("catalog"), '{"id":"catalog","method":"tools/list"}\n')
    wire.emitting(response("catalog"))
    wire.flushed(response("catalog"), succeeded=True)
    assert wire.eligible(0)
    return wire


def test_accepted_request_and_unflushed_output_prevent_retirement():
    wire = ready_wire()
    wire.received(request(123456789012345678901234567890, "tools/call", {"name": "analyze_workspace"}),
                  '{"method":"tools/call","unicode":"机器人"}\n')
    # An empty executor alone is insufficient while SDK delivery/serialization
    # or a blocked output flush still owns the response.
    assert not wire.eligible(0)
    result = response(123456789012345678901234567890)
    wire.emitting(result)
    assert not wire.eligible(0)
    wire.flushed(result, succeeded=True)
    assert wire.eligible(0) and not wire.pending and wire.sending == 0


@pytest.mark.parametrize("method", ["resources/subscribe", "logging/setLevel"])
def test_unsupported_session_state_retains_original_child(method):
    wire = ready_wire()
    wire.received(request(3, method), '{"id":3}\n')
    wire.emitting(response(3))
    wire.flushed(response(3), succeeded=True)
    assert wire.reason and not wire.eligible(0)


def test_late_output_or_failed_flush_cannot_be_a_clean_idle_checkpoint():
    wire = ready_wire()
    wire.frozen = True
    wire.emitting(response("late"))
    wire.flushed(response("late"), succeeded=False)
    assert wire.failed and not wire.eligible(0)


def generation(tmp_path):
    value = launcher.OfflineGeneration.__new__(launcher.OfflineGeneration)
    value.root = tmp_path
    value.directory = tmp_path / "sessions"
    value.directory.mkdir()
    value.path = value.directory / ("a" * 32 + ".json")
    value.guard = threading.RLock()
    value.identity = {"pid": 12, "created": "120", "host_id": "synthetic", "executable": "backend"}
    value.parent = {"pid": 11, "created": "110", "host_id": "synthetic", "executable": "frontend"}
    value.selection = {"schema_version": 1, "snapshot_id": "b" * 64}
    value.idle_yielded = False
    value.idle_control = None
    value.thread = None
    value.stop = threading.Event()
    value.parent_handle = SimpleNamespace(identity=value.parent, alive=lambda: True, close=lambda: None)
    value.record = {"identity": value.identity, "state": "active"}
    launcher.atomic(value.path, launcher.encoded(value.record))
    value.handoff_raw = launcher.encoded({"identity": value.parent, "parent": {"pid": 10},
        "state": "activating", "snapshot": value.selection, "native_intents": False,
        "lazy_frontend": True, "handoff_backend": value.identity, "handoff_nonce": "c" * 64,
        "idle_epoch": 1, "app_created_children": [value.identity]})
    control = SimpleNamespace(directory=tmp_path / "control", config={"nonce": "c" * 64, "epoch": 1})
    control.directory.mkdir()
    return value, control


def test_writer_transfer_keeps_frontend_admitted_and_fences_old_close(tmp_path):
    value, control = generation(tmp_path)
    digest = value.yield_idle(control, "d" * 32)
    transferred = value.path.read_bytes()
    assert launcher.digest(transferred) == digest
    receipt = launcher.parse(transferred)
    assert receipt["identity"] == value.parent and receipt["state"] == "closing"
    assert receipt["app_created_children"] == [value.identity]
    with pytest.raises(RuntimeError, match="already yielded"):
        value.write("closed", "stale writer")
    value.close()
    assert value.path.read_bytes() == transferred


def test_withdrawal_and_changed_nonce_cannot_transfer_authority(tmp_path):
    value, control = generation(tmp_path)
    original = value.path.read_bytes()
    launcher.atomic(control.directory / "withdraw.json", launcher.encoded({**control.config, "attempt": "d" * 32}))
    assert value.yield_idle(control, "d" * 32) is None
    assert value.path.read_bytes() == original and not value.idle_yielded
    control.config["nonce"] = "e" * 64
    with pytest.raises(ValueError, match="binding differs"):
        value.yield_idle(control, "f" * 32)
    assert value.path.read_bytes() == original and not value.idle_yielded


def test_failed_yielded_epoch_retains_primary_barrier_and_failed_terminal(tmp_path):
    value, _ = generation(tmp_path)
    directory = value.directory / "idle" / value.path.stem / "1"
    directory.mkdir(parents=True)
    config = {"protocol": 1, "epoch": 1, "nonce": "c" * 64, "snapshot": value.selection,
              "frontend": value.parent, "backend": value.identity, "seconds": 60}
    launcher.atomic(directory / "config.json", launcher.encoded(config))
    control = IdleControl(value, directory, "c" * 64, vars(launcher))
    value.idle_control = control
    control.receipt_sha256 = value.yield_idle(control, "d" * 32)
    control.attempt = "d" * 32
    transferred = value.path.read_bytes()
    control.wire.frozen = True
    control.wire.emitting(response("unexpected late output"))
    value.close()
    terminal = launcher.parse(launcher.read(directory / "terminal.json"))
    assert terminal["state"] == terminal["backend_receipt"]["state"] == "failed"
    assert terminal["nonce"] == config["nonce"] and terminal["epoch"] == 1
    assert value.path.read_bytes() == transferred
    assert launcher.parse(transferred)["app_created_children"] == [value.identity]
