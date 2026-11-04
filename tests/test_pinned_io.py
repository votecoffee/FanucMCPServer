"""Retain verification handles without per-file buffering or weaker sharing."""
import io
import os
import sys

import pytest

from fanuc_workbench.bridge import stable_launcher as launcher


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows retained-file handle contract")


def release_owned(items):
    for item in items:
        if isinstance(item, tuple):
            kernel, handle = item
            assert kernel.CloseHandle(handle)
        else:
            item.close()


def test_pinned_bytes_use_small_raw_handles_and_still_refuse_replacement(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    path = root / "synthetic.bin"
    raw = b"synthetic immutable bytes\n" * 1024
    path.write_bytes(raw)
    before = len(launcher._held)
    try:
        launcher.pinned_tree(root, launcher.inventory(root))
        owned = launcher._held[before:]
        streams = [item for item in owned if isinstance(item, io.IOBase)]
        assert len(streams) == 1
        assert isinstance(streams[0], io.FileIO)
        assert sum(sys.getsizeof(item) for item in streams) < 4096
        streams[0].seek(0)
        assert streams[0].read() == raw
        with pytest.raises(PermissionError):
            path.write_bytes(b"refused write")
        with pytest.raises(PermissionError):
            path.rename(root / "refused-rename.bin")
    finally:
        release_owned(launcher._held[before:])
        del launcher._held[before:]
    path.write_bytes(b"replacement allowed after owned pins close")


def test_failed_pin_hash_closes_only_its_new_handles(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    path = root / "synthetic.bin"
    path.write_bytes(b"original fixture")
    entries = launcher.inventory(root)
    entries[0]["sha256"] = "0" * 64
    before = len(launcher._held)
    with pytest.raises(ValueError, match="hash mismatch"):
        launcher.pinned_tree(root, entries)
    assert len(launcher._held) == before
    path.write_bytes(b"failed verification released the owned file handle")
