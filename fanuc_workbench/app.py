from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from fanuc_workbench.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FANUC Workbench")
    app.setOrganizationName("Local")
    window = MainWindow()
    window.show()
    return app.exec()
