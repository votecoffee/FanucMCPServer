from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QSpinBox, QTabWidget,
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__(); self.setWindowTitle('FANUC Workbench'); self.resize(1500,900)
        self.thread_pool=QThreadPool.globalInstance(); self.settings=SettingsStore(); self.profiles=self.settings.load_profiles(); self.current_profile=self.profiles[0] if self.profiles else None; self.workspace: Path|None=None
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
        self._load_external_plugins(); self._menu(); self._update_profile(); self.statusBar().showMessage('Configure Robot > Connection, then back up or open a workspace.')

    def _load_external_plugins(self):
        from fanuc_workbench.services.native_controller import NativeControllerService
        services=ServiceRegistry(); services.register('backend_factory',create_backend); services.register('backup_service',BackupService); services.register('controller_service',ControllerService); services.register('native_controller_service',NativeControllerService)
        context=PluginContext(get_workspace=lambda:self.workspace,get_profile=lambda:self.current_profile,log=lambda m:self.statusBar().showMessage(m),services=services)
        for plugin in discover_plugins():
            try:self.tabs.addTab(plugin.create_widget(context),plugin.title)
            except Exception as exc:self.statusBar().showMessage(f'Plugin {plugin.plugin_id} failed: {exc}')

    def _menu(self):
        robot=self.menuBar().addMenu('Robot'); a=QAction('Connection…',self); a.triggered.connect(self.edit_connection); robot.addAction(a); a=QAction('Test connection',self); a.triggered.connect(self.test_connection); robot.addAction(a)
        ws=self.menuBar().addMenu('Workspace'); a=QAction('Open backup/workspace…',self); a.triggered.connect(self.choose_workspace); ws.addAction(a); a=QAction('Refresh all views',self); a.triggered.connect(self.refresh_workspace); ws.addAction(a)

    def _build_backup(self):
        w=QWidget(); l=QVBoxLayout(w); f=QFormLayout(); self.profile_label=QLabel('No robot configured'); self.dest=QLineEdit(str(Path.home()/'FanucBackups')); row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(self.dest); b=QPushButton('Browse…'); b.clicked.connect(self.choose_backup_destination); h.addWidget(b)
        self.ascii=QCheckBox('Attempt ASCII companions (.TP→.LS, .VR→.VA)'); self.ascii.setChecked(True); self.depth=QSpinBox(); self.depth.setRange(1,32); self.depth.setValue(8); f.addRow('Robot',self.profile_label); f.addRow('Destination',row); f.addRow('Recursion limit',self.depth); f.addRow('ASCII export',self.ascii); l.addLayout(f)
        row2=QHBoxLayout(); self.backup_btn=QPushButton('Back Up Robot'); self.backup_btn.clicked.connect(self.start_backup); row2.addWidget(self.backup_btn); b=QPushButton('Refresh Backup History'); b.clicked.connect(self.refresh_history); row2.addWidget(b); row2.addStretch(); l.addLayout(row2)
        self.backup_log=QPlainTextEdit(); self.backup_log.setReadOnly(True); self.backup_log.setMaximumHeight(220); l.addWidget(self.backup_log)
        l.addWidget(QLabel('Backup history')); self.history=QTableWidget(0,5); self.history.setHorizontalHeaderLabels(['Robot','Created','Host','Folder','Files']); self.history.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch); self.history.doubleClicked.connect(self.open_history); l.addWidget(self.history,1); return w

    def _build_files(self):
        w=QWidget(); l=QVBoxLayout(w); note=QLabel('Explicit raw file upload. Prefer Selective Restore for backup files because it previews categories and destinations.'); note.setWordWrap(True); l.addWidget(note); row=QHBoxLayout(); self.file_path=QLineEdit(); self.file_path.setReadOnly(True); row.addWidget(self.file_path,1); b=QPushButton('Choose local file…'); b.clicked.connect(self.choose_local_file); row.addWidget(b); l.addLayout(row); self.remote_path=QLineEdit('md:'); f=QFormLayout(); f.addRow('Remote destination',self.remote_path); l.addLayout(f); self.upload_btn=QPushButton('Upload file to robot…'); self.upload_btn.clicked.connect(self.upload_file); l.addWidget(self.upload_btn); self.file_log=QPlainTextEdit(); self.file_log.setReadOnly(True); l.addWidget(self.file_log,1); return w

    def _update_profile(self):
        if self.current_profile:self.profile_label.setText(f'{self.current_profile.name} — {self.current_profile.host} — {self.current_profile.transport.upper()} — roots: {", ".join(self.current_profile.roots)}')
        else:self.profile_label.setText('No robot configured')

    def edit_connection(self):
        d=ConnectionDialog(self.current_profile,self)
        if not d.exec():return
        p=d.profile()
        if not p.host:QMessageBox.warning(self,'Missing host','Enter robot IP address or hostname.');return
        self.current_profile=p; i=next((i for i,x in enumerate(self.profiles) if x.name==p.name),None)
        if i is None:self.profiles.append(p)
        else:self.profiles[i]=p
        self.settings.save_profiles(self.profiles); self._update_profile()

    def test_connection(self):
        if not self.current_profile:self.edit_connection()
        if not self.current_profile:return
        p=self.current_profile
        def work(*,progress):
            return ControllerService(p).test_connection()
        wk=Worker(work); wk.signals.result.connect(lambda x:QMessageBox.information(self,'Connection OK','Connected successfully.\n'+'\n'.join(f'{k}: {v} entries' for k,v in x.items()))); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Connection failed',e)); self.thread_pool.start(wk)

    def choose_backup_destination(self):
        p=QFileDialog.getExistingDirectory(self,'Backup destination',self.dest.text())
        if p:self.dest.setText(p); self.refresh_history()

    def start_backup(self):
        if not self.current_profile:self.edit_connection()
        if not self.current_profile:return
        destination=Path(self.dest.text()).expanduser(); destination.mkdir(parents=True,exist_ok=True); p=self.current_profile; self.backup_btn.setEnabled(False); self.backup_log.appendPlainText(f'Starting backup of {p.name} ({p.host})')
        def work(*,progress):return ControllerService(p).backup(destination,attempt_ascii=self.ascii.isChecked(),max_depth=self.depth.value(),progress=progress)
        wk=Worker(work); wk.signals.progress.connect(lambda x:self.backup_log.appendPlainText(x.get('message',str(x)) if isinstance(x,dict) else str(x))); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Backup failed',e)); wk.signals.result.connect(self.backup_finished); wk.signals.finished.connect(lambda:self.backup_btn.setEnabled(True)); self.thread_pool.start(wk)

    def backup_finished(self,path):
        self.workspace=Path(path); self.backup_log.appendPlainText(f'Backup complete: {path}'); self.refresh_history(); self.refresh_workspace(); QMessageBox.information(self,'Backup complete',str(path))

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
        wk=Worker(work); wk.signals.result.connect(lambda x:self.file_log.appendPlainText(f'Uploaded {local.name} -> {x}')); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Upload failed',e)); wk.signals.finished.connect(lambda:self.upload_btn.setEnabled(True)); self.thread_pool.start(wk)

    def closeEvent(self, event):
        if self.native_tab.busy:
            self.statusBar().showMessage('Wait for the native controller operation to finish before closing.')
            event.ignore()
            return
        self.native_tab.shutdown()
        super().closeEvent(event)
