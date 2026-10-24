"""Short-lived OS admission for offline data calls, never idle MCP connections."""
import time

from fanuc_workbench.bridge.storage import RobotLocks, RobotBusyError, RequestCancelled


ACTIVE = 8
WAITING = 16
WAIT_SECONDS = 5


class OfflineWorkBusy(RuntimeError):
    pass


class OfflineWorkGate:
    def __init__(self, state):
        self.locks = RobotLocks(state / "mcp-owner" / "offline-work")

    def take(self, kind, maximum):
        for index in range(maximum):
            lease = self.locks.acquire(f"{kind}-{index}")
            try:
                lease.__enter__()
            except RobotBusyError:
                continue
            return lease
        return None

    def run(self, function, *args, cancel_event=None, **kwargs):
        active = self.take("active", ACTIVE)
        waiting = None
        try:
            if active is None:
                waiting = self.take("waiting", WAITING)
                if waiting is None:
                    raise OfflineWorkBusy("Offline data capacity is busy: 8 executing, 16 waiting; capabilities and workflow guidance remain available")
                deadline = time.monotonic() + WAIT_SECONDS
                while active is None:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RequestCancelled("Offline request cancelled before data admission; no data operation started")
                    if time.monotonic() >= deadline:
                        raise OfflineWorkBusy("Offline data workers remained busy for 5 seconds; no data operation started; capabilities and workflow guidance remain available")
                    time.sleep(.02)
                    active = self.take("active", ACTIVE)
                waiting.__exit__(None, None, None)
                waiting = None
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled("Offline request cancelled before data work; no data operation started")
            return function(*args, **kwargs)
        finally:
            if waiting is not None:
                waiting.__exit__(None, None, None)
            if active is not None:
                active.__exit__(None, None, None)
