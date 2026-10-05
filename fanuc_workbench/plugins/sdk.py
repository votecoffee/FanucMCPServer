from __future__ import annotations
from dataclasses import dataclass,field
from typing import Any,Callable

SDK_API_VERSION='1.0'
@dataclass(slots=True)
class PluginManifest:
    plugin_id:str
    title:str
    version:str='0.1.0'
    api_version:str=SDK_API_VERSION
    description:str=''
    capabilities:list[str]=field(default_factory=list)

class ServiceRegistry:
    """Small dependency registry exposed to plugins without coupling them to MainWindow."""
    def __init__(self):self._services:dict[str,Any]={}
    def register(self,name:str,service:Any):
        if not name or name.startswith('_'):raise ValueError('Invalid service name')
        self._services[name]=service
    def get(self,name:str,default=None):return self._services.get(name,default)
    def names(self):return tuple(sorted(self._services))

class PluginBase:
    manifest=PluginManifest('example.plugin','Example Plugin')
    @property
    def plugin_id(self):return self.manifest.plugin_id
    @property
    def title(self):return self.manifest.title
