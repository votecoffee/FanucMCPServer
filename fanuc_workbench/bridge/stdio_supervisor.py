"""Owning stdio relay using ordinary SDK streams and durable process records.

MCP bytes stay opaque. Only the first private bootstrap line is intercepted.
The framework's background launcher is not used as an MCP stdio transport.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

from fanuc_workbench.bridge.extension_process import WindowsJob
from fanuc_workbench.bridge.process_identity import creation_time
from fanuc_workbench.bridge.resource_context import configure_resource_registry, resource_context, close_resource_context
from fanuc_workbench.version import add_version_argument


_uncertain = []
ACTIVATE = b'{"activate":"fanuc-stdio-v1"}\n'


def supervise(command, context, source, destination, *, environment=None, popen=None,
              job_factory=None, identity=None, startup_timeout=15):
    popen = popen or subprocess.Popen
    job_factory = job_factory or WindowsJob
    identity = identity or creation_time
    claim = context.begin("stdio-worker")
    child = job = None
    activated = threading.Event()
    startup_finished = threading.Event()
    boot = queue.Queue(maxsize=1)
    output_errors = []
    output_thread = None
    complete = False
    try:
        child = popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True, env=environment,
                      **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        claim.bind(child.pid, identity(child._handle))

        def output_pump():
            announced = False
            try:
                line = child.stdout.readline(4097)
                if not line or len(line) > 4096 or not line.endswith(b"\n"):
                    raise ValueError("Stdio bootstrap did not provide a bounded identity")
                value = json.loads(line)
                boot.put(value)
                announced = True
                if not startup_finished.wait(startup_timeout) or not activated.is_set():
                    return
                read = getattr(child.stdout, "read1", child.stdout.read)
                forwarding = True
                while chunk := read(65536):
                    if forwarding:
                        try:
                            destination.write(chunk)
                            destination.flush()
                        except (BrokenPipeError, OSError, ValueError):
                            forwarding = False  # Keep draining; native receipt/exit is still app-owned.
            except Exception:
                output_errors.append(True)
                if not announced:
                    try:
                        boot.put_nowait(None)
                    except queue.Full:
                        pass

        output_thread = threading.Thread(target=output_pump, daemon=True, name="fanuc-stdio-output")
        output_thread.start()
        value = boot.get(timeout=startup_timeout)
        if (not isinstance(value, dict) or value.get("fanuc_stdio_bootstrap") != 1
                or type(value.get("pid")) is not int or value["pid"] < 1
                or type(value.get("creation_time")) is not int or value["creation_time"] <= 0):
            raise ValueError("Stdio bootstrap identity is unavailable")
        claim.bind_bootstrap(value["pid"], value["creation_time"])
        job = job_factory()
        job.attach(value["pid"], value["creation_time"])
        child.stdin.write(ACTIVATE)
        child.stdin.flush()
        activated.set()
        startup_finished.set()

        def input_pump():
            try:
                read = getattr(source, "read1", source.read)
                while chunk := read(65536):
                    child.stdin.write(chunk)
                    child.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    child.stdin.close()
                except (OSError, ValueError):
                    pass

        threading.Thread(target=input_pump, daemon=True, name="fanuc-stdio-input").start()
        # Client EOF closes the sole input writer. Do not guess a safe timeout
        # for a running native operation; the child owns its domain drain.
        result = child.wait()
        job.close()
        output_thread.join(timeout=2)
        if output_thread.is_alive() or output_errors:
            raise RuntimeError("Stdio output/exit is unverified; ownership retained")
        child.stdout.close()
        claim.exited()
        complete = True
        return result
    finally:
        if not complete:
            # Retain handles even if persisting the recovery barrier itself fails.
            _uncertain.append((claim, child, job, output_thread))
            startup_finished.set()
            try:
                claim.uncertain("Stdio startup/exit is unverified; inspect app journals without retrying controller changes")
            finally:
                if child is not None and not activated.is_set():
                    # The bootstrap has not received any MCP input yet.
                    try:
                        child.stdin.close()
                        if job is not None:
                            job.close()
                        child.wait(timeout=5)
                    except Exception:
                        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_version_argument(parser)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--runtime", type=Path)
    mode.add_argument("--config", type=Path)
    mode.add_argument("--entry", type=Path)
    parser.add_argument("--management-registry", type=Path, default=os.environ.get("FANUC_MCP_MANAGEMENT_REGISTRY"))
    parser.add_argument("--management-runtime", type=Path, help="Optional installer-owned metadata for --config")
    parser.add_argument("--instance-role", choices=("per-chat", "validation"))
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("The managed FANUC stdio profile currently requires Windows")
    configure_resource_registry(args.management_registry)
    python = Path(sys.executable)
    target = []
    if args.runtime:
        runtime = args.runtime.resolve(strict=True)
        settings = json.loads(runtime.read_text(encoding="utf-8"))
        python = Path(settings["python"]).resolve(strict=True)
        config = Path(settings["config"]).resolve(strict=True)
        if not python.is_file() or not config.is_file():
            parser.error("The installed Python and policy must be ordinary files")
        target = ["--config", str(config), "--management-runtime", str(runtime)]
    elif args.config:
        target = ["--config", str(args.config.resolve(strict=True))]
    else:
        target = ["--entry", str(args.entry.resolve(strict=True))]
    if args.management_registry and not args.entry:
        target.extend(["--management-registry", str(args.management_registry)])
    if args.management_runtime and not args.runtime and not args.entry:
        target.extend(["--management-runtime", str(args.management_runtime.resolve(strict=True))])
    if args.instance_role and not args.entry:
        target.extend(["--instance-role", args.instance_role])
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if args.management_registry:
        environment["FANUC_MCP_MANAGEMENT_REGISTRY"] = str(args.management_registry)
    if args.instance_role:
        environment["FANUC_MCP_INSTANCE_ROLE"] = args.instance_role
    result = supervise([str(python), "-B", "-m", "fanuc_workbench.bridge.stdio_worker", *target],
        resource_context(), sys.stdin.buffer, sys.stdout.buffer, environment=environment)
    close_resource_context()
    raise SystemExit(result)


if __name__ == "__main__":
    main()
