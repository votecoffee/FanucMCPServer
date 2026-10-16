# FANUC Workbench Plugin SDK 1.0

Plugins are ordinary Python packages installed into the same environment as FANUC Workbench. They add tabs without modifying the core application.

## Entry point

```toml
[project.entry-points."fanuc_workbench.plugins"]
my_plugin = "my_package.plugin:MyPlugin"
```

## Contract

Use `PluginBase` + `PluginManifest` from `fanuc_workbench.plugins.sdk` and implement `create_widget(context)`.

```python
from PySide6.QtWidgets import QLabel
from fanuc_workbench.plugins.sdk import PluginBase, PluginManifest, SDK_API_VERSION

class MyPlugin(PluginBase):
    manifest = PluginManifest(
        plugin_id="com.example.robot_tool",
        title="Robot Tool",
        version="1.0.0",
        api_version=SDK_API_VERSION,
        capabilities=["workspace.read"],
    )

    def create_widget(self, context):
        workspace = context.get_workspace()
        backend_factory = context.services.get("backend_factory")
        return QLabel(f"Workspace: {workspace}")
```

The loader accepts legacy v0.x plugins with `plugin_id`, `title`, and `create_widget`; plugins declaring a manifest must match the SDK major API version.

## Context

`PluginContext` exposes:

- `get_workspace()` — current local backup/workspace or `None`.
- `get_profile()` — current robot profile or `None`.
- `log(message)` — send a short status message to the main window.
- `services` — versioned dependency registry. Core v2 exposes `backend_factory`, `backup_service`, and the shared `controller_service` class.

`controller_service(profile)` provides headless connection tests, transfers,
backup/restore entry points, restore preview, and live comparison. The Codex MCP
adapter uses this same class with its own configured access/review policy; see
[`docs/ROBOT_BRIDGE.md`](docs/ROBOT_BRIDGE.md). GUI tab plugins and MCP plugins are
different adapters over shared services.

Plugins should use public parsers/services rather than reaching into `MainWindow`. Controller writes must remain explicit and should present their own confirmation UI.
