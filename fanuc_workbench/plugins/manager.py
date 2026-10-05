from __future__ import annotations
from importlib.metadata import entry_points
from .base import WorkbenchPlugin
from .sdk import SDK_API_VERSION
ENTRY_POINT_GROUP='fanuc_workbench.plugins'

def _compatible(plugin)->bool:
    manifest=getattr(plugin,'manifest',None)
    if manifest is None:return True  # v0.x compatibility
    return str(getattr(manifest,'api_version','1')).split('.')[0]==SDK_API_VERSION.split('.')[0]

def discover_plugins()->list[WorkbenchPlugin]:
    plugins=[]
    try:eps=entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:eps=entry_points().get(ENTRY_POINT_GROUP,[])
    for ep in eps:
        try:
            obj=ep.load();plugin=obj() if isinstance(obj,type) else obj
            if getattr(plugin,'plugin_id',None) and getattr(plugin,'title',None) and _compatible(plugin):plugins.append(plugin)
        except Exception:continue
    return plugins
