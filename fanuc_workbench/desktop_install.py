"""Install or remove the current user's Windows desktop shortcut."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from uuid import uuid4

from fanuc_workbench.identity import APP_NAME, application_icon


def _windows_shell():
    if sys.platform != "win32":
        raise RuntimeError("Start menu shortcuts require Windows")
    from win32com.client import Dispatch

    return Dispatch("WScript.Shell")


def _shortcut_path(shell, programs_directory: Path | None) -> Path:
    programs = programs_directory if programs_directory is not None else Path(shell.SpecialFolders("Programs"))
    return programs.resolve() / APP_NAME / f"{APP_NAME}.lnk"


def create_start_menu_shortcut(*, programs_directory: Path | None = None) -> Path:
    """Point a per-user shortcut at this desktop Python installation.

    The optional directory supports an alternative Start menu location and local
    verification. Reinstallation replaces only this application's shortcut.
    """
    shell = _windows_shell()
    shortcut = _shortcut_path(shell, programs_directory)
    pythonw = Path(sys.executable).resolve().with_name("pythonw.exe")
    if not pythonw.is_file():
        raise FileNotFoundError(f"Desktop Python executable is missing: {pythonw}")
    icon = application_icon()
    if not icon.is_file():
        raise FileNotFoundError(f"Application icon is missing: {icon}")
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    temporary = shortcut.with_name(f"{APP_NAME}.{uuid4().hex}.lnk")
    try:
        link = shell.CreateShortcut(str(temporary))
        link.TargetPath = str(pythonw)
        link.Arguments = "-B -m fanuc_workbench.app"
        link.WorkingDirectory = str(Path(__file__).resolve().parent.parent)
        link.IconLocation = f"{icon},0"
        link.Description = "FANUC robot files, diagnostics and reviewed changes"
        link.WindowStyle = 1
        link.Save()
        temporary.replace(shortcut)
    finally:
        temporary.unlink(missing_ok=True)
    return shortcut


def remove_start_menu_shortcut(*, programs_directory: Path | None = None) -> Path:
    """Remove only the application shortcut, retaining other Start menu entries."""
    shortcut = _shortcut_path(_windows_shell(), programs_directory)
    shortcut.unlink(missing_ok=True)
    if shortcut.parent.is_dir() and not any(shortcut.parent.iterdir()):
        shortcut.parent.rmdir()
    return shortcut


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="Remove the Start menu shortcut")
    args = parser.parse_args(argv)
    try:
        path = remove_start_menu_shortcut() if args.remove else create_start_menu_shortcut()
    except Exception as exc:
        print(f"Start menu shortcut failed: {exc}", file=sys.stderr)
        return 1
    print(f"{'Removed' if args.remove else 'Created'} Start menu shortcut: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
