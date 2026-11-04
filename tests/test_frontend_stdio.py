"""Real idle input pipe, delivered frame reachability and descriptor ownership."""
import asyncio
import contextvars
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
from types import AsyncGeneratorType, SimpleNamespace
import tracemalloc
import weakref
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp.server import stdio as sdk

from fanuc_workbench.bridge import frontend_stdio
from fanuc_workbench.bridge.protocol import FanucServer


async def idle_frames(provider, destination):
    """One bounded bidirectional exchange, with the input pipe left open/idle."""
    read_fd, write_fd = os.pipe()
    incoming = anyio.wrap_file(os.fdopen(read_fd, "r", encoding="utf-8", newline=""))
    feed = anyio.wrap_file(os.fdopen(write_fd, "w", encoding="utf-8", newline=""))
    output = anyio.wrap_file(destination.open("w", encoding="utf-8", newline=""))
    references = []
    started_tracing = not tracemalloc.is_tracing()
    if started_tracing:
        tracemalloc.start()
    try:
        async with provider(incoming, output) as (read, write):
            try:
                # A malformed line is reported; the following frame still runs.
                await feed.write("invalid JSON\n")
                await feed.flush()
                error = await read.receive()
                assert isinstance(error, Exception)
                del error

                async def transfer():
                    raw = json.dumps({"jsonrpc": "2.0", "method": "fixture", "params": {"blob": "x" * (2 * 1024 * 1024)}})
                    await feed.write(raw + "\n")
                    await feed.flush()
                    del raw
                    item = await read.receive()
                    assert len(item.message.params["blob"]) == 2 * 1024 * 1024
                    references.extend((weakref.ref(item), weakref.ref(item.message)))
                    del item
                    item = sdk.SessionMessage(sdk.types.JSONRPCResponse(jsonrpc="2.0", id=1, result={"blob": "y" * (2 * 1024 * 1024)}))
                    references.extend((weakref.ref(item), weakref.ref(item.message)))
                    await write.send(item)

                with anyio.fail_after(10):
                    await transfer()
                    while destination.stat().st_size < 2 * 1024 * 1024:
                        await anyio.sleep(.01)
                    # Allow both pumps to reach their next idle receive/read.
                    await anyio.sleep(.02)
                gc.collect()
                retained = [reference() is not None for reference in references]
                pump_strings = {}
                raw_lines = {}
                def inspect_raw_line(value, owner):
                    if isinstance(value, str) and len(value) >= 2 * 1024 * 1024:
                        trace = tracemalloc.get_object_traceback(value)
                        row = raw_lines.setdefault(id(value), {"bytes": sys.getsizeof(value), "characters": len(value),
                            "allocation_trace": [str(entry) for entry in trace] if trace else [], "owners": []})
                        row["owners"].append(owner)
                for task in asyncio.all_tasks():
                    coroutine = task.get_coro()
                    while coroutine is not None:
                        frame = getattr(coroutine, "cr_frame", None) or getattr(coroutine, "ag_frame", None)
                        if frame is not None and Path(frame.f_code.co_filename).name in {"frontend_stdio.py", "stdio.py"}:
                            pump_strings[frame.f_code.co_name] = sum(
                                len(value) for value in frame.f_locals.values() if isinstance(value, str))
                            if frame.f_code.co_name in {"reader", "stdin_reader"}:
                                inspect_raw_line(frame.f_locals.get("line"), frame.f_code.co_name)
                        coroutine = getattr(coroutine, "cr_await", None) or getattr(coroutine, "ag_await", None)
                # Async-generator await objects do not expose their generator
                # through cr_await. Inspect only this fixture's exact input file.
                for generator in gc.get_objects():
                    if isinstance(generator, AsyncGeneratorType) and generator.ag_frame is not None:
                        frame = generator.ag_frame
                        if frame.f_locals.get("self") is incoming:
                            inspect_raw_line(frame.f_locals.get("line"), "AnyIO AsyncFile iterator")
                return {"retained_message_objects": retained, "idle_pump_string_characters": pump_strings,
                        "retained_raw_input_allocations": list(raw_lines.values()),
                        "output_bytes": destination.stat().st_size}
            finally:
                await feed.aclose()  # EOF releases the real blocked input worker.
                await write.aclose()
    finally:
        await incoming.aclose()
        await output.aclose()
        if started_tracing:
            tracemalloc.stop()


def test_delivered_frames_are_released_while_real_input_pipe_is_idle(tmp_path):
    result = asyncio.run(idle_frames(frontend_stdio.stdio_server, tmp_path / "output.jsonl"))
    assert result["retained_message_objects"] == [False] * 4
    assert result["idle_pump_string_characters"] == {"reader": 0, "writer": 0}
    assert result["retained_raw_input_allocations"] == []
    assert json.loads((tmp_path / "output.jsonl").read_text())["result"]["blob"] == "y" * (2 * 1024 * 1024)


