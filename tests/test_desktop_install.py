from pathlib import Path
import struct
import sys

import pytest

from fanuc_workbench import desktop_install
from fanuc_workbench.identity import APP_NAME, application_icon


def test_application_artwork_contains_all_original_sizes_and_renders():
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    icon = application_icon()
    data = icon.read_bytes()
    assert struct.unpack_from("<HHH", data) == (0, 1, 8)
    sizes = []
    for n in range(8):
        width, height, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + n * 16)
        png = data[offset:offset + length]
        size = (width or 256, height or 256)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack_from(">II", png, 16) == size
        sizes.append(size[0])
        if size[0] == 256:
            assert png == icon.with_suffix(".png").read_bytes()
    assert sizes == [16, 20, 24, 32, 48, 64, 128, 256]
    image = QIcon(str(icon))
    assert not image.isNull()
    assert all(not image.pixmap(size, size).isNull() for size in sizes)


@pytest.fixture
def desktop_python(tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows shell integration")
    directory = tmp_path / "Desktop Python"
    directory.mkdir()
    for name in ("python.exe", "pythonw.exe"):
        (directory / name).touch()
    monkeypatch.setattr(sys, "executable", str(directory / "python.exe"))
    return directory


def test_real_shortcut_roundtrip_refresh_and_scoped_removal(tmp_path, desktop_python):
    from win32com.client import Dispatch

    programs = tmp_path / "Start Menu" / "Programs"
    shortcut = desktop_install.create_start_menu_shortcut(programs_directory=programs)
    assert shortcut == programs / APP_NAME / f"{APP_NAME}.lnk"
    shell = Dispatch("WScript.Shell")
    link = shell.CreateShortcut(str(shortcut))
    assert Path(link.TargetPath) == desktop_python / "pythonw.exe"
    assert link.Arguments == "-B -m fanuc_workbench.app"
    assert Path(link.WorkingDirectory) == Path(desktop_install.__file__).resolve().parent.parent
    assert link.IconLocation == f"{application_icon()},0"
    del link
    assert desktop_install.create_start_menu_shortcut(programs_directory=programs) == shortcut
    assert list(shortcut.parent.iterdir()) == [shortcut]
    other = shortcut.parent / "Keep.txt"
    other.write_text("Unrelated user entry", encoding="utf-8")
    desktop_install.remove_start_menu_shortcut(programs_directory=programs)
    assert not shortcut.exists() and other.read_text() == "Unrelated user entry"
    other.unlink()
    desktop_install.remove_start_menu_shortcut(programs_directory=programs)
    assert programs.is_dir() and not shortcut.parent.exists()
    desktop_install.remove_start_menu_shortcut(programs_directory=programs)


def test_failed_shortcut_save_preserves_existing_link(tmp_path, desktop_python, monkeypatch):
    shortcut = desktop_install.create_start_menu_shortcut(programs_directory=tmp_path)
    original = shortcut.read_bytes()

    class FailedLink:
        def __init__(self, path):
            self.path = Path(path)

        def Save(self):
            self.path.write_bytes(b"partial link")
            raise OSError("Shell save failed")

    class Shell:
        def CreateShortcut(self, path):
            return FailedLink(path)

    monkeypatch.setattr(desktop_install, "_windows_shell", Shell)
    with pytest.raises(OSError, match="Shell save failed"):
        desktop_install.create_start_menu_shortcut(programs_directory=tmp_path)
    assert shortcut.read_bytes() == original
    assert list(shortcut.parent.iterdir()) == [shortcut]


def test_shortcut_refuses_missing_pythonw_before_writing(tmp_path, desktop_python):
    (desktop_python / "pythonw.exe").unlink()
    programs = tmp_path / "Programs"
    with pytest.raises(FileNotFoundError, match="Desktop Python"):
        desktop_install.create_start_menu_shortcut(programs_directory=programs)
    assert not programs.exists()


def test_non_windows_installation_reports_failure_without_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop_install.main([]) == 1
    assert "require Windows" in capsys.readouterr().err
