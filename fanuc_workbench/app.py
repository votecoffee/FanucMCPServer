from __future__ import annotations

import sys

from fanuc_workbench.desktop_runtime import DesktopSession
from fanuc_workbench.identity import APP_NAME, application_icon


def main() -> int:
    with DesktopSession() as session:
        starting = True
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QIcon
            from fanuc_workbench.ui.main_window import MainWindow

            app = QApplication(sys.argv)
            app.setApplicationName(APP_NAME)
            app.setOrganizationName("Local")
            app.setWindowIcon(QIcon(str(application_icon())))
            window = MainWindow()
            window.show()
            starting = False
            return app.exec()
        except Exception:
            session.report(*sys.exc_info(), startup=starting)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
