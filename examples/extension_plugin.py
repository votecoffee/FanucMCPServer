"""Minimal third-party plugin example.

Package this in another Python distribution and expose it with:

[project.entry-points."fanuc_workbench.plugins"]
example = "your_package.plugin:ExamplePlugin"
"""
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ExamplePlugin:
    plugin_id = "example.workspace-info"
    title = "Example Plugin"

    def create_widget(self, context):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        label = QLabel("Workspace: not selected")
        layout.addWidget(label)

        # Real plugins normally add their own refresh button/signals. The context
        # intentionally exposes stable getters instead of MainWindow internals.
        workspace = context.get_workspace()
        if workspace:
            label.setText(f"Workspace: {workspace}")
        return widget
