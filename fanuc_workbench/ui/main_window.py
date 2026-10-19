from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QProgressBar, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from fanuc_workbench.backends.factory import create_backend
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.core.settings import SettingsStore
from fanuc_workbench.core.workers import Worker
from fanuc_workbench.plugins.base import PluginContext
from fanuc_workbench.plugins.manager import discover_plugins
from fanuc_workbench.plugins.sdk import ServiceRegistry
from fanuc_workbench.services.backup import BackupService
from fanuc_workbench.services.controller import ControllerService
from fanuc_workbench.ui.connection_dialog import ConnectionDialog
from fanuc_workbench.ui.feature_tabs import (
    CompareRestoreTab, ProgramEditorTab, SearchAnalysisTab, DataTab, IOTab,
    DiagnosticsTab, ProjectTab, DocumentationTab,
)
from fanuc_workbench.ui.advanced_tabs import LiveMonitorTab, FleetTab, CommissioningTab, IntegrationTab, RoboGuideTab
from fanuc_workbench.ui.native_tab import NativeControllerTab
from fanuc_workbench.ui.mcp_access_dialog import McpAccessDialog
from fanuc_workbench.ui.layout_state import DesktopLayout


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__(); self.setWindowTitle('FanucMCPServer'); self.resize(1500,900)
        self.thread_pool=QThreadPool.globalInstance(); self.settings=SettingsStore(); self.profiles=self.settings.load_profiles(); self.current_profile=self.settings.preferred_profile(self.profiles); self.workspace: Path|None=None
        self.tabs=QTabWidget(); self.setCentralWidget(self.tabs)
        self.backup_tab=self._build_backup(); self.tabs.addTab(self.backup_tab,'Backup / History')
        self.compare_restore=CompareRestoreTab(self); self.tabs.addTab(self.compare_restore,'Compare / Restore')
        self.programs=ProgramEditorTab(self); self.tabs.addTab(self.programs,'Programs')
        self.search=SearchAnalysisTab(self); self.tabs.addTab(self.search,'Search / Analysis')
        self.data=DataTab(self); self.tabs.addTab(self.data,'Registers / Frames')
        self.io=IOTab(self); self.tabs.addTab(self.io,'I/O / PLC Map')
        self.diagnostics=DiagnosticsTab(self); self.tabs.addTab(self.diagnostics,'Diagnostics')
        self.project=ProjectTab(self); self.tabs.addTab(self.project,'Project / Cell')
        self.live_tab=LiveMonitorTab(self); self.tabs.addTab(self.live_tab,'Live / Recorder')
        self.native_tab=NativeControllerTab(self); self.tabs.addTab(self.native_tab,'Native Controller')
        self.fleet=FleetTab(self); self.tabs.addTab(self.fleet,'Fleet / Scheduler')
        self.commissioning=CommissioningTab(self); self.tabs.addTab(self.commissioning,'Commissioning')
        self.integration=IntegrationTab(self); self.tabs.addTab(self.integration,'Integration')
        self.roboguide=RoboGuideTab(self); self.tabs.addTab(self.roboguide,'ROBOGUIDE')
        self.docs=DocumentationTab(self); self.tabs.addTab(self.docs,'Documentation')
        self.files_tab=self._build_files(); self.tabs.addTab(self.files_tab,'Files')
        self.feature_tabs=[self.compare_restore,self.programs,self.search,self.data,self.io,self.diagnostics,self.live_tab,self.fleet,self.commissioning,self.integration,self.roboguide,self.docs]
        self.backup_tab.setObjectName('backup');self.files_tab.setObjectName('files');self.history.setObjectName('history')
        self._load_external_plugins(); self._menu(); self._update_profile(); self.desktop_layout=DesktopLayout(self); self.statusBar().showMessage('Configure Robot > Connection, then back up or open a workspace.')

    def _load_external_plugins(self):
        from fanuc_workbench.services.native_controller import NativeControllerService
        services=ServiceRegistry(); services.register('backend_factory',create_backend); services.register('backup_service',BackupService); services.register('controller_service',ControllerService); services.register('native_controller_service',NativeControllerService)
        context=PluginContext(get_workspace=lambda:self.workspace,get_profile=lambda:self.current_profile,log=lambda m:self.statusBar().showMessage(m),services=services)
        for plugin in discover_plugins():
            try:
                widget=plugin.create_widget(context);widget.setObjectName('plugin:'+plugin.plugin_id);self.tabs.addTab(widget,plugin.title)
            except Exception as exc:self.statusBar().showMessage(f'Plugin {plugin.plugin_id} failed: {exc}')

    def _menu(self):
        robot=self.menuBar().addMenu('Robot'); a=QAction('Connection…',self); a.triggered.connect(self.edit_connection); robot.addAction(a); a=QAction('Test connection',self); a.triggered.connect(self.test_connection); robot.addAction(a)
        ws=self.menuBar().addMenu('Workspace'); a=QAction('Open backup/workspace…',self); a.triggered.connect(self.choose_workspace); ws.addAction(a); a=QAction('Refresh all views',self); a.triggered.connect(self.refresh_workspace); ws.addAction(a)
        self.settings_menu = self.menuBar().addMenu('Settings')
        self.mcp_access_action = QAction('MCP access…', self)
        self.mcp_access_action.triggered.connect(self.edit_mcp_access)
        self.settings_menu.addAction(self.mcp_access_action)
        create=QAction('Create robot policy…',self);create.triggered.connect(self.create_robot_policy);self.settings_menu.addAction(create)

    def create_robot_policy(self):
        from fanuc_workbench.ui.policy_dialog import PolicyDialog
        dialog=PolicyDialog(self.current_profile,self)
        if dialog.exec():
            self._mcp_access_path=str(dialog.settings.path);self.native_tab.config_path.setText(self._mcp_access_path)
            self.settings.update_preferences(robot_policy_path=self._mcp_access_path)
            self.statusBar().showMessage('Policy created. Select it in MCP setup or click Load in Native Controller when ready.')

    def edit_mcp_access(self):
        path = self.native_tab.config_path.text() or getattr(self, '_mcp_access_path', '')
        dialog = McpAccessDialog(path, self)
        if dialog.exec():
            self._mcp_access_path = str(dialog.settings.path)
            self.settings.update_preferences(robot_policy_path=self._mcp_access_path)
            self.statusBar().showMessage(
                'MCP access policy saved. Reconnect MCP servers using that file; '
                'reload the Native Controller policy separately to apply the mode.')

    def _build_backup(self):
        w=QWidget(); l=QVBoxLayout(w); f=QFormLayout(); self.profile_label=QLabel('No robot configured'); self.dest=QLineEdit(str(Path.home()/'FanucBackups')); row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(self.dest); b=QPushButton('Browse…'); b.clicked.connect(self.choose_backup_destination); h.addWidget(b)
        self.profile_combo=QComboBox();self.profile_combo.currentIndexChanged.connect(self.select_profile)
        self.ascii=QCheckBox('Attempt ASCII companions (.TP→.LS, .VR→.VA)'); self.ascii.setChecked(True); self.depth=QSpinBox(); self.depth.setRange(1,32); self.depth.setValue(8); f.addRow('Saved robot',self.profile_combo);f.addRow('Robot',self.profile_label); f.addRow('Destination',row); f.addRow('Recursion limit',self.depth); f.addRow('ASCII export',self.ascii); l.addLayout(f)
        row2=QHBoxLayout(); self.backup_btn=QPushButton('Back Up Robot'); self.backup_btn.clicked.connect(self.start_backup); row2.addWidget(self.backup_btn); b=QPushButton('Refresh Backup History'); b.clicked.connect(self.refresh_history); row2.addWidget(b); row2.addStretch(); l.addLayout(row2)
        self.backup_progress_bar=QProgressBar();self.backup_progress_bar.setRange(0,100);self.backup_progress_bar.setValue(0);self.backup_progress_bar.setFormat('Ready');l.addWidget(self.backup_progress_bar)
        self.backup_progress_label=QLabel('Progress is based on listed files; companion attempts are included with their source file.');l.addWidget(self.backup_progress_label)
        self.backup_log=QPlainTextEdit(); self.backup_log.setReadOnly(True); self.backup_log.setMaximumHeight(220); l.addWidget(self.backup_log)
        l.addWidget(QLabel('Backup history')); self.history=QTableWidget(0,5); self.history.setHorizontalHeaderLabels(['Robot','Created','Host','Folder','Files']); self.history.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch); self.history.doubleClicked.connect(self.open_history); l.addWidget(self.history,1); return w

    def _build_files(self):
        w=QWidget(); l=QVBoxLayout(w); note=QLabel('Explicit raw file upload. Prefer Selective Restore for backup files because it previews categories and destinations.'); note.setWordWrap(True); l.addWidget(note); row=QHBoxLayout(); self.file_path=QLineEdit(); self.file_path.setReadOnly(True); row.addWidget(self.file_path,1); b=QPushButton('Choose local file…'); b.clicked.connect(self.choose_local_file); row.addWidget(b); l.addLayout(row); self.remote_path=QLineEdit('md:'); f=QFormLayout(); f.addRow('Remote destination',self.remote_path); l.addLayout(f); self.upload_btn=QPushButton('Upload file to robot…'); self.upload_btn.clicked.connect(self.upload_file); l.addWidget(self.upload_btn); self.file_log=QPlainTextEdit(); self.file_log.setReadOnly(True); l.addWidget(self.file_log,1); return w

    def _update_profile(self):
        self.profile_combo.blockSignals(True);self.profile_combo.clear()
        for profile in self.profiles:self.profile_combo.addItem(f'{profile.name} — {profile.host}',profile)
        if self.current_profile in self.profiles:self.profile_combo.setCurrentIndex(self.profiles.index(self.current_profile))
        self.profile_combo.blockSignals(False)
        if self.current_profile:self.profile_label.setText(f'{self.current_profile.name} — {self.current_profile.host} — {self.current_profile.transport.upper()} — roots: {", ".join(self.current_profile.roots)}')
        else:self.profile_label.setText('No robot configured')

    def select_profile(self, index):
        profile = self.profile_combo.itemData(index)
        if profile is not None:
            self.current_profile = profile
            self.settings.remember_profile(profile)
            self._update_profile()

    def remember_connection(self, profile):
        self.settings.remember_profile(profile, connected=True)

    def edit_connection(self):
        d=ConnectionDialog(self.current_profile,self)
        if not d.exec():return
        p=d.profile()
        if not p.host:QMessageBox.warning(self,'Missing host','Enter robot IP address or hostname.');return
        self.current_profile=p; i=next((i for i,x in enumerate(self.profiles) if x.name==p.name),None)
        if i is None:self.profiles.append(p)
        else:self.profiles[i]=p
        self.settings.save_profiles(self.profiles); self.settings.remember_profile(p); self._update_profile()

    def test_connection(self):
        if not self.current_profile:self.edit_connection()
        if not self.current_profile:return
        p=self.current_profile
        def work(*,progress):
            return ControllerService(p).test_connection()
        def succeeded(result):
            self.remember_connection(p)
            QMessageBox.information(self,'Connection OK','Connected successfully.\n'+'\n'.join(f'{k}: {v} entries' for k,v in result.items()))
        wk=Worker(work); wk.signals.result.connect(succeeded); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Connection failed',e)); self.thread_pool.start(wk)

    def choose_backup_destination(self):
        p=QFileDialog.getExistingDirectory(self,'Backup destination',self.dest.text())
        if p:self.dest.setText(p); self.refresh_history()

    def start_backup(self):
        if not self.current_profile:self.edit_connection()
        if not self.current_profile:return
        destination=Path(self.dest.text()).expanduser(); destination.mkdir(parents=True,exist_ok=True); p=self.current_profile; self._backup_profile=p; self.backup_btn.setEnabled(False); self.backup_log.appendPlainText(f'Starting backup of {p.name} ({p.host})')
        attempt_ascii,max_depth=self.ascii.isChecked(),self.depth.value()
        self.backup_progress_bar.setRange(0,0);self.backup_progress_bar.setFormat('Discovering files…')
        def work(*,progress):return ControllerService(p).backup(destination,attempt_ascii=attempt_ascii,max_depth=max_depth,progress=progress)
        wk=Worker(work); wk.signals.progress.connect(self.backup_progress); wk.signals.error.connect(self.backup_failed); wk.signals.result.connect(self.backup_finished); wk.signals.finished.connect(lambda:self.backup_btn.setEnabled(True)); self.thread_pool.start(wk)

    def backup_progress(self, event):
        if not isinstance(event,dict):self.backup_log.appendPlainText(str(event));return
        self.backup_log.appendPlainText(event.get('message',''))
        percent=event.get('percent')
        if percent is None:
            self.backup_progress_bar.setRange(0,0)
        else:
            self.backup_progress_bar.setRange(0,100);self.backup_progress_bar.setValue(percent)
            self.backup_progress_bar.setFormat(f"%p% — {event.get('completed_files',0)}/{event.get('total_files',0)} listed files processed")
        self.backup_progress_label.setText(event.get('message','')+f" — {event.get('failed_files',0)} failed, {event.get('warning_count',0)} warnings")

    def backup_failed(self, error):
        if self.backup_progress_bar.maximum()==0:self.backup_progress_bar.setRange(0,100);self.backup_progress_bar.setValue(0)
        self.backup_progress_bar.setFormat('Interrupted — %p%');self.backup_progress_label.setText('Backup interrupted; inspect the log and retained manifest.')
        QMessageBox.critical(self,'Backup failed',error)

    def backup_finished(self,path):
        if getattr(self,'_backup_profile',None) is not None:self.remember_connection(self._backup_profile)
        self.workspace=Path(path); self.backup_log.appendPlainText(f'Backup complete: {path}'); self.refresh_history(); self.refresh_workspace()
        manifest=json.loads((self.workspace/'backup_manifest.json').read_text(encoding='utf-8'));failed=sum(record.get('error') is not None for record in manifest.get('records',[]));warnings=len(manifest.get('warnings',[]))
        QMessageBox.information(self,'Backup finished',f'{path}\n\n{failed} failed files; {warnings} warnings. See the retained manifest for details.')

    def refresh_history(self):
        self.history.setRowCount(0); root=Path(self.dest.text()).expanduser()
        if not root.is_dir():return
        for folder in sorted([p for p in root.iterdir() if p.is_dir()],key=lambda p:p.stat().st_mtime,reverse=True):
            m=folder/'backup_manifest.json'
            if not m.exists():continue
            try:d=json.loads(m.read_text(encoding='utf-8'))
            except Exception:continue
            r=self.history.rowCount(); self.history.insertRow(r); vals=[d.get('robot_name',''),d.get('created_at',''),d.get('host',''),str(folder),len(d.get('records',[]))]
            for c,v in enumerate(vals):self.history.setItem(r,c,QTableWidgetItem(str(v)))
    def open_history(self,*_):
        r=self.history.currentRow()
        if r>=0:self.workspace=Path(self.history.item(r,3).text());self.refresh_workspace()

    def choose_workspace(self):
        p=QFileDialog.getExistingDirectory(self,'Open FANUC backup/workspace',str(self.workspace or Path.home()))
        if p:self.workspace=Path(p);self.refresh_workspace()

    def refresh_workspace(self):
        for tab in self.feature_tabs:
            try:tab.sync_workspace()
            except Exception as exc:self.statusBar().showMessage(f'View refresh warning: {exc}')
        if self.workspace:self.statusBar().showMessage(f'Workspace: {self.workspace}')

    def choose_local_file(self):
        p,_=QFileDialog.getOpenFileName(self,'Choose file')
        if p:self.file_path.setText(p)
    def upload_file(self):
        if not self.current_profile:self.edit_connection()
        if not self.current_profile:return
        local=Path(self.file_path.text()); remote=self.remote_path.text().strip()
        if not local.is_file():QMessageBox.warning(self,'No file','Choose a local file.');return
        if remote.endswith(':') or remote.endswith('/'):remote+=local.name
        if QMessageBox.warning(self,'Write to robot',f'Upload {local}\n\nto {self.current_profile.host}  {remote}?\n\nThis may overwrite controller data or a program. Verify robot safety and backup first.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        p=self.current_profile; self.upload_btn.setEnabled(False)
        def work(*,progress):
            return ControllerService(p).upload_file(local,remote)
        def uploaded(remote):
            self.remember_connection(p)
            self.file_log.appendPlainText(f'Uploaded {local.name} -> {remote}')
        wk=Worker(work); wk.signals.result.connect(uploaded); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Upload failed',e)); wk.signals.finished.connect(lambda:self.upload_btn.setEnabled(True)); self.thread_pool.start(wk)

    def closeEvent(self, event):
        if self.native_tab.busy:
            self.statusBar().showMessage('Wait for the native controller operation to finish before closing.')
            event.ignore()
            return
        self.native_tab.shutdown()
        self.desktop_layout.save()
        super().closeEvent(event)
