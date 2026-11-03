"""Bounded shared request dispatch; cancellation never abandons a running write."""
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import contextvars
import threading
import time
import uuid

from fanuc_workbench.bridge.storage import RequestCancelled, utc_now


IDLE_POOL_SECONDS = 30


class RequestQueueFull(RuntimeError):
    pass


class RequestFuture(Future):
    def __init__(self, identifier):
        super().__init__()
        self.request_id = identifier
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()
        return super().cancel()


class RequestDispatcher:
    """Keep the MCP event loop free, with a separate lane for status/cancellation.

    Admission lasts until the underlying worker exits, including when its client
    stops waiting. Cancelled queue entries keep their slots until drained, so
    repeatedly cancelling queued requests cannot grow the executor's queue.
    """

    def __init__(self, workers=4, pending=16, robot_wait_seconds=15, *, configuration_supplier=None):
        if type(workers) is not int or not 1 <= workers <= 16:
            raise ValueError("max_concurrent_requests must be between 1 and 16")
        if type(pending) is not int or not 0 <= pending <= 128:
            raise ValueError("max_pending_requests must be between 0 and 128")
        if type(robot_wait_seconds) not in (int, float) or not 0 <= robot_wait_seconds <= 60:
            raise ValueError("robot_lock_timeout_seconds must be between 0 and 60")
        self.workers, self.pending, self.robot_wait_seconds = workers, pending, robot_wait_seconds
        self.configuration_supplier = configuration_supplier
        self.lock = threading.RLock()
        self.close_lock = threading.RLock()
        self.local = threading.local()
        self.closed = False
        self.active = {}
        self.history = deque(maxlen=64)
        self.pools = {}
        self.idle_timers = {}
        self.idle_since = {}
        self.retiring = {}

    @property
    def current_request_id(self):
        return getattr(self.local, "request_id", None)

    @property
    def lock_timeout(self):
        return self.robot_wait_seconds if self.current_request_id else 0

    @property
    def cancel_event(self):
        return getattr(self.local, "cancel_event", None)

    @property
    def current_configuration(self):
        return getattr(self.local, "configuration", None)

    def submit(self, function, /, *args, control=False, **kwargs):
        """Return a Future with request_id; the callable's arguments are never logged."""
        lane = "control" if control else "requests"
        context = contextvars.copy_context()
        with self.lock:
            if self.closed:
                raise RuntimeError("Request dispatcher has closed")
            configuration = self.configuration_supplier() if self.configuration_supplier else None
            capacity = 9 if control else self.workers + self.pending
            if sum(entry[0]["lane"] == lane for entry in self.active.values()) >= capacity:
                raise RequestQueueFull("Request queue is full; inspect current requests before submitting more work")
            self.idle_since.pop(lane, None)
            if lane not in self.pools:
                self.pools[lane] = ThreadPoolExecutor(max_workers=1 if control else self.workers,
                    thread_name_prefix="fanuc-control" if control else "fanuc-request")
            identifier = uuid.uuid4().hex
            future = RequestFuture(identifier)
            record = {"request_id": identifier, "operation": getattr(function, "__name__", "request")[:100],
                      "lane": lane, "status": "queued", "created_at": utc_now()}
            self.active[identifier] = (record, future)

            def execute():
                if not future.set_running_or_notify_cancel():
                    self._finish(record, future, "cancelled")
                    self._arm_idle(lane)
                    return
                with self.lock:
                    record.update(status="running", started_at=utc_now())
                self.local.request_id, self.local.cancel_event = identifier, future.cancel_event
                self.local.configuration = configuration
                try:
                    result = function(*args, **kwargs)
                except BaseException as exc:
                    state = "cancelled" if isinstance(exc, RequestCancelled) else "failed"
                    self._finish(record, future, state, error_type=type(exc).__name__)
                    future.set_exception(exc)
                else:
                    self._finish(record, future, "complete")
                    future.set_result(result)
                finally:
                    self.local.request_id = self.local.cancel_event = None
                    self.local.configuration = None
                    self._arm_idle(lane)

            try:
                # Do not expose/cancel the executor Future: its queue slot must
                # remain occupied until execute has drained a cancelled entry.
                self.pools[lane].submit(context.run, execute)
            except BaseException:
                self.active.pop(identifier, None)
                raise
            return future

    def _arm_idle(self, lane):
        with self.lock:
            if self.closed or any(row[0]["lane"] == lane for row in self.active.values()):
                return
            self.idle_since[lane] = time.monotonic()
            if lane not in self.idle_timers:
                self._start_idle_timer(lane, IDLE_POOL_SECONDS)

    def _start_idle_timer(self, lane, seconds):
        timer = threading.Timer(seconds, lambda: self._retire_idle(lane, timer))
        timer.name = "fanuc-idle-" + lane
        timer.daemon = True
        self.idle_timers[lane] = timer
        timer.start()

    def _retire_idle(self, lane, timer):
        with self.lock:
            if self.closed or self.idle_timers.get(lane) is not timer:
                return
            self.idle_timers.pop(lane)
            if lane not in self.idle_since:
                return  # A request is still executing; its completion rearms.
            remaining = IDLE_POOL_SECONDS - (time.monotonic() - self.idle_since[lane])
            if remaining > 0:
                self._start_idle_timer(lane, remaining)
                return
            self.idle_since.pop(lane)
            pool = self.pools.pop(lane)
            self.retiring[pool] = timer
        try:
            # No queued/running requests remain in this lane. Join outside the
            # admission lock; a new request may create a fresh pool immediately.
            pool.shutdown(wait=True)
        finally:
            with self.lock:
                self.retiring.pop(pool)

    def _finish(self, record, future, status, **fields):
        with self.lock:
            record.update(status=status, finished_at=utc_now(), cancel_requested=future.cancel_event.is_set(), **fields)
            self.active.pop(record["request_id"], None)
            self.history.append(dict(record))

    @staticmethod
    async def wait(future):
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def run(self, function, /, *args, control=False, **kwargs):
        return await self.wait(self.submit(function, *args, control=control, **kwargs))

    def status(self):
        with self.lock:
            current = [{**record, "cancel_requested": future.cancel_event.is_set()}
                       for record, future in self.active.values()]
            return {"max_concurrent_requests": self.workers, "max_pending_requests": self.pending,
                    "robot_lock_timeout_seconds": self.robot_wait_seconds,
                    "active": current, "recent": list(self.history), "closed": self.closed,
                    "control_workers": 1, "control_capacity": 9, "controller_contacted": False}

    def close(self):
        if self.current_request_id:
            raise RuntimeError("Close the request dispatcher from its owning thread")
        with self.close_lock:
            with self.lock:
                if self.closed:
                    return
                self.closed = True
                futures = [future for _, future in self.active.values()]
                pools = set(self.pools.values()) | set(self.retiring)
                timers = set(self.idle_timers.values()) | set(self.retiring.values())
                self.idle_timers.clear()
            for timer in timers:
                timer.cancel()
            for future in futures:
                future.cancel()
            for pool in pools:
                pool.shutdown(wait=True, cancel_futures=False)
            for timer in timers:
                timer.join()
