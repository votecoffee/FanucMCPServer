"""Independent cleanup of the selected stdio frontdoor, without helper launches."""
import asyncio
import io
import threading
from types import SimpleNamespace

import pytest
from mcp.server import stdio as sdk

from fanuc_workbench.bridge import frontend_stdio, stable_launcher as launcher


def generation(tmp_path):
    # No real process is adopted. The retained parent surrogate is a real,
    # fixture-owned file; keeping the error traceback keeps this object alive.
    owner = object.__new__(launcher.OfflineGeneration)
    owner.stop = threading.Event()
    owner.guard = threading.RLock()
    owner.thread = None
    owner.parent_handle = (tmp_path / "query-handle.bin").open("w+b")
    owner.path = tmp_path / "generation.json"
    owner.record = {"state": "active", "native_intents": False, "app_created_children": []}
    launcher.atomic(owner.path, launcher.encoded(owner.record))
    return owner


def test_receipt_write_error_still_closes_owned_parent_handle(tmp_path, monkeypatch):
    owner = generation(tmp_path)
    before = owner.path.read_bytes()
    calls = []
    failure = OSError("synthetic receipt publication failure")
    def write(*args):
        calls.append(args)
        raise failure
    monkeypatch.setattr(owner, "write", write)
    try:
        with pytest.raises(OSError) as error:
            owner.close()
        assert error.value is failure and error.value.__traceback__ is not None
        assert owner.parent_handle.closed
        assert owner.path.read_bytes() == before  # No invented terminal receipt.
        assert len(calls) == 1
    finally:
        owner.parent_handle.close()  # This fixture's original file only.


def test_unstarted_watcher_join_error_still_publishes_failure_and_closes_handle(tmp_path):
    owner = generation(tmp_path)
    owner.thread = threading.Thread(target=lambda: None)  # Never started.
    try:
        with pytest.raises(RuntimeError, match="cannot join thread") as error:
            owner.close()
        assert error.value.__traceback__ is not None and owner.stop.is_set()
        assert owner.parent_handle.closed
        assert launcher.parse(owner.path.read_bytes())["state"] == "failed"
    finally:
        owner.parent_handle.close()


def test_all_close_errors_remain_chained_after_independent_attempts(tmp_path, monkeypatch):
    owner = generation(tmp_path)
    original = owner.parent_handle
    before = owner.path.read_bytes()
    calls = []
    join_error, receipt_error, handle_error = (RuntimeError("watcher join"), OSError("receipt"), OSError("query handle close"))
    def join(**kwargs):
        calls.append("join")
        raise join_error
    def write(*args):
        calls.append("receipt")
        raise receipt_error
    def close():
        calls.append("parent")
        original.close()
        raise handle_error
    owner.thread = SimpleNamespace(join=join)
    owner.parent_handle = SimpleNamespace(close=close)
    monkeypatch.setattr(owner, "write", write)
    try:
        with pytest.raises(OSError) as error:
            owner.close()
        assert calls == ["join", "receipt", "parent"] and original.closed
        assert error.value is handle_error
        assert handle_error.__context__ is receipt_error and receipt_error.__context__ is join_error
        assert owner.path.read_bytes() == before
    finally:
        original.close()


@pytest.mark.parametrize("input_error", [False, True])
def test_stdio_restores_other_wire_after_first_restore_error(monkeypatch, input_error):
    calls = []
    stdout_error, stdin_error = OSError("stdout restoration"), OSError("stdin restoration")
    def claim(fd, *args):
        def restore():
            calls.append(fd)
            if fd == 1:
                raise stdout_error
            if input_error:
                raise stdin_error
        return io.BytesIO(), restore
    # Replace only this app module's binding with a synthetic claim provider.
    # Shared/global SDK modules and descriptors are never edited by the fixture.
    fake = SimpleNamespace(**{name: getattr(sdk, name) for name in (
        "_open_stdin_diversion", "_open_stdout_diversion", "_UnownedTextWrapper",
        "create_context_streams", "SessionMessage", "types")}, _claim_fd=claim)
    monkeypatch.setattr(frontend_stdio, "sdk", fake)
    monkeypatch.setattr(frontend_stdio, "verify_sdk", lambda: None)
    async def scenario():
        async with frontend_stdio.stdio_server() as (read, write):
            await write.aclose()
    with pytest.raises(OSError) as error:
        asyncio.run(scenario())
    assert calls == [1, 0]
    assert error.value is (stdin_error if input_error else stdout_error)
    if input_error:
        assert stdin_error.__context__ is stdout_error
