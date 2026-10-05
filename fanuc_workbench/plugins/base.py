from __future__ import annotations
from dataclasses import dataclass,field
from pathlib import Path
from typing import Callable,Protocol
from PySide6.QtWidgets import QWidget
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.plugins.sdk import ServiceRegistry

@dataclass(slots=True)
class PluginContext:
    get_workspace:Callable[[],Path|None]
    get_profile:Callable[[],RobotProfile|None]
    log:Callable[[str],None]
    services:ServiceRegistry=field(default_factory=ServiceRegistry)

class WorkbenchPlugin(Protocol):
    plugin_id:str
    title:str
    def create_widget(self,context:PluginContext)->QWidget:...
