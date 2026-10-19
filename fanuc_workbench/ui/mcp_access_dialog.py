"""Operator-owned controller access mode for a selected MCP policy file."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)

from fanuc_workbench.bridge.access_settings import load_access_settings, save_access_settings
from fanuc_workbench.identity import configuration_environment


class McpAccessDialog(QDialog):
    def __init__(self, config_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("MCP access")
        self.resize(620, 330)
        self.settings = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select the same robot policy file used by the MCP server."))
        row = QHBoxLayout()
        self.config_path = QLineEdit(config_path or configuration_environment() or "")
        row.addWidget(self.config_path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        load = QPushButton("Load")
        load.clicked.connect(self.load_policy)
        row.addWidget(load)
        create = QPushButton('New policy…')
        create.clicked.connect(self.create_policy)
        row.addWidget(create)
        advanced = QPushButton('All safeguards…')
        advanced.clicked.connect(self.edit_safeguards)
        row.addWidget(advanced)
        layout.addLayout(row)
        self.read_only = QCheckBox("Read-only controller access")
        self.read_only.setChecked(True)
        self.read_only.setEnabled(False)
        layout.addWidget(self.read_only)
        note = QLabel(
            "Checked: MCP can read the controller and save local backups, analyses and edits. "
            "Controller file uploads, description changes, R/PR value changes and program "
            "deployment are blocked.\n\n"
            "Unchecked: reviewed writes are permitted only for the exact configured targets, "
            "with the existing approval and readback checks. This does not grant motion or "
            "program execution.\n\n"
            "Save changes this policy file only. Reconnect each MCP server using it to apply "
            "the mode; existing sessions retain their previous policy. Reload the policy in "
            "the Native Controller tab separately. Other desktop file/restore tools have "
            "their own confirmations."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("Load a policy before changing its controller access mode.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.buttons.accepted.connect(self.save_policy)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.config_path.textChanged.connect(self.invalidate_policy)
        if self.config_path.text():
            self.load_policy()

    def invalidate_policy(self):
        self.settings = None
        self.read_only.setChecked(True)
        self.read_only.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.status.setText("Load this policy before changing its controller access mode.")

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "MCP robot policy", "", "JSON policy (*.json)")
        if path:
            self.config_path.setText(path)
            self.load_policy()

    def create_policy(self):
        from fanuc_workbench.ui.policy_dialog import PolicyDialog
        dialog = PolicyDialog(getattr(self.parent(), 'current_profile', None), self)
        if dialog.exec():
            self.config_path.setText(str(dialog.settings.path))
            self.load_policy()

    def edit_safeguards(self):
        from fanuc_workbench.ui.safeguards_dialog import SafeguardsDialog
        dialog = SafeguardsDialog(self.config_path.text(), parent=self)
        dialog.exec()
        if dialog.saved_policy_path:
            self.config_path.setText(dialog.saved_policy_path)
            self.load_policy()

    def load_policy(self):
        self.invalidate_policy()
        try:
            if not self.config_path.text().strip():
                raise ValueError("Select a policy file")
            self.settings = load_access_settings(Path(self.config_path.text()))
            self.read_only.setChecked(self.settings.read_only)
            self.read_only.setEnabled(True)
            self.buttons.button(QDialogButtonBox.Save).setEnabled(True)
            mode = "Read-only" if self.settings.read_only else "Reviewed writes enabled"
            self.status.setText(f"Saved mode: {mode}. Loaded MCP sessions may have a different mode.")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.status.setText("Could not load a valid robot policy. Check the file and try again.")

    def save_policy(self):
        if self.settings is None:
            return
        try:
            self.settings = save_access_settings(self.settings, read_only=self.read_only.isChecked())
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.invalidate_policy()
            self.status.setText("Could not save: the file changed or is unavailable. Load it again before saving.")
            return
        self.accept()
