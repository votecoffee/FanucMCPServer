"""FANUC Workbench plugin SDK template.

Install your package with an entry point:
[project.entry-points."fanuc_workbench.plugins"]
my_plugin = "my_package.plugin:MyPlugin"
"""
from PySide6.QtWidgets import QLabel,QVBoxLayout,QWidget
from fanuc_workbench.plugins.sdk import PluginBase,PluginManifest,SDK_API_VERSION

class MyPlugin(PluginBase):
    manifest=PluginManifest('com.example.myplugin','My FANUC Tool','1.0.0',SDK_API_VERSION,'Example external tab',['workspace.read'])
    def create_widget(self,context):
        w=QWidget();l=QVBoxLayout(w);l.addWidget(QLabel(f'Workspace: {context.get_workspace()}'));return w
