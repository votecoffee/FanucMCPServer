from __future__ import annotations
from PySide6.QtWidgets import QDialog,QDialogButtonBox,QFormLayout,QLineEdit,QSpinBox,QVBoxLayout,QComboBox,QCheckBox,QLabel
from fanuc_workbench.core.models import RobotProfile

class ConnectionDialog(QDialog):
    def __init__(self,profile:RobotProfile|None=None,parent=None):
        super().__init__(parent);self.setWindowTitle('Robot connection');profile=profile or RobotProfile(name='Robot',host='')
        self.name=QLineEdit(profile.name);self.host=QLineEdit(profile.host)
        self.transport=QComboBox();self.transport.addItems(['ftp','sftp']);self.transport.setCurrentText(profile.transport)
        self.port=QSpinBox();self.port.setRange(1,65535);self.port.setValue(profile.port or (22 if profile.transport=='sftp' else 21))
        self.username=QLineEdit(profile.username);self.password=QLineEdit(profile.password);self.password.setEchoMode(QLineEdit.Password)
        self.roots=QLineEdit(', '.join(profile.roots));self.roots.setToolTip('Comma-separated FANUC devices/directories, e.g. md:, mc:')
        self.trust=QCheckBox('Trust unknown SFTP host key on first connection (TOFU)');self.trust.setChecked(profile.sftp_trust_unknown_host)
        self.transport.currentTextChanged.connect(lambda t:self.port.setValue(22 if t=='sftp' else 21))
        f=QFormLayout();f.addRow('Name',self.name);f.addRow('Host / IP',self.host);f.addRow('Transport',self.transport);f.addRow('Port',self.port);f.addRow('Username',self.username);f.addRow('Password',self.password);f.addRow('Backup roots',self.roots);f.addRow('',self.trust)
        note=QLabel('SFTP uses SSH encryption. Unknown host keys are rejected unless TOFU is enabled. Passwords are not persisted by default.');note.setWordWrap(True)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        l=QVBoxLayout(self);l.addLayout(f);l.addWidget(note);l.addWidget(buttons)
    def profile(self):
        roots=[x.strip() for x in self.roots.text().split(',') if x.strip()]
        return RobotProfile(name=self.name.text().strip() or 'Robot',host=self.host.text().strip(),port=self.port.value(),username=self.username.text().strip() or 'anonymous',password=self.password.text(),roots=roots or ['md:'],transport=self.transport.currentText(),sftp_trust_unknown_host=self.trust.isChecked())
