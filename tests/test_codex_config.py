"""Disposable UTF-8 TOML only; these tests never select a real Codex profile."""
import copy
import hashlib
import os
import re
import tomllib

import pytest

from fanuc_workbench.bridge.codex_config import PLUGIN, SERVER, plugin_flags_bytes, set_plugin_flags

TEXT = '''# Unicode café / 日本語 / 🤖
model = "unchanged"
message = """Keep these literal-looking rows:
[plugins."fanuc-mcp-server@fanuc-mcp-server-local"]
enabled = false
"""
literal = ''' + "'''First\n[plugins.\"fanuc-mcp-server@fanuc-mcp-server-local\"]\nenabled = false\nLast'''" + '''
[unrelated]
enabled = true
values = ["α", "β"]
[plugins."other@example"]
enabled = true
[plugins."fanuc-mcp-server@fanuc-mcp-server-local"] # retain comment
enabled = false # keep this comment too
description = "工具"
[plugins."fanuc-mcp-server@fanuc-mcp-server-local".mcp_servers."fanuc-mcp-server"]
enabled = false
'''


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("windows", [False, True])
def test_utf8_flags_preserve_values_and_have_exact_line_endings(tmp_path, ending, windows):
    before = TEXT.replace("\n", ending).encode("utf-8")
    expected = copy.deepcopy(tomllib.loads(before.decode("utf-8")))
    expected["plugins"][PLUGIN]["enabled"] = True
    expected["plugins"][PLUGIN]["mcp_servers"][SERVER]["enabled"] = True
    path = tmp_path / "Disposable profile 日本語" / "config.toml"; path.parent.mkdir(); path.write_bytes(before)
    receipt = set_plugin_flags(path, True, True, expected_sha256=hashlib.sha256(before).hexdigest(), windows=windows)
    after = path.read_bytes()
    assert tomllib.loads(after.decode("utf-8")) == expected
    assert not after.startswith(b"\xef\xbb\xbf") and "🤖".encode() in after
    assert b"\r\r\n" not in after and re.search(rb"\r(?!\n)", after) is None
    assert b"# keep this comment too" in after
    use_crlf = windows or ending == "\r\n"
    assert after.count(b"\r\n") == (after.count(b"\n") if use_crlf else 0)
    assert receipt["crcrlf_count"] == 0 and receipt["unrelated_settings_preserved"]
    assert set_plugin_flags(path, True, True, expected_sha256=receipt["after_sha256"], windows=windows)["after_sha256"] == receipt["after_sha256"]


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_missing_real_section_ignores_headers_inside_multiline_values(ending):
    before = ('message = """keep\n[plugins.\"' + PLUGIN + '\"]\nenabled = false\n"""\n[other]\nvalue = "é"\n').replace("\n", ending).encode()
    after = plugin_flags_bytes(before, True, False, windows=True)
    data = tomllib.loads(after.decode())
    assert data["message"] == tomllib.loads(before.decode())["message"]
    assert data["plugins"][PLUGIN]["enabled"] and not data["plugins"][PLUGIN]["mcp_servers"][SERVER]["enabled"]
    assert after.count(b"\r\n") == after.count(b"\n")


@pytest.mark.parametrize("before", [b'a = 1\r\r\n', b'a = 1\r', b'a = "\xff"\n', b'a = [\n'])
def test_malformed_input_is_preserved_without_temporary_writes(tmp_path, before):
    path = tmp_path / "config.toml"; path.write_bytes(before)
    with pytest.raises((ValueError, UnicodeError)):
        set_plugin_flags(path, True, True, expected_sha256=hashlib.sha256(before).hexdigest())
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_stale_hash_and_concurrent_change_do_not_overwrite_settings(tmp_path, monkeypatch):
    from fanuc_workbench.bridge import codex_config
    path = tmp_path / "config.toml"; before = TEXT.encode(); path.write_bytes(before)
    with pytest.raises(ValueError, match="reviewed"):
        set_plugin_flags(path, True, True, expected_sha256="0" * 64)
    original = codex_config.tempfile.mkstemp
    def concurrent(*args, **kwargs):
        result = original(*args, **kwargs); path.write_bytes(b'model = "concurrent"\n'); return result
    monkeypatch.setattr(codex_config.tempfile, "mkstemp", concurrent)
    with pytest.raises(ValueError, match="concurrently"):
        set_plugin_flags(path, True, True, expected_sha256=hashlib.sha256(before).hexdigest())
    assert path.read_bytes() == b'model = "concurrent"\n' and list(tmp_path.iterdir()) == [path]


def test_write_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"; before = TEXT.encode(); path.write_bytes(before)
    def failure(*args): raise OSError("inert flush failure")
    monkeypatch.setattr(os, "fsync", failure)
    with pytest.raises(OSError):
        set_plugin_flags(path, True, True, expected_sha256=hashlib.sha256(before).hexdigest())
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_inline_source_layout_refuses_instead_of_rewriting_unrelated_values():
    before = ('[plugins]\n"' + PLUGIN + '" = {enabled = false}\n').encode()
    with pytest.raises(ValueError, match="source layout"):
        plugin_flags_bytes(before, True, True)
