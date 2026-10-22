"""Explicit Tasks adapters for bounded backup/recording read jobs only."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import math
import threading

from universal_tools.tasks import TaskExtension, TaskObservation, TaskRoute, TaskStore


def owner_hash(principal):
    return hashlib.sha256(principal.encode()).hexdigest() if principal else None


def job_id(task_id):
    return hashlib.sha256(("fanuc-job-task-v1:" + task_id).encode()).hexdigest()[:32]


class JobTaskAdapter:
    def __init__(self, bridge, principal, kind):
        self.bridge, self.principal, self.kind = bridge, principal, kind

    def validate(self, arguments):
        common = {"request_id", "robot_id"}
        allowed = common | ({"paths", "max_depth", "attempt_ascii"} if self.kind == "backup" else
                            {"expressions", "samples", "interval_seconds", "cycle_signal"})
        if set(arguments) - allowed or not common <= set(arguments):
            raise ValueError("Use the exact app Task schema")
        self.bridge._access(arguments["robot_id"])
        if self.kind == "backup":
            if type(arguments.get("max_depth", 8)) is not int or not 0 <= arguments.get("max_depth", 8) <= 16:
                raise ValueError("Invalid backup depth")
            if type(arguments.get("attempt_ascii", True)) is not bool:
                raise ValueError("attempt_ascii must be boolean")
            paths = arguments.get("paths")
            if paths is not None:
                if not isinstance(paths, list) or not 1 <= len(paths) <= self.bridge.config.max_backup_files:
                    raise ValueError("Invalid selected backup paths")
                for path in paths:
                    self.bridge._path(arguments["robot_id"], path)
        else:
            self.bridge.operations._watch_specs(arguments.get("expressions", []))
            samples, interval = arguments.get("samples", 10), arguments.get("interval_seconds", 2.0)
            if type(samples) is not int or not 1 <= samples <= 300:
                raise ValueError("Invalid recording sample count")
            if type(interval) not in {int, float} or not math.isfinite(interval) or not 2 <= interval <= 60:
                raise ValueError("Invalid recording interval")
            signal = arguments.get("cycle_signal", "")
            if signal and signal not in arguments["expressions"]:
                raise ValueError("Cycle signal must be a selected expression")

    async def authorize(self, context, handle):
        if not hmac.compare_digest(context.principal, self.principal):
            raise ValueError("Task principal does not own this app frontend")
        if context.task_id:
            identifier = job_id(context.task_id)
            if handle is not None and handle != identifier:
                raise ValueError("Task/job binding changed")
            try:
                item = self.bridge.operations.jobs.authorize_owner(identifier, owner_hash(context.principal))
            except FileNotFoundError:
                if handle is not None:
                    raise ValueError("Task job evidence is missing; reconcile without replay") from None
            else:
                if item["task_binding"]["task_id"] != context.task_id or item["kind"] != self.kind:
                    raise ValueError("Task route binding changed")
                self.bridge._access(item["robot_id"])

    async def start(self, context, arguments):
        self.validate(arguments)
        identifier = job_id(context.task_id)
        binding = {"task_id": context.task_id, "owner_hash": owner_hash(context.principal),
                   "route": self.kind + "@1", "arguments_sha256": hashlib.sha256(
                       json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        options = {key: value for key, value in arguments.items() if key not in {"request_id", "robot_id"}}
        operation = self.bridge.operations.start_backup if self.kind == "backup" else self.bridge.operations.start_watch
        await self.bridge.requests.run(operation, arguments["robot_id"], **options,
                                       job_id=identifier, task_binding=binding)
        return identifier

    async def inspect(self, context, handle, pending):
        identifier = job_id(context.task_id)
        try:
            item = await self.bridge.requests.run(self.bridge.operations.jobs.status, identifier, control=True)
        except (FileNotFoundError, ValueError):
            # No proof of admission is not proof of no effect. Retain the start
            # intent for app/operator reconciliation; never redispatch it.
            return TaskObservation("working", reconciled=False)
        status = item["status"]
        if status == "cancelled":
            return TaskObservation("cancelled", reconciled=True, handle=identifier)
        if status in {"complete", "failed", "interrupted"}:
            result = {"content": [{"type": "text", "text": json.dumps(item)}],
                      "isError": status != "complete"}
            return TaskObservation("completed", result=result, reconciled=True, handle=identifier)
        return TaskObservation("working", reconciled=True, handle=identifier)

    async def update(self, context, handle, responses):
        raise ValueError("Backup/recording Tasks do not request approval inputs")

    async def cancel(self, context, handle):
        await self.bridge.requests.run(self.bridge.operations.jobs.cancel, job_id(context.task_id), control=True)


class JobTasks(TaskExtension):
    def __init__(self, bridge, principal):
        if not isinstance(principal, str) or not 32 <= len(principal) <= 128:
            raise ValueError("Tasks need an app-selected trusted frontend identity")
        self.bridge, self.principal = bridge, principal
        routes = {}
        for name, kind in (("start_backup_task", "backup"), ("start_recording_task", "watch")):
            adapter = JobTaskAdapter(bridge, principal, kind)
            routes[name] = TaskRoute(adapter, "fanuc-" + kind + "-job-v1", adapter.validate)
        super().__init__(TaskStore(bridge.config.state_dir / "mcp-tasks.sqlite3"), routes)

    @asynccontextmanager
    async def lifespan(self, server):
        loop = asyncio.get_running_loop()
        pending, guard, wake = set(), threading.Lock(), asyncio.Event()
        accepting = True

        def changed(item):
            binding = item.get("task_binding", {})
            if binding.get("owner_hash") != owner_hash(self.principal):
                return
            with guard:
                if not accepting or len(pending) >= 64:
                    return  # The authorized subscription poll remains available.
                notify = not pending
                pending.add(binding["task_id"])
                if notify:
                    loop.call_soon_threadsafe(wake.set)

        async def publish():
            while True:
                await wake.wait()
                wake.clear()
                with guard:
                    identifiers = tuple(pending)
                    pending.clear()
                for identifier in identifiers:
                    await self.changed(identifier)

        jobs = self.bridge.operations.jobs
        with jobs.lock:
            jobs.listeners.add(changed)
        publisher = asyncio.create_task(publish())
        try:
            async with super().lifespan(server):
                yield
        finally:
            with guard:
                accepting = False
            with jobs.lock:
                jobs.listeners.discard(changed)
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)
