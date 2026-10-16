"""Conservative LS validation and normalized source/readback comparison."""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from fanuc_workbench.services.native_values import fingerprint, program_name

TOKEN = re.compile(r'''"[^"\r\n]*"|'[^'\r\n]*'|[A-Za-z_$][A-Za-z0-9_$]*|(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?|[^\s]''')
GENERATED = re.compile(r"^\s*(OWNER|PROG_SIZE|PROGRAM_SIZE|CREATE|MODIFIED|FILE_NAME|LINE_COUNT|MEMORY_SIZE)\s*=", re.I)


def validate_source(data):
    if not isinstance(data, bytes) or not 1 <= len(data) <= 1024 * 1024:
        raise ValueError("Native deployment accepts 1 byte to 1 MiB of LS source")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("Initial native LS deployment requires ASCII source") from None
    if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        raise ValueError("LS source contains unsupported control characters")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = re.fullmatch(r"/PROG\s+([A-Z][A-Z0-9_]{0,7})", lines[0])
    if not first:
        raise ValueError("LS must start with one explicit /PROG application name")
    name = program_name(first[1])
    sections = [line.split()[0].upper() for line in lines if line.startswith("/")]
    for section in ["/PROG", "/ATTR", "/MN", "/POS", "/END"]:
        if sections.count(section) != 1:
            raise ValueError("LS source needs exactly one PROG/ATTR/MN/POS/END section")
    if not sections.index("/PROG") < sections.index("/ATTR") < sections.index("/MN") < sections.index("/POS") < sections.index("/END") or lines[-1] != "/END":
        raise ValueError("LS section order is invalid")
    body = re.split(r"^\s*/MN\s*$", text, maxsplit=1, flags=re.I | re.M)[1]
    body = re.split(r"^\s*/POS\s*$", body, maxsplit=1, flags=re.I | re.M)[0]
    definitions = re.findall(r"^\s*\d+\s*:\s*LBL\[(\d+)\]", body, re.I | re.M)
    jumps = re.findall(r"\bJMP\s+LBL\[(\d+)\]", body, re.I)
    if len(definitions) != len(set(definitions)) or set(jumps) - set(definitions):
        raise ValueError("LS labels are duplicated or a JMP target is missing")
    return {"program": name, "source_sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "validation": "structural; native compiler acceptance is separate"}


def normalized_listing(data):
    text = data.decode("ascii")
    rows = []
    for line in text.splitlines():
        if GENERATED.match(line):
            continue
        line = re.sub(r"^\s*\d+\s*:\s*", "", line)
        tokens = []
        for token in TOKEN.findall(line):
            if token[0].isdigit() or token[0] == '.' and len(token) > 1 and token[1].isdigit():
                token = str(Decimal(token).normalize())
            elif token[0] not in {'"', "'"}:
                token = token.upper()
            tokens.append(token)
        if tokens:
            rows.append(tokens)
    return rows


def verify_listing(source, readback):
    expected, actual = normalized_listing(source), normalized_listing(readback)
    return {"matches": expected == actual, "expected_normalized_sha256": fingerprint(expected),
            "actual_normalized_sha256": fingerprint(actual),
            "scope": "LS token/row comparison excluding generated owner/size/date/file/line-count fields; not runtime equivalence"}
