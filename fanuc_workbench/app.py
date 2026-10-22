from __future__ import annotations

import sys

from fanuc_workbench.desktop_runtime import DesktopSession
from fanuc_workbench.identity import APP_NAME, application_icon
from fanuc_workbench import __version__


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print(f"{APP_NAME} {__version__}")
        return 0
    with DesktopSession() as session:
        starting = True
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QIcon
            from fanuc_workbench.ui.main_window import MainWindow

            app = QApplication(sys.argv)
            app.setApplicationName(APP_NAME)
            app.setApplicationVersion(__version__)
            app.setOrganizationName("Local")
            app.setWindowIcon(QIcon(str(application_icon())))
            window = MainWindow()
            window.show()
            starting = False
            result = app.exec()
            from fanuc_workbench.bridge.resource_context import close_resource_context
            close_resource_context()
            return result
        except Exception:
            session.report(*sys.exc_info(), startup=starting)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