def test_server_uses_app_provider_and_original_sdk_session(monkeypatch):
    entered = []
    @asynccontextmanager
    async def provider():
        entered.append("provider")
        yield "read", "write"
        entered.append("closed")
    async def run(*args):
        assert args == ("read", "write", "options")
        entered.append("session")
    server = object.__new__(FanucServer)
    server._lowlevel_server = SimpleNamespace(run=run, create_initialization_options=lambda: "options")
    monkeypatch.setattr(frontend_stdio, "stdio_server", provider)
    asyncio.run(server.run_stdio_async())
    assert entered == ["provider", "session", "closed"]


def test_default_provider_preserves_fd_diversion_and_restore():
    # Disposable fixture subprocess only. No application/native/helper launch.
    source = '''
import sys,os,json,anyio
sys.path[:0]=__PATHS__
from fanuc_workbench.bridge.frontend_stdio import stdio_server
from mcp.server import stdio as sdk
async def main():
    async with stdio_server() as (read,write):
        assert os.read(0,1)==b''
        os.write(1,b'STRAY\\n')
        item=await read.receive()
        await write.send(sdk.SessionMessage(sdk.types.JSONRPCResponse(jsonrpc='2.0',id=item.message.id,result={'ok':True})))
        await write.aclose()
    assert not sdk._claims
    os.write(1,b'RESTORED\\n')
anyio.run(main)
'''.replace("__PATHS__", repr([value for value in sys.path if value]))
    result = subprocess.run([sys.executable, "-I", "-B", "-c", source], input='{"jsonrpc":"2.0","id":1,"method":"fixture"}\n',
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    frame, restored = result.stdout.splitlines()
    assert json.loads(frame) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert restored == "RESTORED" and result.stderr == "STRAY\n"


def test_unreviewed_sdk_version_is_refused_before_claim(monkeypatch):
    monkeypatch.setattr(frontend_stdio, "version", lambda package: "unreviewed")
    with pytest.raises(RuntimeError, match="reviewed MCP 2.3.0"):
        frontend_stdio.verify_sdk()


@pytest.mark.parametrize("fail_flush", [False, True])
def test_completed_writer_context_is_released_and_input_context_propagates(fail_flush):
    output_payload = contextvars.ContextVar("fixture_output_payload", default=None)
    input_marker = contextvars.ContextVar("fixture_input_marker", default=None)
    class Payload:
        def __init__(self):
            self.data = bytearray(8 * 1024 * 1024)
    class Output:
        def __init__(self):
            self.flushed = anyio.Event()
        async def write(self, raw):
            assert json.loads(raw)["result"] == {"ok": True}
        async def flush(self):
            self.flushed.set()
            if fail_flush:
                raise OSError("synthetic flush failure")

    async def scenario():
        read_fd, write_fd = os.pipe()
        incoming = anyio.wrap_file(os.fdopen(read_fd, "r", encoding="utf-8"))
        feed = anyio.wrap_file(os.fdopen(write_fd, "w", encoding="utf-8"))
        output = Output()
        reference = None
        failures = []
        token = input_marker.set("input-owner")
        manager = frontend_stdio.stdio_server(incoming, output)
        try:
            try:
                async with manager as (read, write):
                    input_marker.reset(token)
                    token = None
                    try:
                        await feed.write('{"jsonrpc":"2.0","method":"fixture"}\n')
                        await feed.flush()
                        await read.receive()
                        assert read.last_context.get(input_marker) == "input-owner"
                        async def send():
                            payload = Payload()
                            observed = weakref.ref(payload)
                            output_token = output_payload.set(payload)
                            try:
                                await write.send(sdk.SessionMessage(sdk.types.JSONRPCResponse(
                                    jsonrpc="2.0", id=1, result={"ok": True})))
                            finally:
                                output_payload.reset(output_token)
                            return observed
                        reference = await send()
                        await output.flushed.wait()
                        await anyio.sleep(.02)
                        gc.collect()
                        assert reference() is None
                    finally:
                        # A writer failure cancels the fixture body. Still send
                        # EOF to drain its own blocked input worker normally.
                        with anyio.CancelScope(shield=True):
                            await feed.aclose()
                            await write.aclose()
            except* OSError as error:
                failures.append(error)
            assert bool(failures) is fail_flush
            gc.collect()
            assert reference is not None and reference() is None
        finally:
            if token is not None:
                input_marker.reset(token)
            await incoming.aclose()
    asyncio.run(scenario())
