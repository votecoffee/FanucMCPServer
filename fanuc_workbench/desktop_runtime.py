"""Local diagnostics for the desktop process, including failures before Qt loads."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import traceback

from fanuc_workbench import __version__
from fanuc_workbench.identity import APP_NAME


def desktop_log_directory() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / APP_NAME / "logs"


def notify_desktop_error(log_path: Path | None, *, startup: bool) -> None:
    text = f"{APP_NAME} could not start." if startup else "An unexpected application error occurred."
    text += f"\n\nDiagnostic log:\n{log_path}" if log_path else "\n\nA diagnostic log could not be written."
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(None, APP_NAME, text)
            return
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
    elif sys.stderr is not None:
        print(text, file=sys.stderr)


class DesktopSession:
    """Rotate desktop logs and restore the process exception hook on exit."""

    def __init__(self, directory: Path | None = None, notify=None):
        self.directory = directory if directory is not None else desktop_log_directory()
        self.notify = notify if notify is not None else notify_desktop_error
        self.logger = logging.Logger("fanuc_workbench.desktop", logging.INFO)
        self.handler = None
        self.log_path = None
        self.reporting = False

    def __enter__(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / "desktop.log"
            self.handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
            self.handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(self.handler)
            self.log_path = path
        except OSError:
            # Read-only user data must not prevent the desktop from opening.
            self.logger.addHandler(logging.NullHandler())
        self.previous_hook = sys.excepthook
        sys.excepthook = self.exception_hook
        self.logger.info("Desktop starting: %s %s; Python %s", APP_NAME, __version__, sys.version.split()[0])
        return self

    def exception_hook(self, exc_type, value, tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            self.previous_hook(exc_type, value, tb)
            return
        self.report(exc_type, value, tb)

    def report(self, exc_type, value, tb, *, startup=False):
        self.logger.error("Desktop startup failed" if startup else "Unhandled desktop exception",
                          exc_info=(exc_type, value, tb))
        if self.reporting:
            return
        self.reporting = True
        try:
            self.notify(self.log_path, startup=startup)
        except Exception:
            if sys.stderr is not None:
                traceback.print_exception(exc_type, value, tb, file=sys.stderr)
        finally:
            self.reporting = False

    def __exit__(self, *_):
        if sys.excepthook == self.exception_hook:
            sys.excepthook = self.previous_hook
        self.logger.info("Desktop stopped")
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
