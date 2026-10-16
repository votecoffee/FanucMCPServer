"""Typed native values and deterministic review fingerprints, without vendor imports."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import struct

REGISTER = re.compile(r"(R|PR)\[([1-9][0-9]{0,5})(?:,([1-9][0-9]{0,5}))?\]")
IO = re.compile(r"(DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|F)\[([1-9][0-9]{0,5})\]")
PROGRAM = re.compile(r"[A-Z][A-Z0-9_]{0,7}")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def register_target(value):
    match = REGISTER.fullmatch(value) if isinstance(value, str) else None
    if not match or match[1] == "R" and match[3]:
        raise ValueError("Use an explicit R[index] or PR[group,index] target")
    kind = match[1]
    group, index = (int(match[2]), int(match[3])) if match[3] else (1, int(match[2]))
    if group > 8:
        raise ValueError("Position-register group must be between 1 and 8")
    return kind, group, index


def program_name(value):
    if not isinstance(value, str) or not PROGRAM.fullmatch(value):
        raise ValueError("Use an uppercase application program name of 1-8 letters, digits or underscores")
    if any(word in value for word in ("DCS", "MASTER", "SAFETY", "SYSMAC")):
        raise ValueError("System and safety programs are outside native deployment policy")
    return value


def real32(value):
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError("Native real values must be finite numbers")
    try:
        result = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error):
        raise ValueError("Value exceeds native 32-bit REAL range") from None
    if not math.isfinite(result):
        raise ValueError("Value exceeds native 32-bit REAL range")
    return result


def propose_register(before, change):
    if not isinstance(change, dict):
        raise ValueError("Register changes require a typed JSON object")
    proposed = copy.deepcopy(before)
    if before["kind"] == "R":
        if set(change) != {"type", "value"} or change["type"] not in {"integer", "real"}:
            raise ValueError("R changes require only type (integer/real) and value")
        value = change["value"]
        if change["type"] == "integer":
            if type(value) is not int or not -(2**31) <= value < 2**31:
                raise ValueError("Integer registers require a signed 32-bit integer")
        else:
            value = real32(value)
        proposed.update(type=change["type"], value=value)
    else:
        if not before.get("initialized"):
            raise ValueError("Initialize and review a PR before editing its existing axes")
        axes = change.get("values")
        if set(change) != {"values"} or not isinstance(axes, dict) or not axes or set(axes) - set(before["values"]):
            raise ValueError("PR changes may update existing axes only; frame, tool, configuration and representation stay bound")
        proposed["values"].update({axis: real32(value) for axis, value in axes.items()})
    if proposed == before:
        raise ValueError("Proposed native value is unchanged")
    return proposed


def register_matches(actual, proposed, *, tolerance=0.0001):
    if proposed["kind"] == "R":
        return actual == proposed
    other = dict(actual)
    values = other.pop("values", {})
    expected = dict(proposed)
    expected_values = expected.pop("values", {})
    return (other == expected and set(values) == set(expected_values)
            and all(type(values[key]) in {int, float} and math.isfinite(values[key])
                    and abs(values[key] - value) <= tolerance for key, value in expected_values.items()))
