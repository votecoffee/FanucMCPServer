"""Release delivered stdio frames while retaining the pinned SDK's ownership.

Adapted from the MCP Python SDK stdio transport under the MIT License:
Copyright (c) 2024 Anthropic, PBC

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import hashlib
import sys
from contextlib import asynccontextmanager
from importlib.metadata import version

import anyio
import anyio.lowlevel
from mcp.server import stdio as sdk


SDK_STDIO_SHA256 = "2a3e73aee57056a05def81cbad210458437de97b4da0c9993325b19636bca9ec"


def verify_sdk():
    # Use the verified import loader for both loose and immutable ZIP layouts.
    source = sdk.__loader__.get_data(sdk.__file__)
    if version("mcp") != "2.3.0" or hashlib.sha256(source).hexdigest() != SDK_STDIO_SHA256:
        raise RuntimeError("FANUC stdio requires the reviewed MCP 2.3.0 transport")


@asynccontextmanager
async def stdio_server(stdin=None, stdout=None, *, idle=None):
    """Use SDK framing, context streams and fd diversion without idle payloads."""
    verify_sdk()
    restore_stdin = restore_stdout = None
    try:
        if not stdin:
            buffer, restore_stdin = sdk._claim_fd(0, sys.stdin, "rb", sdk._open_stdin_diversion)
            stdin = anyio.wrap_file(sdk._UnownedTextWrapper(buffer, encoding="utf-8", errors="replace"))
        if not stdout:
            buffer, restore_stdout = sdk._claim_fd(1, sys.stdout, "wb", sdk._open_stdout_diversion)
            stdout = anyio.wrap_file(sdk._UnownedTextWrapper(buffer, encoding="utf-8"))

        read_tx, read_rx = sdk.create_context_streams[sdk.SessionMessage | Exception](0)
        write_tx, write_rx = sdk.create_context_streams[sdk.SessionMessage](0)

        async def reader():
            try:
                async with read_tx:
                    while True:
                        # AsyncFile.__aiter__ retains its yielded line during
                        # the next read. Read directly so this pump owns it.
                        line = await stdin.readline()
                        if not line:
                            break
                        message = session_message = None
                        try:
                            try:
                                message = sdk.types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                            except Exception as error:
                                if idle is not None:
                                    idle.wire.unsupported("invalid input")
                                await read_tx.send(error)
                                continue
                            if idle is not None:
                                idle.wire.received(message, line)
                            session_message = sdk.SessionMessage(message)
                            await read_tx.send(session_message)
                        finally:
                            line = message = session_message = None
            except anyio.ClosedResourceError:
                await anyio.lowlevel.checkpoint()

        async def writer():
            try:
                async with write_rx:
                    async for session_message in write_rx:
                        raw = None
                        succeeded = False
                        if idle is not None:
                            idle.wire.emitting(session_message.message)
                        try:
                            raw = session_message.message.model_dump_json(by_alias=True, exclude_unset=True)
                            await stdout.write(raw + "\n")
                            await stdout.flush()
                            succeeded = True
                        finally:
                            if idle is not None:
                                idle.wire.flushed(session_message.message, succeeded=succeeded)
                            session_message = raw = None
                            # Output serialization does not use the sender's
                            # ContextVars. Input read_rx.last_context still
                            # propagates to the SDK handler unchanged.
                            write_rx.last_context = None
            except anyio.ClosedResourceError:
                await anyio.lowlevel.checkpoint()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(reader)
            tasks.start_soon(writer)
            yield read_rx, write_tx
    finally:
        try:
            if restore_stdout is not None:
                restore_stdout()
        finally:
            if restore_stdin is not None:
                restore_stdin()
