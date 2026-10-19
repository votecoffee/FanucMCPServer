"""Stage local connection changes and explicitly save the chosen profile."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QDialogButtonBox,QHBoxLayout,QLabel,QListWidget,
    QMessageBox,QPushButton,QVBoxLayout)

from fanuc_workbench.services.profiles import ProfileCatalog
from fanuc_workbench.ui.connection_dialog import ConnectionDialog


class ProfilesDialog(QDialog):
    def __init__(self, profiles, current_profile, commit, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Manage robot connections');self.resize(720,450)
        self.catalog=ProfileCatalog(profiles);self.commit=commit
        layout=QVBoxLayout(self)
        note=QLabel('Add, edit, duplicate or remove saved connections. Select the active robot, then Save and select. Changes stay staged until saved; saving does not connect to a controller. Passwords remain in this app session only.')
        note.setWordWrap(True);layout.addWidget(note)
        self.list=QListWidget();self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded);layout.addWidget(self.list,1)
        self.list.itemDoubleClicked.connect(lambda *_:self.edit())
        row=QHBoxLayout();self.edit_buttons=[]
        for text,callback in [('New…',self.add),('Edit…',self.edit),('Duplicate',self.duplicate),('Remove',self.remove)]:
            button=QPushButton(text);button.setAutoDefault(False);button.clicked.connect(callback);row.addWidget(button)
            if text!='New…':self.edit_buttons.append(button)
        row.addStretch();layout.addLayout(row)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('Save and select')
        self.buttons.button(QDialogButtonBox.Save).setDefault(True)
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.list.currentRowChanged.connect(self.selection_changed)
        selected=next((i for i,p in enumerate(profiles) if p==current_profile),0)
        self.render(selected)

    def selection_changed(self,*_):
        for button in self.edit_buttons:button.setEnabled(self.list.currentRow()>=0)

    def render(self,selected=0):
        self.list.clear()
        for profile in self.catalog.profiles:
            self.list.addItem(f'{profile.name} — {profile.host}:{profile.port} — {profile.transport.upper()}')
        if self.list.count():self.list.setCurrentRow(min(max(0,selected),self.list.count()-1))
        self.selection_changed()

    def _edit(self,index=None):
        profiles=self.catalog.profiles
        dialog=ConnectionDialog(profiles[index] if index is not None else None,self,
            existing_names=[p.name for i,p in enumerate(profiles) if i!=index])
        if dialog.exec()!=QDialog.Accepted:return
        try:
            if index is None:index=self.catalog.add(dialog.profile())
            else:self.catalog.update(index,dialog.profile())
        except ValueError as exc:
            QMessageBox.warning(self,'Connection details',str(exc));return
        self.render(index)

    def add(self):self._edit()
    def edit(self):
        if self.list.currentRow()>=0:self._edit(self.list.currentRow())
    def duplicate(self):
        if self.list.currentRow()<0:return
        try:self.render(self.catalog.duplicate(self.list.currentRow()))
        except ValueError as exc:QMessageBox.warning(self,'Connection details',str(exc))
    def remove(self):
        index=self.list.currentRow()
        if index>=0:self.catalog.remove(index);self.render(index)
    def accept(self):
        try:self.catalog.validated_profiles()
        except ValueError as exc:
            QMessageBox.warning(self,'Connection details',str(exc));return
        index=self.list.currentRow()
        if self.list.count() and index<0:
            QMessageBox.warning(self,'Connection details','Select the connection to make active.');return
        if self.commit(self.catalog,index if index>=0 else None):super().accept()
