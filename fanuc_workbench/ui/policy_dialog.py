"""Create a reviewed local robot policy without a controller connection."""
import json
from pathlib import Path
import re

from PySide6.QtWidgets import (QCheckBox,QComboBox,QDialog,QDialogButtonBox,QFileDialog,QFormLayout,
    QHBoxLayout,QLabel,QLineEdit,QPlainTextEdit,QPushButton,QScrollArea,QSpinBox,QSplitter,QVBoxLayout,QWidget)
from PySide6.QtCore import Qt

from fanuc_workbench.bridge.policy_authoring import create_policy, policy_document
from fanuc_workbench.core.models import RobotProfile


class PolicyDialog(QDialog):
    def __init__(self, profile=None, parent=None):
        super().__init__(parent)
        profile = profile or RobotProfile('Robot','')
        self.setWindowTitle('Create robot policy');self.resize(900,820);self.settings=None;self.reviewed=None
        layout=QVBoxLayout(self)
        note=QLabel('Create a local MCP/Native Controller policy. Review the exact connection and allowed targets. No controller connection is made. Existing policies are never overwritten.');note.setWordWrap(True);layout.addWidget(note)
        splitter=QSplitter(Qt.Horizontal);layout.addWidget(splitter,1)
        form_widget=QWidget();form=QFormLayout(form_widget);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(form_widget);splitter.addWidget(scroll)
        self.robot_id=QLineEdit(re.sub(r'[^a-zA-Z0-9_-]','_',profile.name)[:64] or 'robot')
        self.name=QLineEdit(profile.name);self.host=QLineEdit(profile.host);self.username=QLineEdit(profile.username)
        self.transport=QComboBox();self.transport.addItems(['ftp','sftp']);self.transport.setCurrentText(profile.transport)
        self.port=QSpinBox();self.port.setRange(1,65535);self.port.setValue(profile.port)
        self.transport.currentTextChanged.connect(lambda value:self.port.setValue(22 if value=='sftp' else 21))
        self.roots=QLineEdit(', '.join(profile.roots));self.password_env=QLineEdit();self.password_env.setPlaceholderText('Optional environment-variable name')
        self.read_only=QCheckBox('Read-only controller access');self.read_only.setChecked(True)
        for label,widget in [('Robot ID',self.robot_id),('Name',self.name),('Host',self.host),('Transport',self.transport),('Port',self.port),('Username',self.username),('Read roots (comma separated)',self.roots),('Password environment',self.password_env),('Access',self.read_only)]:form.addRow(label,widget)
        self.native=QComboBox();self.native.addItems(['disabled','frrobot']);form.addRow('Native API provider',self.native)
        self.fields={}
        for key,label in [('writable_files','Replace files — exact remote paths'),('creatable_files','Create storage TXT/CSV — outside MD:'),('native_register_targets','Native registers — exact R/PR targets'),('native_programs','Native program names'),('comment_targets','Description targets')]:
            edit=QPlainTextEdit();edit.setMaximumHeight(65);edit.setPlaceholderText('One exact target per line; leave empty to deny');self.fields[key]=edit;form.addRow(label,edit)
        self.comment_url=QLineEdit();self.comment_url.setPlaceholderText('Only needed for description changes');form.addRow('Description HTTP(S) origin',self.comment_url)
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setPlaceholderText('Click Review policy to validate and inspect the complete JSON.');splitter.addWidget(self.preview);splitter.setSizes([450,450])
        self.status=QLabel('Read-only is the default. SFTP requires verified host keys; native access requires compatible licensed software.');self.status.setWordWrap(True);layout.addWidget(self.status)
        row=QHBoxLayout();review=QPushButton('Review policy');review.clicked.connect(self.review_policy);row.addWidget(review)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);self.buttons.button(QDialogButtonBox.Save).setText('Create policy…');self.buttons.button(QDialogButtonBox.Save).setEnabled(False);self.buttons.accepted.connect(self.save_policy);self.buttons.rejected.connect(self.reject);row.addWidget(self.buttons);layout.addLayout(row)
        for widget in [self.robot_id,self.name,self.host,self.username,self.roots,self.password_env,self.comment_url]:widget.textChanged.connect(self.invalidate)
        for widget in self.fields.values():widget.textChanged.connect(self.invalidate)
        self.read_only.toggled.connect(self.invalidate);self.native.currentIndexChanged.connect(self.invalidate);self.transport.currentIndexChanged.connect(self.invalidate);self.port.valueChanged.connect(self.invalidate)

    def invalidate(self,*_):
        self.reviewed=None;self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.status.setText('Inputs changed. Review the updated policy before creating it.')

    def review_policy(self):
        try:
            profile=RobotProfile(self.name.text().strip() or 'Robot',self.host.text().strip(),port=self.port.value(),username=self.username.text().strip() or 'anonymous',roots=[root.strip() for root in self.roots.text().split(',') if root.strip()],transport=self.transport.currentText())
            if not profile.roots:raise ValueError('Provide at least one explicit read root')
            self.reviewed=policy_document(profile,robot_id=self.robot_id.text().strip(),read_only=self.read_only.isChecked(),password_env=self.password_env.text().strip(),native_provider=self.native.currentText(),comment_service_url=self.comment_url.text().strip(),**{key:[line.strip() for line in edit.toPlainText().splitlines() if line.strip()] for key,edit in self.fields.items()})
            self.preview.setPlainText(json.dumps(self.reviewed,indent=2));self.buttons.button(QDialogButtonBox.Save).setEnabled(True)
            self.status.setText('Reviewed policy is ready to create. Loaded MCP/native sessions keep their current policy until explicitly reloaded/reconnected.')
        except (ValueError,TypeError,KeyError,OSError) as error:
            self.reviewed=None;self.buttons.button(QDialogButtonBox.Save).setEnabled(False);self.status.setText(str(error))

    def save_policy(self):
        if self.reviewed is None:return
        default=Path.home()/'robot_bridge.local.json'
        path,_=QFileDialog.getSaveFileName(self,'Create robot policy',str(default),'JSON policy (*.json)')
        if not path:return
        try:
            self.settings=create_policy(Path(path),self.reviewed)
        except (OSError,ValueError,TypeError,KeyError) as error:
            self.status.setText('Could not create policy: '+str(error));return
        self.accept()
