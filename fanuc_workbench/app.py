from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from fanuc_workbench.ui.main_window import MainWindow
from fanuc_workbench.identity import APP_NAME


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Local")
    window = MainWindow()
    window.show()
    return app.exec()
