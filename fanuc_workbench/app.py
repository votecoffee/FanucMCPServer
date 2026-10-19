from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from fanuc_workbench.ui.main_window import MainWindow
from fanuc_workbench.identity import APP_NAME, application_icon


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Local")
    app.setWindowIcon(QIcon(str(application_icon())))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
