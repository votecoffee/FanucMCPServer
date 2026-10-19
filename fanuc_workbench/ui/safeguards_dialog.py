"""Complete operator policy controls, separate from live controller operations."""
import copy
import json
from pathlib import Path
import re
import secrets

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPlainTextEdit,
    QMessageBox, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from fanuc_workbench.bridge.policy_editor import BrokerRuntime, load_policy, review_policy, save_policy
from fanuc_workbench.bridge.policy_fields import (SERVER_FIELDS, PROFILE_FIELDS, CONNECTION_FIELDS,
    WRITE_FIELDS, HTTP_FIELDS, SNPX_FIELDS, BROKER_FIELDS, BROKER_SERVER_FIELDS, BUILT_IN_GUARDS)


def lines(value, kind):
    if kind == "formats":
        return "\n".join(f"{key}={value}" for key, value in value.items())
    if kind == "ranges":
        return "\n".join(f"{row['address']},{row['count']}" for row in value)
    return "\n".join(value or [])


def parse_lines(text, kind):
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if kind == "ranges":
        result = []
        for row in rows:
            if not re.fullmatch(r"[0-9]+\s*,\s*[0-9]+", row):
                raise ValueError("Word ranges require address,count on each line")
            address, count = map(int, row.split(","))
            result.append({"address": address, "count": count})
        return result
    if kind == "formats":
        result = {}
        for row in rows:
            if row.count("=") != 1:
                raise ValueError("Decoding formats require target=u16 or target=i16 on each line")
            key, value = (item.strip() for item in row.split("="))
            if key in result:
                raise ValueError("Decoding formats cannot repeat a target")
            result[key] = value
        return result
    return rows


