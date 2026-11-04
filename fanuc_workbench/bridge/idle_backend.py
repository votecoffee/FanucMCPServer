"""Opt-in, private offline-child quiescence. Never controls another owner."""
from __future__ import annotations

from pathlib import Path
import threading
import time
import uuid

from fanuc_workbench.bridge.offline_profile import TOOLS


_current = None
READ_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call", "prompts/list",
                          "resources/list", "resources/templates/list", "resources/read"})


def current():
    return _current


class WireState:
    """Keep identifiers/counters only; completed messages and payloads are released."""
    def __init__(self):
        self.lock = threading.RLock()
        self.pending = {}
        self.sending = 0
        self.bytes = 0
        self.ready = False
        self.reason = None
        self.frozen = False
        self.failed = False
        self.activity = time.monotonic()

    def unsupported(self, reason):
        with self.lock:
            self.reason = self.reason or reason
            self.failed |= self.frozen
            self.activity = time.monotonic()

    @staticmethod
    def key(identifier):
        return type(identifier).__name__, identifier

    def received(self, message, line):
        with self.lock:
            # TextIO may normalize unusual line endings. A byte-boundary mismatch
            # then retains this backend; it never changes accepted wire behavior.
            self.bytes += sum(len(line[i:i + 4096].encode("utf-8")) for i in range(0, len(line), 4096))
            self.activity = time.monotonic()
            if self.frozen:
                self.unsupported("input after quiescence fence")
            method = getattr(message, "method", None)
            identifier = getattr(message, "id", None)
            if method == "notifications/initialized" and identifier is None:
                return
            if method not in READ_METHODS or identifier is None:
                self.unsupported("unsupported session method or notification")
                return
            if method == "tools/call" and (getattr(message, "params", None) or {}).get("name") not in TOOLS:
                self.unsupported("unsupported tool state")
            key = self.key(identifier)
            if key in self.pending:
                self.unsupported("duplicate outstanding request id")
            self.pending[key] = method

    def emitting(self, message):
        with self.lock:
            self.sending += 1
            if self.frozen:
                self.unsupported("output after quiescence fence")
            if getattr(message, "method", None) is not None:
                self.unsupported("server request or notification")

    def flushed(self, message, *, succeeded):
        with self.lock:
            self.sending -= 1
            self.activity = time.monotonic()
            if not succeeded:
                self.unsupported("output flush failed")
                return
            method = self.pending.pop(self.key(getattr(message, "id", None)), None)
            if method == "tools/list":
                self.ready = True
            elif method is None:
                self.unsupported("unmatched response")

    def eligible(self, seconds):
        return (self.ready and self.reason is None and not self.frozen and not self.pending
                and self.sending == 0 and time.monotonic() - self.activity >= seconds)


class IdleControl:
    def __init__(self, generation, directory, nonce, launcher):
        self.generation, self.launcher = generation, launcher
        self.directory = launcher["ordinary"](Path(directory), directory=True)
        expected = generation.root / "sessions/idle" / generation.path.stem
        if self.directory.parent != expected:
            raise ValueError("Idle control escaped this frontend connection")
        self.config = launcher["parse"](launcher["read"](self.directory / "config.json"))
        c = self.config
        prior = launcher["parse"](generation.handoff_raw)
        if (set(c) != {"protocol", "epoch", "nonce", "snapshot", "frontend", "backend", "seconds"}
                or c["protocol"] != 1 or type(c["epoch"]) is not int or c["epoch"] < 1
                or self.directory.name != str(c["epoch"]) or c["nonce"] != nonce
                or c["snapshot"] != generation.selection or c["frontend"] != generation.parent
                or c["backend"] != generation.identity or prior.get("idle_epoch") != c["epoch"]
                or type(c["seconds"]) not in (int, float) or not .05 <= c["seconds"] <= 3600):
            raise ValueError("Idle control identities, epoch or bounds differ")
        self.wire = WireState()
        self.bridge = None
        self.stop = threading.Event()
        self.thread = None
        self.attempt = None
        self.receipt_sha256 = None

    def envelope(self, **values):
        return {**self.config, **values}

    def write(self, name, **values):
        self.launcher["atomic"](self.directory / name, self.launcher["encoded"](self.envelope(**values)))

    def bind(self, bridge):
        if not bridge.offline_metadata or bridge.config.robots or bridge.config.allow_writes:
            raise ValueError("Idle release requires the standalone offline service")
        self.bridge = bridge
        self.thread = threading.Thread(target=self.watch, name="fanuc-idle-child", daemon=True)
        self.thread.start()

    def eligible(self):
        return (self.bridge is not None and not self.bridge._closed
                and not self.bridge.requests.closed and not self.bridge.requests.admission_failed
                and not self.bridge.requests.active and self.wire.eligible(self.config["seconds"]))

    def watch(self):
        offered_activity = None
        seen_attempt = None
        try:
            while not self.stop.wait(.025):
                with self.wire.lock, self.bridge.requests.lock:
                    if self.eligible() and offered_activity != self.wire.activity:
                        self.write("offer.json", state="offered", offer=uuid.uuid4().hex)
                        offered_activity = self.wire.activity
                fence = self.directory / "fence.json"
                if not fence.exists():
                    continue
                value = self.launcher["parse"](self.launcher["read"](fence))
                if any(value.get(key) != item for key, item in self.config.items()):
                    raise ValueError("Idle fence binding differs")
                attempt = value.get("attempt")
                if (not isinstance(attempt, str) or len(attempt) != 32
                        or any(char not in "0123456789abcdef" for char in attempt)):
                    raise ValueError("Invalid idle attempt nonce")
                if seen_attempt == attempt:
                    continue
                seen_attempt = attempt
                with self.wire.lock, self.bridge.requests.lock:
                    if not self.eligible() or value.get("bytes") != self.wire.bytes:
                        self.write("answer.json", state="retained", attempt=attempt)
                        continue
                    digest = self.generation.yield_idle(self, attempt)
                    if digest is None:
                        self.write("answer.json", state="retained", attempt=attempt)
                        continue
                    self.wire.frozen = True
                    self.attempt, self.receipt_sha256 = attempt, digest
                    self.write("answer.json", state="quiescent", attempt=attempt, receipt_sha256=digest)
                    return
        except BaseException:
            # No uncertain control failure can permit a later child. Retain a
            # failure marker and the actual primary receipt without repairing it.
            self.wire.unsupported("idle checkpoint failed")
            self.write("failure.json", state="failed")

    def close(self, failed):
        self.stop.set()
        if self.thread:
            self.thread.join()
        if self.attempt is not None:
            self.write("terminal.json", state="failed" if failed or self.wire.failed else "closed",
                       attempt=self.attempt, receipt_sha256=self.receipt_sha256,
                       backend_receipt={**self.generation.record, "state": "failed" if failed or self.wire.failed else "closed"})


def install(generation, directory, nonce, launcher):
    global _current
    if _current is not None:
        raise RuntimeError("One backend owns one idle control generation")
    _current = IdleControl(generation, directory, nonce, launcher)
    return _current
