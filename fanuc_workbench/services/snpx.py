"""Narrow SNPX/HMI reader: handshake and bounded memory reads only.

Protocol layout adapted from fanuc_ucl (Copyright 2026 Bowan Foryt),
Apache-2.0. See licenses/fanuc-ucl-Apache-2.0.txt and THIRD_PARTY_NOTICES.md.
Changes: synchronous Python transport, no command/write API, exact read policy,
strict framing, total deadlines and retained wire evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import socket
import struct
import time


class SnpxError(RuntimeError):
    """A transport or protocol failure; no automatic retry is performed."""


# Fixed digital ports; indices must not spill into the next port's address space.
_BITS = {"DI": (0x48, 0, 5000), "DO": (0x46, 0, 5000),
         "RI": (0x48, 5000, 1000), "RO": (0x46, 5000, 1000),
         "UI": (0x48, 6000, 1000), "UO": (0x46, 6000, 1000)}
_WORDS = {"GI": (0x0C, 0), "GO": (0x0A, 0),
          "AI": (0x0C, 1000), "AO": (0x0A, 1000)}


def bit_target(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(DI|DO|RI|RO|UI|UO)\[([1-9][0-9]{0,4})\]", value) if isinstance(value, str) else None
    if not match:
        raise ValueError("SNPX digital reads require an exact DI/DO/RI/RO/UI/UO target")
    segment, offset, maximum = _BITS[match[1]]
    index = int(match[2])
    if index > maximum:
        raise ValueError("SNPX digital target exceeds its fixed port address space")
    return segment, offset + index - 1


def io_target(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(GI|GO|AI|AO)\[([1-9][0-9]{0,3})\]", value) if isinstance(value, str) else None
    if not match:
        return bit_target(value)
    index = int(match[2])
    if index > 1000:
        raise ValueError("SNPX group/analog targets are limited to indices 1-1000")
    segment, offset = _WORDS[match[1]]
    return segment, offset + index - 1


def word_span(address: int, count: int) -> None:
    if (type(address) is not int or type(count) is not int
            or not 1 <= address <= 65535 or not 1 <= count <= 128
            or address + count > 65536):
        raise ValueError("Use a one-based %R address and 1-128 words within addresses 1-65535")


@dataclass(frozen=True)
class SnpxReadPolicy:
    port: int = 60008
    io_targets: tuple[str, ...] = ()
    word_ranges: tuple[tuple[int, int], ...] = ()
    io_word_formats: tuple[tuple[str, str], ...] = ()

    @classmethod
    def load(cls, data):
        if data is None:
            return None
        if not isinstance(data, dict) or set(data) - {"port", "io_targets", "word_ranges", "io_word_formats"}:
            raise ValueError("snpx_read requires port, exact io_targets/word_ranges and optional io_word_formats")
        port, targets, ranges = data.get("port", 60008), data.get("io_targets", []), data.get("word_ranges", [])
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("SNPX port must be an integer between 1 and 65535")
        if (not isinstance(targets, list) or len(targets) > 256
                or not isinstance(ranges, list) or len(ranges) > 256 or not (targets or ranges)):
            raise ValueError("SNPX reads require bounded nonempty exact target/range allowlists")
        for target in targets:
            io_target(target)
        if len(set(targets)) != len(targets):
            raise ValueError("Duplicate SNPX I/O targets are not allowed")
        formats = data.get("io_word_formats", {})
        word_targets = {target for target in targets if target.split('[', 1)[0] in _WORDS}
        if (not isinstance(formats, dict) or set(formats) != word_targets
                or any(not isinstance(value, str) or value not in {"u16", "i16"} for value in formats.values())):
            raise ValueError("Each allowed GI/GO/AI/AO target requires an explicit u16/i16 io_word_formats entry; no other entries are allowed")
        spans = []
        for item in ranges:
            if not isinstance(item, dict) or set(item) != {"address", "count"}:
                raise ValueError("Each SNPX word range requires address and count only")
            address, count = item["address"], item["count"]
            word_span(address, count)
            if any(address < start + size and start < address + count for start, size in spans):
                raise ValueError("SNPX word ranges cannot overlap")
            spans.append((address, count))
        return cls(port, tuple(targets), tuple(spans), tuple(sorted(formats.items())))

    def check_words(self, address, count):
        word_span(address, count)
        if not any(start <= address and address + count <= start + size for start, size in self.word_ranges):
            raise ValueError("SNPX word span is outside the configured allowlist")

    def check_io(self, target):
        io_target(target)
        if target not in self.io_targets:
            raise ValueError("SNPX I/O target is outside the configured allowlist")

    def io_format(self, target):
        self.check_io(target)
        if target.split('[', 1)[0] in _WORDS:
            # Also fail closed for manually constructed policies bypassing load().
            result = dict(self.io_word_formats).get(target)
            if result not in {"u16", "i16"}:
                raise ValueError("SNPX group/analog reads require explicit u16/i16 decoding")
            return result
        return "bool"


def _frame(kind=0, seq=0, operation=0, message=0, service=0, segment=0, index=0, count=0):
    data = bytearray(56)
    struct.pack_into("<HH", data, 0, kind, seq)
    struct.pack_into("<I", data, 8, operation)
    struct.pack_into("<I", data, 16, operation)
    data[30:32] = bytes((seq, message))
    if message:
        struct.pack_into("<I", data, 36, 0xE10)
        data[40:42] = b"\x01\x01"
    struct.pack_into("<BBHH", data, 42, service, segment, index, count)
    return bytes(data)


class SnpxReader:
    """One connection per capture; no arbitrary services or write entry points.

    INIT and the 0x4f/0x01 session handshake precede ReadSysMemory (0x04).
    The session handshake uses the protocol's Writing header flag but contains
    no memory data, CLRASG or SETASG. Controller coexistence needs acceptance.
    """

    def __init__(self, host: str, policy: SnpxReadPolicy, timeout: float = 10, checkpoint=None):
        self.host, self.policy, self.timeout = host, policy, timeout
        self.checkpoint = checkpoint
        self.transcript = []
        self.sock = None
        self.seq = 2

    def _remaining(self):
        if self.checkpoint:
            self.checkpoint()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SnpxError("SNPX capture deadline expired")
        return remaining

    def __enter__(self):
        self.deadline = time.monotonic() + self.timeout
        try:
            self.sock = socket.create_connection((self.host, self.policy.port), self._remaining())
            init = self._exchange(_frame(), 0)
            expected = bytearray(56)
            expected[0], expected[8] = 1, 1
            if init != bytes(expected):
                raise SnpxError("SNPX initialization was not acknowledged")
            magic = _frame(8, 1, 0x100, 0xC0, 0x4F, 1)
            reply = self._exchange(magic, 0)
            if reply != magic:
                self._payload(reply, 1, 0, None)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _exchange(self, request, maximum):
        if self.sock is None:
            raise SnpxError("SNPX reader is not connected")
        evidence = {"request_hex": request.hex(), "response_hex": ""}
        self.transcript.append(evidence)
        response = bytearray()
        try:
            self.sock.settimeout(self._remaining())
            self.sock.sendall(request)

            def receive(total):
                while len(response) < total:
                    self.sock.settimeout(self._remaining())
                    chunk = self.sock.recv(total - len(response))
                    if not chunk:
                        raise SnpxError("SNPX response ended before the complete frame")
                    response.extend(chunk)

            receive(42)
            length = struct.unpack_from("<H", response, 4)[0]
            if length > maximum:
                raise SnpxError("SNPX response exceeds the requested payload size")
            receive(56 + length)
            return bytes(response)
        except (OSError, SnpxError) as exc:
            evidence["error_type"] = type(exc).__name__
            # Never reuse a stream after a partial or invalid response.
            self.__exit__(None, None, None)
            raise SnpxError("SNPX transport or framing failed") from exc
        finally:
            evidence["response_hex"] = response.hex()

    @staticmethod
    def _payload(frame, seq, expected, segment):
        kind, sequence, length = struct.unpack_from("<HHH", frame)
        if kind != 3 or sequence != seq or frame[30] != seq or frame[40:42] != b"\x01\x01":
            raise SnpxError("SNPX response has an unexpected type, sequence or packet count")
        if frame[31] == 0xD4 and length == 0 and expected <= 6:
            if frame[42:44] != b"\0\0":
                raise SnpxError("SNPX controller returned an error status")
            return frame[44:44 + expected]
        if (frame[31] == 0x94 and length == expected and expected > 6
                and frame[48:52] == bytes((1, 1, 4, segment)) and frame[52:54] == b"\0\0"):
            return frame[56:]
        raise SnpxError("SNPX response status, payload length or service is unsupported")

    def _read(self, segment, index, count, size):
        seq = self.seq
        self.seq += 1
        if seq > 255:
            raise SnpxError("SNPX capture request limit exceeded")
        reply = self._exchange(_frame(2, seq, 0x200, 0xC0, 4, segment, index, count), size)
        try:
            return self._payload(reply, seq, size, segment)
        except SnpxError:
            self.__exit__(None, None, None)
            raise

    def read_words(self, address: int, count: int):
        self.policy.check_words(address, count)
        return self._read(8, address - 1, count, count * 2)

    def read_io(self, target: str):
        format_name = self.policy.io_format(target)
        segment, index = io_target(target)
        if format_name != "bool":
            payload = self._read(segment, index, 1, 2)
            return struct.unpack("<H" if format_name == "u16" else "<h", payload)[0]
        # Read the containing byte, then select the requested bit only.
        payload = self._read(segment, index - index % 8, 8, 1)
        return bool(payload[0] & (1 << (index % 8)))