class PolicyPane(QWidget):
    saved = Signal(str)

    def __init__(self, kind, path="", parent=None):
        super().__init__(parent)
        self.kind, self.broker = kind, None
        self.snapshot = self.reviewed = None
        self.draft, self.widgets, self.errors, self.raw_inputs = {}, {}, {}, {}
        self.snpx_cache = {}
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.config_path = QLineEdit(path)
        row.addWidget(self.config_path, 1)
        for title, callback in (("Browse…", self.browse), ("Load", self.load_document)):
            button = QPushButton(title); button.clicked.connect(callback); row.addWidget(button)
        if kind == "robot":
            button = QPushButton("New policy…"); button.clicked.connect(self.create_document); row.addWidget(button)
        layout.addLayout(row)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.status = QLabel("Load a policy file to edit its safeguards."); self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.review_button = QPushButton("Review changes"); self.review_button.clicked.connect(self.review_changes)
        self.save_button = QPushButton("Save reviewed policy"); self.save_button.clicked.connect(self.save_changes)
        row.addWidget(self.review_button); row.addWidget(self.save_button); layout.addLayout(row)
        self.config_path.textChanged.connect(self.invalidate_file)
        self.invalidate_file()
        if path and kind in {"robot", "application"}: self.load_document()

    def invalidate_file(self, *_):
        self.snapshot = self.reviewed = None
        self.tabs.setEnabled(False); self.review_button.setEnabled(False); self.save_button.setEnabled(False)
        self.status.setText("Load this policy before editing or saving. Existing sessions retain their loaded policy.")

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Local policy", self.config_path.text(), "JSON policy (*.json)")
        if path:
            self.config_path.setText(path); self.load_document()

    def create_document(self):
        from fanuc_workbench.ui.policy_dialog import PolicyDialog
        dialog = PolicyDialog(getattr(self.window().parent(), "current_profile", None), self)
        if dialog.exec():
            self.config_path.setText(str(dialog.settings.path)); self.load_document()

    def load_document(self):
        self.invalidate_file()
        try:
            self.snapshot = load_policy(Path(self.config_path.text()), self.kind, self.broker)
            self.draft = self.snapshot.document
            self.errors.clear(); self.raw_inputs.clear(); self.snpx_cache.clear()
            self.build_forms()
            self.tabs.setEnabled(True); self.review_button.setEnabled(True)
            self.status.setText(f"Loaded {self.snapshot.path}. Changes are local until saved and explicitly applied to a new session.")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.snapshot = None
            self.status.setText(f"Could not load policy: {error}")

    def value(self, path, default):
        result = self.draft
        for key in path:
            if isinstance(result, list) and isinstance(key, int) and 0 <= key < len(result):
                result = result[key]; continue
            if not isinstance(result, dict) or key not in result:
                return copy.deepcopy(default)
            result = result[key]
        return result

    def put(self, path, value, remove=False):
        target = self.draft
        for key in path[:-1]: target = target[key] if isinstance(target, list) else target.setdefault(key, {})
        if remove: target.pop(path[-1], None)
        else: target[path[-1]] = value
        if path[-2:] == ("profile", "transport") and "port" not in target:
            port = self.widgets.get(path[:-1] + ("port",))
            if port is not None:
                port.blockSignals(True); port.setValue(22 if value == "sftp" else 21); port.blockSignals(False)
        self.changed()

    def changed(self):
        self.reviewed = None; self.save_button.setEnabled(False)
        self.review_button.setEnabled(self.snapshot is not None and not self.errors)
        self.status.setText(next(iter(self.errors.values())) if self.errors else "Unsaved changes. Review the complete proposal before saving.")

    def text_changed(self, path, widget, kind):
        self.raw_inputs[path] = widget.toPlainText()
        try:
            value = parse_lines(widget.toPlainText(), kind)
            self.errors.pop(path, None); self.put(path, value)
        except ValueError as error:
            self.errors[path] = str(error); self.changed()

    def add_fields(self, form, fields, prefix=()):
        for spec in fields:
            path = prefix + (spec.key,)
            default = spec.default
            if spec.key == "port" and prefix[-1:] == ("profile",):
                default = 22 if self.value(prefix + ("transport",), "ftp") == "sftp" else 21
            value = self.value(path, default)
            if spec.kind == "bool":
                widget = QCheckBox(); widget.setChecked(value)
                widget.toggled.connect(lambda checked, p=path: self.put(p, checked))
            elif spec.kind in {"int", "float"}:
                widget = QDoubleSpinBox(); widget.setDecimals(0 if spec.kind == "int" else 3)
                widget.setRange(spec.minimum, spec.maximum); widget.setValue(float(value))
                widget.setMaximumWidth(240)
                widget.valueChanged.connect(lambda number, p=path, k=spec.kind: self.put(p, int(number) if k == "int" else number))
            elif spec.kind == "choice":
                widget = QComboBox(); widget.addItems(spec.choices); widget.setCurrentText(value)
                widget.currentTextChanged.connect(lambda text, p=path: self.put(p, text))
            elif spec.kind in {"lines", "ranges", "formats", "nullable_lines"}:
                edit = QPlainTextEdit(); edit.setMaximumHeight(105)
                edit.setPlainText(self.raw_inputs.get(path, lines(value, spec.kind)))
                edit.textChanged.connect(lambda p=path, w=edit, k=spec.kind: self.text_changed(p, w, k))
                if spec.kind == "nullable_lines":
                    widget = QWidget(); box = QVBoxLayout(widget); box.setContentsMargins(0,0,0,0)
                    restrict = QCheckBox("Restrict writes to this exact list"); restrict.setChecked(value is not None)
                    edit.setEnabled(value is not None); box.addWidget(restrict); box.addWidget(edit)
                    def toggle(checked, p=path, w=edit):
                        w.setEnabled(checked); self.put(p, parse_lines(w.toPlainText(), "lines") if checked else None)
                    restrict.toggled.connect(toggle)
                    widget.restrict, widget.edit = restrict, edit
                else: widget = edit
            else:
                widget = QLineEdit(str(value or ""))
                if spec.kind == "secret":
                    widget.setEchoMode(QLineEdit.Password); widget.setMaxLength(128)
                widget.textChanged.connect(lambda text, p=path, k=spec.kind: self.put(p, text, k == "optional" and not text))
            widget.setObjectName(".".join(map(str, path))); widget.setToolTip(spec.hint)
            self.widgets[path] = widget
            form.addRow(spec.label, widget)
            if spec.hint:
                hint = QLabel(spec.hint); hint.setWordWrap(True); form.addRow("", hint)

    def page(self, name, fields=None, prefix=()):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); form = QFormLayout(body); scroll.setWidget(body)
        self.tabs.addTab(scroll, name)
        if fields: self.add_fields(form, fields, prefix)
        return form

    def build_forms(self):
        self.widgets = {}
        while self.tabs.count():
            page = self.tabs.widget(0); self.tabs.removeTab(0)
            if page is not self.preview: page.deleteLater()
        if self.kind == "robot":
            self.page("Server safeguards", SERVER_FIELDS)
            form = self.page("Robot safeguards")
            row = QHBoxLayout(); self.robot = QComboBox()
            self.robot.addItems(self.draft.get("robots", {})); row.addWidget(self.robot, 1)
            for label, callback in (("Add…", self.add_robot), ("Rename…", self.rename_robot), ("Remove", self.remove_robot)):
                button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
            form.addRow("Robot", row)
            self.robot_tabs = QTabWidget(); form.addRow(self.robot_tabs)
            self.robot.currentTextChanged.connect(self.build_robot)
            self.build_robot()
        elif self.kind == "application":
            form = self.page("App host access", BROKER_FIELDS, ("backends", 0, "service"))
            note = QLabel("App-owned framework host, independent of the universal broker. Saving stages a new policy revision. Finish and release retained work, then refresh the app host to apply it. Robot online-write permission remains separate.")
            note.setWordWrap(True); form.addRow(note)
        else:
            form = self.page("Broker service" if self.kind == "broker" else "Broker server", BROKER_FIELDS if self.kind == "broker" else BROKER_SERVER_FIELDS)
            note = QLabel("Broker policy is separate from robot policy. Stop the broker and reconcile retained work before saving registered settings; refresh/reconnect after saving. Host server settings affect every registered app.")
            note.setWordWrap(True); form.addRow(note)
            if self.kind == "broker_limits":
                generate = QPushButton("Generate authentication token")
                generate.clicked.connect(lambda: self.widgets[("token",)].setText(secrets.token_urlsafe(48)))
                form.addRow(generate)
        self.tabs.addTab(self.preview, "Change review")

    def build_robot(self, *_):
        for path in list(self.widgets):
            if path[0] == "robots": self.widgets.pop(path)
        while self.robot_tabs.count():
            page = self.robot_tabs.widget(0); self.robot_tabs.removeTab(0); page.deleteLater()
        name = self.robot.currentText()
        if not name: return
        prefix = ("robots", name)
        for title, fields, path in (("Connection", PROFILE_FIELDS, prefix + ("profile",)),
                                   ("Access & targets", WRITE_FIELDS, prefix), ("HTTP reads", HTTP_FIELDS, prefix)):
            scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); form = QFormLayout(body)
            scroll.setWidget(body); self.robot_tabs.addTab(scroll, title); self.add_fields(form, fields, path)
            if title == "Connection":
                self.add_fields(form, CONNECTION_FIELDS, prefix)
                note = QLabel("Passwords are resolved from environment variables. SFTP host-key verification remains required.")
                note.setWordWrap(True); form.addRow(note)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); form = QFormLayout(body)
        scroll.setWidget(body); self.robot_tabs.addTab(scroll, "SNPX reads")
        enabled = QCheckBox("Enable exact SNPX read allowlists")
        enabled.setChecked(self.value(prefix + ("snpx_read",), None) is not None); form.addRow(enabled)
        controls = QWidget(); inner = QFormLayout(controls)
        self.add_fields(inner, SNPX_FIELDS, prefix + ("snpx_read",)); form.addRow(controls)
        controls.setEnabled(enabled.isChecked())
        def toggle(checked):
            controls.setEnabled(checked)
            if not checked:
                self.snpx_cache[name] = self.value(prefix + ("snpx_read",), {})
                for mapping in (self.errors, self.raw_inputs):
                    for path in list(mapping):
                        if path[:3] == prefix + ("snpx_read",): mapping.pop(path)
            self.put(prefix + ("snpx_read",), copy.deepcopy(self.snpx_cache.get(name, {})) if checked else None)
            self.build_robot()
        enabled.toggled.connect(toggle)
        self.snpx_enabled = enabled

    def add_robot(self):
        name, accepted = QInputDialog.getText(self, "Add robot", "Robot ID")
        if not accepted: return
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in self.draft["robots"]:
            self.status.setText("Use a unique robot ID containing letters, numbers, underscores or hyphens."); return
        self.draft["robots"][name] = {"profile": {"name": name, "host": "", "roots": ["md:"]}}
        self.robot.addItem(name); self.robot.setCurrentText(name); self.changed()

    def rename_robot(self):
        old = self.robot.currentText()
        if not old: return
        name, accepted = QInputDialog.getText(self, "Rename robot", "Robot ID", text=old)
        if not accepted or name == old: return
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in self.draft["robots"]:
            self.status.setText("Use a unique valid robot ID."); return
        self.draft["robots"][name] = self.draft["robots"].pop(old)
        if old in self.snpx_cache: self.snpx_cache[name] = self.snpx_cache.pop(old)
        for mapping in (self.errors, self.raw_inputs):
            for path in list(mapping):
                if path[:2] == ("robots", old): mapping[("robots", name) + path[2:]] = mapping.pop(path)
        self.robot.blockSignals(True); self.robot.setItemText(self.robot.currentIndex(), name); self.robot.blockSignals(False)
        self.build_robot(); self.changed()

    def remove_robot(self):
        name = self.robot.currentText()
        if not name: return
        self.draft["robots"].pop(name)
        self.snpx_cache.pop(name, None)
        for mapping in (self.errors, self.raw_inputs):
            for path in list(mapping):
                if path[:2] == ("robots", name): mapping.pop(path)
        self.robot.removeItem(self.robot.currentIndex()); self.changed()

    def review_changes(self):
        self.reviewed = None; self.save_button.setEnabled(False)
        if self.snapshot is None or self.errors: return
        try:
            self.reviewed = review_policy(self.snapshot, self.draft)
            shown = json.loads(self.reviewed.proposed)
            if self.kind == "broker_limits": shown["token"] = "<authentication token hidden>"
            self.preview.setPlainText(f"File: {self.snapshot.path}\nOriginal SHA-256: {self.snapshot.sha256}\nProposed SHA-256: {self.reviewed.sha256}\n\n{self.reviewed.diff}\n\nComplete policy:\n{json.dumps(shown, indent=2)}")
            self.tabs.setCurrentWidget(self.preview); self.save_button.setEnabled(True)
            self.status.setText("Validated. Save changes only this local file and retains an exact original backup; it does not connect to a controller.")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.status.setText(f"Could not review policy: {error}")

    def save_changes(self):
        if self.reviewed is None: return
        try:
            self.snapshot, backup = save_policy(self.reviewed)
            self.reviewed = None; self.draft = self.snapshot.document
            self.save_button.setEnabled(False); self.raw_inputs.clear()
            self.status.setText(f"Saved {self.snapshot.path}. Original: {backup or 'unchanged'}. Reconnect MCP servers / reload the native policy deliberately; host policy also needs refresh/reconnect.")
            self.saved.emit(str(self.snapshot.path))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.reviewed = None; self.save_button.setEnabled(False)
            self.status.setText(f"Could not save policy: {error}")


