"""Explicit local publication helper; not an MCP tool or automatic config repair."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

PLUGIN = "fanuc-mcp-server@fanuc-mcp-server-local"
SERVER = "fanuc-mcp-server"


def _visible_lines(text):
    """Skip headers/keys inside TOML multiline basic and literal strings."""
    state = None
    for number, line in enumerate(text.splitlines(keepends=True)):
        if state is None:
            yield number, line
        i = 0
        while i < len(line):
            if state is not None:
                if state == '"""' and line[i] == "\\":
                    i += 2
                elif line.startswith(state, i):
                    i += 3; state = None
                else:
                    i += 1
            elif line[i] == "#":
                break
            elif line.startswith(('"""', "'''"), i):
                state = line[i:i+3]; i += 3
            elif line[i] in "\"'":
                quote = line[i]; i += 1
                while i < len(line):
                    if quote == '"' and line[i] == "\\": i += 2
                    elif line[i] == quote: i += 1; break
                    else: i += 1
            else:
                i += 1


def _header(line):
    if not line.lstrip().startswith("["):
        return None
    try:
        data = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(data, dict) and len(data) == 1:
        key, data = next(iter(data.items())); path.append(key)
    return tuple(path) if data == {} else ()  # Array tables still terminate the previous block.


def plugin_flags_bytes(before, plugin_enabled, server_enabled, *, windows=None):
    """Change only FANUC's two flags. Windows output is explicit UTF-8 CRLF."""
    if type(plugin_enabled) is not bool or type(server_enabled) is not bool:
        raise ValueError("Plugin and server flags must be booleans")
    if b"\r\r\n" in before or re.search(rb"\r(?!\n)", before):
        raise ValueError("Malformed CR line endings; inspect the original without automatic repair")
    original = tomllib.loads(before.decode("utf-8"))
    expected = copy.deepcopy(original)
    try:
        entry = expected.setdefault("plugins", {}).setdefault(PLUGIN, {})
        entry["enabled"] = plugin_enabled
        entry.setdefault("mcp_servers", {}).setdefault(SERVER, {})["enabled"] = server_enabled
    except (AttributeError, TypeError):
        raise ValueError("Unsupported plugin settings structure") from None
    text = before.decode("utf-8").replace("\r\n", "\n")
    for path, value in ((("plugins", PLUGIN), plugin_enabled),
                        (("plugins", PLUGIN, "mcp_servers", SERVER), server_enabled)):
        lines = text.splitlines(keepends=True); visible = dict(_visible_lines(text))
        headers = [(i, _header(line)) for i, line in visible.items() if _header(line) is not None]
        matches = [(i, n) for n, (i, keys) in enumerate(headers) if keys == path]
        if len(matches) > 1: raise ValueError("Ambiguous plugin section")
        if matches:
            start, number = matches[0]; end = headers[number+1][0] if number+1 < len(headers) else len(lines)
            pattern = re.compile(r"^(\s*(?:enabled|\"enabled\"|'enabled')\s*=\s*)(true|false)([ \t]*(?:#[^\n]*)?)(\n?)$")
            keys = [(i, pattern.fullmatch(visible[i])) for i in range(start+1, end) if i in visible]
            keys = [(i, match) for i, match in keys if match]
            if len(keys) > 1: raise ValueError("Ambiguous plugin enabled flag")
            if keys:
                i, match = keys[0]
                lines[i] = match[1] + str(value).lower() + match[3] + match[4]
            else:
                # If the key exists in an unsupported source form, parsed equality below refuses.
                lines.insert(start+1, "enabled = " + str(value).lower() + "\n")
                if not lines[start].endswith("\n"): lines[start] += "\n"
            text = "".join(lines)
        else:
            header = "[" + ".".join(json.dumps(key, ensure_ascii=False) for key in path) + "]"
            text += ("" if text.endswith("\n") or not text else "\n") + "\n" + header + "\nenabled = " + str(value).lower() + "\n"
    try:
        if tomllib.loads(text) != expected:
            raise ValueError("Unrelated TOML settings would change")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("Unsupported plugin settings source layout; no config was written") from exc
    if windows is None: windows = os.name == "nt"
    newline = "\r\n" if windows or b"\r\n" in before else "\n"
    payload = text.replace("\n", newline).encode("utf-8")
    if tomllib.loads(payload.decode("utf-8")) != expected:
        raise ValueError("Line ending conversion changed TOML values")
    return payload


def set_plugin_flags(path, plugin_enabled, server_enabled, *, expected_sha256, windows=None):
    """Explicit target and stale-byte check; callers retain their own review/backup."""
    path = Path(path)
    if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("Config must be an ordinary file")
    before = path.read_bytes()
    if hashlib.sha256(before).hexdigest() != expected_sha256:
        raise ValueError("Config differs from its reviewed SHA-256")
    payload = plugin_flags_bytes(before, plugin_enabled, server_enabled, windows=windows)
    descriptor, temporary = tempfile.mkstemp(prefix=".fanuc-config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        if path.read_bytes() != before:
            raise ValueError("Config changed concurrently; no replacement was made")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"before_sha256": expected_sha256, "after_sha256": hashlib.sha256(payload).hexdigest(),
            "unrelated_settings_preserved": True, "bytes": len(payload),
            "crlf_count": payload.count(b"\r\n"), "lf_count": payload.count(b"\n"),
            "crcrlf_count": payload.count(b"\r\r\n")}
