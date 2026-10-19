"""Native controller tools using the same policy and reviewed plans as MCP."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.core.workers import Worker
from fanuc_workbench.services.native_programs import validate_source
from fanuc_workbench.identity import configuration_environment


def pretty(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


class NativeControllerTab(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.bridge, self.plan, self.capture = owner, None, None, None
        self.busy, self.controls = False, []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        saved=owner.settings.preferences() if hasattr(owner,'settings') else {}
        self.config_path = QLineEdit(configuration_environment() or saved.get('robot_policy_path',''))
        row.addWidget(QLabel('Robot policy'))
        row.addWidget(self.config_path, 1)
        self.button(row, 'Browse…', self.choose_config)
        if hasattr(owner,'create_robot_policy'):self.button(row,'New policy…',owner.create_robot_policy)
        self.button(row, 'Load', self.load_config)
        self.robot = QComboBox()
        self.robot.currentIndexChanged.connect(self.clear_plan)
        row.addWidget(self.robot)
        layout.addLayout(row)
        self.policy = QLabel('Load a robot policy to use native data access and reviewed changes.')
        self.policy.setWordWrap(True)
        layout.addWidget(self.policy)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        tabs.addTab(self.live_ui(), 'Live Controller')
        tabs.addTab(self.register_ui(), 'Edit R / PR')
        tabs.addTab(self.program_ui(), 'Deploy Program')
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setPlaceholderText('Prepared change review and verification results appear here.')
        layout.addWidget(self.review, 1)
        row = QHBoxLayout()
        self.approve = self.button(row, 'Review and commit prepared change…', self.commit)
        self.cancel = self.button(row, 'Cancel prepared change', self.cancel_plan)
        self.approve.setEnabled(False)
        self.cancel.setEnabled(False)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)

    def button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.controls.append(button)
        return button

    def live_ui(self):
        widget, layout = QWidget(), QVBoxLayout()
        widget.setLayout(layout)
        self.expressions = QPlainTextEdit('R[999]\nDI[1]\nDO[1]')
        self.expressions.setMaximumHeight(90)
        layout.addWidget(QLabel('Watch expressions, one per line'))
        layout.addWidget(self.expressions)
        row = QHBoxLayout()
        self.button(row, 'Read values and controller status', self.refresh_live)
        self.button(row, 'List programs', self.list_programs)
        self.button(row, 'Reconnect native API', self.reconnect)
        self.auto = QCheckBox('Refresh automatically')
        self.auto.toggled.connect(self.toggle_poll)
        row.addWidget(self.auto)
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 60)
        self.seconds.setValue(2)
        self.seconds.setSuffix(' s')
        self.seconds.valueChanged.connect(lambda: self.toggle_poll(self.auto.isChecked()))
        row.addWidget(self.seconds)
        layout.addLayout(row)
        self.values = QTableWidget(0, 2)
        self.values.setHorizontalHeaderLabels(['Signal / Register', 'Native value or availability'])
        self.values.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.values)
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        layout.addWidget(self.status)
        return widget

    def register_ui(self):
        widget, layout = QWidget(), QVBoxLayout()
        widget.setLayout(layout)
        row = QHBoxLayout()
        self.target = QLineEdit('R[999]')
        self.target.textChanged.connect(self.clear_plan)
        row.addWidget(QLabel('Register'))
        row.addWidget(self.target)
        self.button(row, 'Read current value', self.read_register)
        self.button(row, 'Prepare value change', self.prepare_register)
        layout.addLayout(row)
        self.current = QPlainTextEdit()
        self.current.setReadOnly(True)
        layout.addWidget(QLabel('Preserved current value'))
        layout.addWidget(self.current)
        self.change = QPlainTextEdit()
        self.change.textChanged.connect(self.invalidate_edited_plan)
        self.change.setPlaceholderText('Read a register to fill its editable value fields.')
        layout.addWidget(QLabel('Proposed value — R: type/value; PR: existing axes only'))
        layout.addWidget(self.change)
        return widget

    def program_ui(self):
        widget, layout = QWidget(), QVBoxLayout()
        widget.setLayout(layout)
        row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.textChanged.connect(self.invalidate_edited_plan)
        row.addWidget(self.source, 1)
        self.button(row, 'Choose LS program…', self.choose_source)
        self.button(row, 'Validate and prepare deployment', self.prepare_program)
        layout.addLayout(row)
        row=QHBoxLayout();self.offline_report=QLineEdit();self.offline_report.setPlaceholderText('Optional preserved validation.json checked before preparing deployment')
        self.offline_report.textChanged.connect(self.invalidate_edited_plan);row.addWidget(self.offline_report,1)
        def choose_report():
            path,_=QFileDialog.getOpenFileName(self,'Offline compiler validation report','','JSON (*.json)')
            if path:self.offline_report.setText(path)
        self.button(row,'Offline compiler report…',choose_report);layout.addLayout(row)
        note = QLabel('Review the source, current controller program and exact proposal before loading. '
                      'Deployment loads/compiles and checks TP/LS readback. It never selects or runs the program.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.source_preview = QPlainTextEdit()
        self.source_preview.setReadOnly(True)
        layout.addWidget(self.source_preview)
        return widget

    def selected(self):
        if self.bridge is None or not self.robot.currentData():
            raise ValueError('Load a robot policy and select a robot first')
        return self.robot.currentData()

    def run(self, function, success):
        if self.busy:
            return
        self.busy = True
        self.set_busy(True)
        worker = Worker(lambda *, progress: function())
        self._worker, self._success = worker, success
        worker.signals.result.connect(self.received)
        worker.signals.error.connect(self.failed)
        worker.signals.finished.connect(self.finished)
        self.owner.thread_pool.start(worker)

    @Slot(object)
    def received(self, value):
        try:
            self._success(value)
        except Exception as exc:
            self.failed(str(exc))

    def set_busy(self, busy):
        for control in [*self.controls, self.config_path, self.robot, self.target, self.source, self.change, self.offline_report]:
            control.setEnabled(not busy)
        self.approve.setEnabled(not busy and self.plan is not None)
        self.cancel.setEnabled(not busy and self.plan is not None)

    @Slot()
    def finished(self):
        self.busy = False
        self._worker = None
        self.set_busy(False)

    @Slot(str)
    def failed(self, message):
        self.auto.setChecked(False)
        self.review.setPlainText(message)

    def choose_config(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Robot access policy', '', 'JSON policy (*.json)')
        if path:
            self.config_path.setText(path)

    def load_config(self):
        if self.busy:
            return
        try:
            candidate = RobotBridge.from_config(Path(self.config_path.text()))
            self.shutdown()
            self.bridge = candidate
            saved=self.owner.settings.preferences() if hasattr(self.owner,'settings') else {}
            self.robot.blockSignals(True)
            self.robot.clear()
            for identifier, access in candidate.config.robots.items():
                self.robot.addItem(f'{access.profile.name} ({identifier})', identifier)
            if hasattr(self.owner,'settings'):
                selected=saved.get('native_robot_id') if saved.get('robot_policy_path')==str(Path(self.config_path.text()).resolve()) else None
                index=self.robot.findData(selected)
                if index>=0:self.robot.setCurrentIndex(index)
                self.owner.settings.update_preferences(robot_policy_path=str(Path(self.config_path.text()).resolve()),native_robot_id=self.robot.currentData())
            self.robot.blockSignals(False)
            self.update_policy()
        except Exception as exc:
            self.robot.blockSignals(False)
            self.failed(str(exc))

    def update_policy(self):
        if self.bridge and self.robot.currentData():
            access = self.bridge.config.robots[self.robot.currentData()]
            mode = 'Read-only' if self.bridge.config.read_only else 'Reviewed writes enabled'
            self.policy.setText(f'{access.profile.host} | Access: {mode} | Native provider: {access.native_provider} | '
                f'Writable registers: {", ".join(access.native_register_targets) or "none"} | '
                f'Program targets: {", ".join(access.native_programs) or "none"}')

    def clear_capture(self):
        self.capture = None
        if hasattr(self, 'current'):
            self.current.clear()

    def clear_plan(self, *_):
        if self.plan and self.bridge:
            self.bridge.cancel_change(self.plan['plan_id'])
        self.plan = None
        self.clear_capture()
        if hasattr(self, 'approve'):
            self.set_busy(self.busy)
        self.update_policy()
        if self.bridge and self.robot.currentData() and hasattr(self.owner,'settings'):
            self.owner.settings.update_preferences(native_robot_id=self.robot.currentData())

    def invalidate_edited_plan(self):
        if self.plan:
            self.clear_plan()
            self.review.setPlainText('The proposal changed. Prepare it again before approval.')

    def toggle_poll(self, enabled):
        self.timer.start(self.seconds.value()*1000) if enabled else self.timer.stop()

    def poll(self):
        if not self.busy and self.bridge:
            self.refresh_live()

    def refresh_live(self):
        try:
            robot = self.selected()
            expressions = [line.strip() for line in self.expressions.toPlainText().splitlines() if line.strip()]
            diagnostics=getattr(getattr(self.owner,'search',None),'program_diagnostics',None)
            session=diagnostics.capture_session if diagnostics else None
            def work():
                data,status=self.bridge.native.sample(robot, expressions),self.bridge.native.status(robot)
                error=None
                if session and session.armed:
                    try:
                        from fanuc_workbench.services.diagnostic_capture import native_source_id
                        session.observe_native(data,status,native_source_id(self.bridge,robot),lambda name:self.bridge.native.read_program(robot,name))
                    except Exception as exc:session.armed=False;error=str(exc)
                return data,status,error
            def show(result):
                data,status,error=result
                rows = {**data['values'], **{key: {'unavailable': value} for key, value in data['errors'].items()}}
                self.values.setRowCount(len(rows))
                for index, (name, value) in enumerate(rows.items()):
                    self.values.setItem(index, 0, QTableWidgetItem(name))
                    self.values.setItem(index, 1, QTableWidgetItem(json.dumps(value)))
                self.status.setPlainText(pretty(status))
                if diagnostics and diagnostics.capture_session is session:
                    diagnostics.refresh_capture()
                    if error:diagnostics.capture_label.setText('Capture stopped: '+error)
            self.run(work, show)
        except Exception as exc:
            self.failed(str(exc))

    def list_programs(self):
        try:
            robot = self.selected()
            self.run(lambda: self.bridge.native.programs(robot, limit=500), lambda value: self.status.setPlainText(pretty(value)))
        except Exception as exc:
            self.failed(str(exc))

    def reconnect(self):
        try:
            robot = self.selected()
            self.clear_plan()
            self.run(lambda: self.bridge.native.reconnect(robot), lambda value: self.status.setPlainText(pretty(value)))
        except Exception as exc:
            self.failed(str(exc))

    def read_register(self):
        try:
            robot, target = self.selected(), self.target.text().strip()
            self.clear_plan()
            def show(capture):
                self.capture = capture
                self.current.setPlainText(pretty(capture))
                value = capture['value']
                change = {'type': value['type'], 'value': value['value']} if value['kind'] == 'R' else {'values': value.get('values', {})}
                self.change.setPlainText(pretty(change))
            self.run(lambda: self.bridge.native.read_register(robot, target), show)
        except Exception as exc:
            self.failed(str(exc))

    def prepared(self, plan):
        self.plan = plan
        self.auto.setChecked(False)
        self.review.setPlainText(pretty(plan))

    def prepare_register(self):
        try:
            robot, target = self.selected(), self.target.text().strip()
            if self.capture is None or self.capture['target'] != target:
                raise ValueError('Read this register before preparing a change')
            checksum, change = self.capture['sha256'], json.loads(self.change.toPlainText())
            if self.plan:
                self.bridge.cancel_change(self.plan['plan_id'])
                self.plan = None
            self.run(lambda: self.bridge.native.prepare_register(robot, target, change, checksum), self.prepared)
        except Exception as exc:
            self.failed(str(exc))

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self, 'LS source program', '', 'LS program (*.LS *.ls)')
        if path:
            self.source.setText(path)
            try:
                with Path(path).open('rb') as stream:
                    data = stream.read(1024*1024+1)
                validate_source(data)
                self.source_preview.setPlainText(data.decode('ascii')[:65536])
            except Exception as exc:
                self.failed(str(exc))

    def prepare_program(self):
        try:
            robot = self.selected()
            with Path(self.source.text()).open('rb') as stream:
                data = stream.read(1024*1024+1)
            info = validate_source(data)
            offline=None
            if self.offline_report.text().strip():
                from fanuc_workbench.extensions.programs_v1_1_0.diagnostics import read_json
                from fanuc_workbench.services.native_validation import review_validation
                path=Path(self.offline_report.text()).resolve();report,_=read_json(path.parent,path.name)
                offline=review_validation(path.parent,report['source_file'],path.name,
                    compiled_path=report.get('compiled_file'),readback_path=report.get('readback_file'))
                if (offline['reported_status']!='offline_checks_passed' or not offline['preserved_checks_pass']
                    or offline['source_sha256']!=info['source_sha256']):
                    raise ValueError('Offline compiler evidence failed or belongs to a different source revision. Inspect its diagnostics before preparing deployment.')
            self.clear_plan()
            def work():
                captured = self.bridge.native.read_program(robot, info['program'])
                staged = self.bridge.stage_file(info['program']+'.LS', base64.b64encode(data).decode(), hashlib.sha256(data).hexdigest())
                plan=self.bridge.native.prepare_program(robot, staged['source_file'], captured['sha256'])
                if offline is not None:plan['offline_validation']=offline
                return plan
            self.run(work, self.prepared)
        except Exception as exc:
            self.failed(str(exc))

    def commit(self):
        if self.plan is None or self.busy:
            return
        plan = self.plan
        try:
            review = self.bridge.change_review(plan['plan_id'], plan['proposed_sha256'])
        except Exception as exc:
            self.plan = None
            self.set_busy(False)
            self.failed(str(exc))
            return
        text = pretty(review)
        proposal = review['proposal']
        summary = (f"Robot: {proposal['robot_id']} @ {proposal['host']}\nTarget: {proposal['target']}\n"
                   f"Proposal SHA-256: {plan['proposed_sha256']}\n\n")
        if proposal['kind'] == 'register':
            summary += 'Before:\n' + pretty(proposal['before']) + '\n\nProposed:\n' + pretty(proposal['proposed'])
        else:
            summary += (f"Native LS load ({proposal['proposed']['operation']})\n"
                        f"Source SHA-256: {proposal['proposed']['source_sha256']}\n"
                        "The program will not be selected or run. Expand Details for source and current-program evidence.")
        dialog = QMessageBox(QMessageBox.Warning, 'Approve this exact native controller change?',
            summary, QMessageBox.Yes | QMessageBox.No, self)
        dialog.setDetailedText(text)
        dialog.setDefaultButton(QMessageBox.No)
        if dialog.exec() != QMessageBox.Yes:
            return
        self.plan = None
        self.run(lambda: self.bridge.commit_change(plan['plan_id'], plan['proposed_sha256'], client_approved=True),
                 lambda value: self.review.setPlainText(pretty(value)))

    def cancel_plan(self):
        try:
            self.clear_plan()
            self.review.setPlainText('Prepared change cancelled. Evidence retained.')
        except Exception as exc:
            self.failed(str(exc))

    def shutdown(self):
        self.timer.stop()
        self.auto.setChecked(False)
        diagnostics=getattr(getattr(self.owner,'search',None),'program_diagnostics',None)
        if diagnostics:diagnostics.disarm_capture()
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        self.plan = self.capture = None
