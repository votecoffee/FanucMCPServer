"""PLC export browsing and reviewed local mapping; no controller interface."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView,QCheckBox,QDialog,QDialogButtonBox,QFileDialog,
    QHBoxLayout,QLabel,QLineEdit,QMessageBox,QPlainTextEdit,QPushButton,QTableWidget,
    QTableWidgetItem,QVBoxLayout,QWidget)

from fanuc_workbench.core.workers import Worker
from fanuc_workbench.services.plc_tags import load_plc_tags,suggest_mappings


class PlcCatalog(QWidget):
    def __init__(self, io_tab):
        super().__init__();self.io_tab=io_tab;self.catalog=None;self.matches=[]
        layout=QVBoxLayout(self);row=QHBoxLayout()
        self.import_button=QPushButton('Import PLC tags (CSV / L5X)…');self.import_button.clicked.connect(self.choose_import);row.addWidget(self.import_button)
        assign=QPushButton('Assign selected tag to selected I/O');assign.clicked.connect(self.assign);row.addWidget(assign)
        review=QPushButton('Review suggested mappings…');review.clicked.connect(self.review);row.addWidget(review);layout.addLayout(row)
        self.filter=QLineEdit();self.filter.setPlaceholderText('Search all metadata, or tag:Robot1. / scope:Cell — also scopes mapping suggestions');self.filter.textChanged.connect(self.render);layout.addWidget(self.filter)
        self.status=QLabel('Import a PLC tag CSV or L5X export. Assignments change the local grid; Save PLC tag map writes it to the workspace.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.table=QTableWidget(0,6);self.table.setObjectName('plc_tag_catalog');self.table.setHorizontalHeaderLabels(['PLC Tag','Scope','Data type / dimensions','Alias target','Description','I/O references'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setColumnWidth(0,280);self.table.setColumnWidth(4,320);layout.addWidget(self.table)

    def choose_import(self):
        path,_=QFileDialog.getOpenFileName(self,'Import PLC tag catalog','','PLC exports (*.csv *.l5x);;CSV (*.csv);;Logix L5X (*.l5x)')
        if not path:return
        self.import_button.setEnabled(False)
        worker=Worker(lambda *,progress:load_plc_tags(Path(path)));self._worker=worker
        worker.signals.result.connect(self.loaded);worker.signals.error.connect(self.failed);worker.signals.finished.connect(lambda:self.import_button.setEnabled(True));self.io_tab.owner.thread_pool.start(worker)

    def failed(self,error):
        QMessageBox.critical(self,'PLC import failed',error)

    def loaded(self,catalog):
        self.catalog=catalog;self.render()

    def render(self,*_):
        self.table.setRowCount(0)
        if self.catalog is None:return
        query=self.filter.text().casefold().strip()
        def matches(entry):
            if query.startswith('tag:'):return entry['qualified_tag'].casefold().startswith(query[4:].strip())
            if query.startswith('scope:'):return query[6:].strip() in entry['scope'].casefold()
            return not query or query in ' '.join(str(entry[key]) for key in ('qualified_tag','scope','description','alias_for')).casefold()
        self.matches=[entry for entry in self.catalog['entries'] if matches(entry)]
        for entry in self.matches[:500]:
            row=self.table.rowCount();self.table.insertRow(row)
            values=[entry['tag'],entry['scope'] or 'Controller',entry['data_type']+(' ['+entry['dimensions']+']' if entry['dimensions'] else ''),entry['alias_for'],entry['description'],', '.join(entry['io_hints'])]
            for column,value in enumerate(values):
                item=QTableWidgetItem(value);item.setFlags(item.flags() & ~Qt.ItemIsEditable);item.setToolTip(value);self.table.setItem(row,column,item)
            self.table.item(row,0).setData(Qt.UserRole,entry['qualified_tag'])
        warnings=' | '.join(self.catalog['warnings'])
        self.status.setText(f"{self.catalog['source_name']} — showing {min(500,len(self.matches))}/{len(self.matches)} matching tags ({len(self.catalog['entries'])} total). {warnings}")
        self.status.setToolTip('Source SHA-256: '+self.catalog['source_sha256'])

    def assign(self):
        selected=self.table.currentRow();target=self.io_tab.table.currentRow()
        if selected<0 or target<0:
            self.status.setText('Select an I/O row in the upper table and a PLC tag in this catalog first.');return
        tag=self.table.item(selected,0).data(Qt.UserRole)
        self.io_tab.table.item(target,6).setText(tag)
        self.status.setText('Assigned '+tag+' in the mapping grid. Click Save PLC tag map to persist it locally.')

    def review(self):
        if self.catalog is None:return
        table=self.io_tab.table
        current={table.item(row,0).text():table.item(row,6).text() for row in range(table.rowCount())}
        proposed=suggest_mappings(self.matches,[table.item(row,0).text() for row in range(table.rowCount())],current)
        dialog=QDialog(self);dialog.setWindowTitle('Review PLC mapping suggestions');dialog.resize(850,600)
        layout=QVBoxLayout(dialog);note=QLabel('Suggestions use explicit I/O tokens in the filtered catalog metadata. Review every assignment. They do not verify PLC/controller bit routing. Apply stages cells; Save PLC tag map is separate.');note.setWordWrap(True);layout.addWidget(note)
        replace=QCheckBox('Include replacements of existing mappings shown below');layout.addWidget(replace)
        preview=QPlainTextEdit();preview.setReadOnly(True);layout.addWidget(preview,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Apply|QDialogButtonBox.Cancel);buttons.button(QDialogButtonBox.Apply).setText('Apply to mapping grid');buttons.button(QDialogButtonBox.Apply).clicked.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        def render():
            rows=[]
            for entry in proposed:
                apply=entry['status']=='ready' or (replace.isChecked() and entry['status']=='replace')
                rows.append(f"{'APPLY' if apply else 'SKIP'} | {entry['signal']} | {entry['previous'] or '(empty)'} -> {entry['tag'] or ', '.join(entry['candidates'])} | {entry['status']}")
            preview.setPlainText('\n'.join(rows) or 'No explicit I/O references found in the filtered catalog.')
            buttons.button(QDialogButtonBox.Apply).setEnabled(any(entry['status']=='ready' or (replace.isChecked() and entry['status']=='replace') for entry in proposed))
        replace.toggled.connect(render);render()
        if dialog.exec()!=QDialog.Accepted:return
        fresh={table.item(row,0).text():table.item(row,6).text() for row in range(table.rowCount())}
        if fresh!=current:
            self.status.setText('Mapping grid changed during review. Review the suggestions again.');return
        count=0
        for entry in proposed:
            if entry['status']=='ready' or (replace.isChecked() and entry['status']=='replace'):
                for row in range(table.rowCount()):
                    if table.item(row,0).text()==entry['row_signal']:table.item(row,6).setText(entry['tag']);count+=1
        self.status.setText(f'Staged {count} mappings in the grid. Click Save PLC tag map to persist them locally.')
