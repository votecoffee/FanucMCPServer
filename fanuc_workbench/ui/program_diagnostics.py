"""Offline program tools; controller acquisition stays in its existing view."""
import json
from pathlib import Path

from PySide6.QtWidgets import (QComboBox,QFileDialog,QFormLayout,QHBoxLayout,QLabel,QLineEdit,
    QMessageBox,QPlainTextEdit,QPushButton,QSpinBox,QTabWidget,QVBoxLayout,QWidget)

from fanuc_workbench.core.workers import Worker
from fanuc_workbench.services.program_analysis import trace_logic,explain_wait,triggered_captures,semantic_compare,dependency_review
from fanuc_workbench.extensions.programs_v1_1_0.diagnostics import observations
from fanuc_workbench.services.program_packages import build_change_package
from fanuc_workbench.services.native_validation import validate_offline,review_validation
from fanuc_workbench.services.diagnostic_capture import DiagnosticCaptureSession,native_source_id


class ProgramDiagnosticsPanel(QWidget):
    MODES=('Logic instructions','Readers / writers','Control-flow edges','Why waiting?','Triggered captures',
           'Semantic comparison','Dependency requirements','Missing dependencies','Offline validation evidence')

    def __init__(self,owner):
        super().__init__();self.owner=owner;self.busy=False;self.result=None;self.capture_session=None
        layout=QVBoxLayout(self)
        self.workspace_label=QLabel();self.workspace_label.setWordWrap(True);layout.addWidget(self.workspace_label)
        self.tools=QTabWidget();layout.addWidget(self.tools)
        analysis=QWidget();form=QFormLayout(analysis)
        self.paths=QLineEdit();self.paths.setPlaceholderText('Explicit LS paths, separated by semicolons')
        row=QHBoxLayout();row.addWidget(self.paths,1);choose=QPushButton('Select LS files…');choose.clicked.connect(self.choose_listings);row.addWidget(choose);form.addRow('Selected listings',row)
        self.entry=QLineEdit();self.entry.setPlaceholderText('Entry program names, separated by semicolons');form.addRow('Package entries',self.entry)
        self.observation=self.file_row(form,'Diagnostic observations','JSON (*.json)')
        self.comparison=self.file_row(form,'Comparison listing','LS (*.LS *.ls)')
        self.inventory=self.file_row(form,'Dependency inventory','JSON (*.json)')
        self.validation=self.file_row(form,'Offline validation report','JSON (*.json)')
        self.compiled=self.file_row(form,'Compiled TP evidence','TP (*.TP *.tp)')
        self.readback=self.file_row(form,'Printed LS evidence','LS (*.LS *.ls)')
        self.signals=QLineEdit();self.signals.setPlaceholderText('DI[1]; R[1]');form.addRow('Trigger signals',self.signals)
        row=QHBoxLayout();self.before=QSpinBox();self.after=QSpinBox()
        for spin in (self.before,self.after): spin.setRange(0,100);spin.setValue(20)
        row.addWidget(QLabel('Before'));row.addWidget(self.before);row.addWidget(QLabel('After'));row.addWidget(self.after);row.addStretch();form.addRow('Capture window (samples)',row)
        self.mode=QComboBox();self.mode.addItems(self.MODES);form.addRow('Analysis',self.mode)
        self.tools.addTab(analysis,'Analyze preserved files')
        compiler=QWidget();form=QFormLayout(compiler)
        self.maketp=self.file_row(form,'MakeTP executable','Executable (*.exe)',relative=False)
        self.printtp=self.file_row(form,'PrintTP executable','Executable (*.exe)',relative=False)
        self.configuration=self.file_row(form,'Robot INI','INI (*.ini)',relative=False)
        self.robot_context=QLineEdit();row=QHBoxLayout();row.addWidget(self.robot_context,1);browse=QPushButton('Browse…');browse.clicked.connect(self.choose_context);row.addWidget(browse);form.addRow('Offline robot context',row)
        self.version=QLineEdit();self.version.setPlaceholderText('Explicit compatible core, for example V8.33-1');form.addRow('WinOLPC version',self.version)
        self.components=QLineEdit();self.components.setPlaceholderText('Explicit supporting files, separated by semicolons')
        row=QHBoxLayout();row.addWidget(self.components,1);browse=QPushButton('Select files…')
        def choose_components():
            paths,_=QFileDialog.getOpenFileNames(self,'Select reviewed supporting files',str(Path.home()),'All files (*)')
            if paths:self.components.setText('; '.join(paths))
        browse.clicked.connect(choose_components);row.addWidget(browse);form.addRow('Supporting components',row)
        self.component_evidence=QLineEdit();self.component_evidence.setPlaceholderText('Vendor manifest, wrapper or other source used to select the files');form.addRow('Component selection source',self.component_evidence)
        self.compile_button=QPushButton('Run isolated MakeTP → PrintTP validation…');self.compile_button.clicked.connect(self.compile);form.addRow(self.compile_button)
        note=QLabel('Copies the robot context and rebinds output paths into a new local directory. Pins the selected supporting files; dependency completeness and native compatibility remain unverified until tested. Preserves diagnostics and checks printed LS. Controller deployment is a separate reviewed operation.');note.setWordWrap(True);form.addRow(note)
        self.tools.addTab(compiler,'Native offline compiler')
        capture=QWidget();form=QFormLayout(capture)
        self.task_name=QLineEdit();self.task_name.setPlaceholderText('Exact task name from Native Controller status');form.addRow('Native task',self.task_name)
        self.state_map=QLineEdit('{}');form.addRow('Verified status code map (JSON)',self.state_map)
        self.capture_signals=QLineEdit();self.capture_signals.setPlaceholderText('DI[1]; R[1]');form.addRow('Signal changes',self.capture_signals)
        self.capture_before=QSpinBox();self.capture_after=QSpinBox()
        for spin in (self.capture_before,self.capture_after):spin.setRange(0,100);spin.setValue(20)
        form.addRow('Pre-trigger samples',self.capture_before);form.addRow('Post-trigger samples',self.capture_after)
        row=QHBoxLayout();arm=QPushButton('Arm for Native Controller reads');arm.clicked.connect(self.arm_capture);row.addWidget(arm);disarm=QPushButton('Disarm');disarm.clicked.connect(self.disarm_capture);row.addWidget(disarm);save=QPushButton('Save captured bundle…');save.clicked.connect(self.save_capture);row.addWidget(save);form.addRow(row)
        self.capture_label=QLabel('Not armed');form.addRow(self.capture_label)
        note=QLabel('Uses explicitly requested Native Controller samples. Arming does not connect or start polling. A trigger reads the current program LS through the existing native/file service and preserves its hash. Numeric task states require a verified map; alarm history remains separate from active alarm evidence.');note.setWordWrap(True);form.addRow(note)
        self.tools.addTab(capture,'Triggered native capture')
        row=QHBoxLayout();self.analyze_button=QPushButton('Analyze');self.analyze_button.clicked.connect(self.analyze);row.addWidget(self.analyze_button)
        package=QPushButton('Create reviewed change package…');package.clicked.connect(self.package);row.addWidget(package)
        export=QPushButton('Export displayed report…');export.clicked.connect(self.export);row.addWidget(export)
        self.action_buttons=[self.analyze_button,package,export]
        self.offset=QSpinBox();self.offset.setRange(0,100000);row.addWidget(QLabel('Row offset'));row.addWidget(self.offset);row.addStretch();layout.addLayout(row)
        self.report=QPlainTextEdit();self.report.setReadOnly(True);layout.addWidget(self.report,1)
        self.sync_workspace()

    def file_row(self,form,label,filter,relative=True):
        edit=QLineEdit();row=QHBoxLayout();row.addWidget(edit,1)
        button=QPushButton('Browse…')
        def browse():
            path,_=QFileDialog.getOpenFileName(self,label,str(self.owner.workspace or Path.home()),filter)
            if path:
                try: edit.setText(Path(path).resolve().relative_to(self.root()).as_posix() if relative else path)
                except ValueError: QMessageBox.warning(self,'Outside workspace','Choose evidence inside the active workspace.')
        button.clicked.connect(browse);row.addWidget(button);form.addRow(label,row)
        return edit

    def root(self):
        if not self.owner.workspace: raise ValueError('Open a backup/workspace first.')
        return Path(self.owner.workspace).resolve()

    def sync_workspace(self):
        self.workspace_label.setText('Offline workspace: '+str(self.owner.workspace or 'none selected'))

    @staticmethod
    def split(edit): return [value.strip() for value in edit.text().split(';') if value.strip()]

    def choose_listings(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Select explicit LS listings',str(self.owner.workspace or Path.home()),'LS (*.LS *.ls)')
        if paths:
            try:self.paths.setText('; '.join(Path(path).resolve().relative_to(self.root()).as_posix() for path in paths))
            except ValueError: QMessageBox.warning(self,'Outside workspace','Choose listings inside the active workspace.')

    def choose_context(self):
        path=QFileDialog.getExistingDirectory(self,'Offline robot context')
        if path:self.robot_context.setText(path)

    def destination(self,title):
        path,_=QFileDialog.getSaveFileName(self,title,str(Path.home()/'new_robot_review'))
        return Path(path) if path else None

    def run(self,work):
        if self.busy:return
        self.busy=True;self.result=None;self.tools.setEnabled(False);self.compile_button.setEnabled(False)
        for button in self.action_buttons:button.setEnabled(False)
        self.report.setPlainText('Working…')
        worker=Worker(lambda *,progress:work());self.worker=worker
        def show(result): self.result=result;self.report.setPlainText(json.dumps(result,indent=2,ensure_ascii=False))
        def finished():
            self.busy=False;self.tools.setEnabled(True);self.compile_button.setEnabled(True)
            for button in self.action_buttons:button.setEnabled(True)
        worker.signals.result.connect(show);worker.signals.error.connect(self.report.setPlainText);worker.signals.finished.connect(finished)
        self.owner.thread_pool.start(worker)

    def analyze(self):
        try:
            root=self.root();paths=self.split(self.paths);mode=self.mode.currentIndex();offset=self.offset.value()
            if not paths:raise ValueError('Select at least one LS listing.')
            observation=self.observation.text().strip();inventory=self.inventory.text().strip() or None
            entries=self.split(self.entry);comparison=self.comparison.text().strip()
            if mode<3: work=lambda:trace_logic(root,paths,view=('instructions','accesses','edges')[mode],offset=offset,limit=20)
            elif mode==3:
                def work():
                    rows,digest=observations(root,observation)
                    if not rows:raise ValueError('No preserved observations.')
                    return {**explain_wait(root,paths[0],rows[-1]),'observation_file_sha256':digest}
            elif mode==4:
                before,after,signals=self.before.value(),self.after.value(),self.split(self.signals)
                work=lambda:triggered_captures(root,paths,observation,before=before,after=after,signals=signals,offset=offset,limit=20)
            elif mode==5:work=lambda:semantic_compare(root,paths[0],comparison,offset=offset,limit=20)
            elif mode in (6,7):work=lambda:dependency_review(root,paths,entry_programs=entries,inventory_path=inventory,view='requirements' if mode==6 else 'missing',offset=offset,limit=20)
            else:
                report,compiled,readback=self.validation.text().strip(),self.compiled.text().strip() or None,self.readback.text().strip() or None
                work=lambda:review_validation(root,paths[0],report,compiled_path=compiled,readback_path=readback)
            self.run(work)
        except Exception as exc: self.report.setPlainText(str(exc))

    def package(self):
        try:
            root=self.root();paths=self.split(self.paths);entries=self.split(self.entry);inventory=self.inventory.text().strip() or None
            destination=self.destination('Create new reviewed package directory')
            if destination:self.run(lambda:build_change_package(root,paths,destination,entry_programs=entries,inventory_path=inventory))
        except Exception as exc:self.report.setPlainText(str(exc))

    def compile(self):
        try:
            root=self.root();paths=self.split(self.paths)
            if not paths:raise ValueError('Select the LS source on Analyze preserved files first.')
            # Validate the selected relative path before passing a local compiler.
            from fanuc_workbench.extensions.programs_v1_1_0.diagnostics import selected_bytes
            selected_bytes(root,paths[0],1024*1024)
            source=root/paths[0]
            options={'maketp':Path(self.maketp.text()),'printtp':Path(self.printtp.text()),'configuration':Path(self.configuration.text()),'robot_context':Path(self.robot_context.text()),'version':self.version.text().strip(),
                'components':[Path(value) for value in self.split(self.components)],'component_evidence':self.component_evidence.text().strip()}
            destination=self.destination('Create new isolated compiler validation directory')
            if destination:self.run(lambda:validate_offline(source,destination,**options))
        except Exception as exc:self.report.setPlainText(str(exc))

    def export(self):
        if self.result is None:return
        path,_=QFileDialog.getSaveFileName(self,'Export displayed program report',str(Path.home()/'program_review.json'),'JSON (*.json)')
        if path:
            from fanuc_workbench.core.settings import SettingsStore
            try:SettingsStore._write_json(Path(path),self.result)
            except (OSError,ValueError) as exc:QMessageBox.critical(self,'Report export failed',str(exc))

    def arm_capture(self):
        try:
            native=self.owner.native_tab;robot=native.selected()
            if self.capture_session and self.capture_session.recorder.received and QMessageBox.question(self,'New triggered capture','Replace the current captured data? Save its bundle first if you want to keep it.',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)!=QMessageBox.Yes:return
            if self.capture_session:self.capture_session.armed=False
            self.capture_session=DiagnosticCaptureSession(native_source_id(native.bridge,robot),task_name=self.task_name.text().strip(),
                status_states=json.loads(self.state_map.text()),before=self.capture_before.value(),after=self.capture_after.value(),signals=self.split(self.capture_signals))
            self.refresh_capture()
        except Exception as exc:self.capture_label.setText(str(exc))

    def refresh_capture(self):
        session=self.capture_session
        self.capture_label.setText(('Armed' if session.armed else 'Disarmed')+f' — {session.recorder.received} observations, {len(session.recorder.captures)} captures' if session else 'Not armed')

    def disarm_capture(self):
        if self.capture_session:self.capture_session.armed=False
        self.refresh_capture()

    def save_capture(self):
        if not self.capture_session:return
        self.disarm_capture();session=self.capture_session
        destination=self.destination('Save new triggered diagnostic bundle directory')
        if destination:self.run(lambda:session.save(destination))