class SafeguardsDialog(QDialog):
    def __init__(self, policy_path="", broker_runtime_path="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Safeguards and policies"); self.resize(1050, 850)
        self.saved_policy_path = ""
        self.loaded_broker_runtime_path = ""
        layout = QVBoxLayout(self)
        note = QLabel("Edit and review local settings. Saving does not modify any controller or elevate an already loaded MCP/native session.")
        note.setWordWrap(True); layout.addWidget(note)
        tabs = QTabWidget(); layout.addWidget(tabs, 1)
        self.robot_policy = PolicyPane("robot", policy_path); tabs.addTab(self.robot_policy, "Robot policy")
        self.robot_policy.saved.connect(self.remember_policy)
        self.application_policy = PolicyPane("application"); tabs.addTab(self.application_policy, "App framework")
        broker_page = QWidget(); box = QVBoxLayout(broker_page)
        row = QHBoxLayout(); self.broker_runtime_path = QLineEdit(broker_runtime_path)
        row.addWidget(QLabel("Host / broker runtime.json")); row.addWidget(self.broker_runtime_path, 1)
        browse = QPushButton("Browse…"); browse.clicked.connect(self.browse_runtime); row.addWidget(browse)
        load = QPushButton("Load runtime"); load.clicked.connect(self.load_broker); row.addWidget(load); box.addLayout(row)
        broker_tabs = QTabWidget(); box.addWidget(broker_tabs)
        self.broker_policy = PolicyPane("broker"); broker_tabs.addTab(self.broker_policy, "FANUC service policy")
        self.broker_limits = PolicyPane("broker_limits"); broker_tabs.addTab(self.broker_limits, "Host server settings")
        self.broker_status = QLabel("Select an app-host or broker runtime to validate its policy contract. No server or controller is started.")
        self.broker_status.setWordWrap(True); box.addWidget(self.broker_status)
        self.broker_runtime_path.textChanged.connect(self.invalidate_broker)
        tabs.addTab(broker_page, "Host / broker settings")
        guards = QPlainTextEdit(BUILT_IN_GUARDS); guards.setReadOnly(True); tabs.addTab(guards, "Built-in checks")
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def remember_policy(self, path):
        self.saved_policy_path = path

    def invalidate_broker(self):
        self.loaded_broker_runtime_path = ""
        for pane in (self.broker_policy, self.broker_limits):
            pane.broker = None; pane.invalidate_file()

    def browse_runtime(self):
        path, _ = QFileDialog.getOpenFileName(self, "Universal Tools runtime", "", "JSON runtime (*.json)")
        if path: self.broker_runtime_path.setText(path); self.load_broker()

    def load_broker(self):
        self.invalidate_broker()
        try:
            runtime = BrokerRuntime.load(Path(self.broker_runtime_path.text()))
            self.loaded_broker_runtime_path = str(Path(self.broker_runtime_path.text()).resolve())
            for pane, path in ((self.broker_policy, runtime.state / "services/fanuc-mcp-server.json"),
                               (self.broker_limits, runtime.state / "settings.json")):
                pane.broker = runtime; pane.config_path.setText(str(path))
                if path.exists(): pane.load_document()
            self.broker_status.setText("Runtime loaded. Global settings require a stopped host and reconciled work. Use App framework for app publications, or FANUC service policy for broker registrations.")
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.broker_status.setText(f"Could not load runtime: {error}")

    def reject(self):
        panes = (self.robot_policy, self.application_policy, self.broker_policy, self.broker_limits)
        if any(pane.snapshot and (pane.errors or pane.draft != pane.snapshot.document) for pane in panes):
            answer = QMessageBox.question(self, "Unsaved policy edits", "Discard unsaved policy edits?",
                                          QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard: return
        super().reject()
