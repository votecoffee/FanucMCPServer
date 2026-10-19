from __future__ import annotations

import re
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
)

from fanuc_workbench.services.controller import ControllerService
from fanuc_workbench.core.workers import Worker
from fanuc_workbench.parsers.comments import set_comment
from fanuc_workbench.parsers.data import (
    parse_frames, parse_io, parse_numeric_registers, parse_position_registers,
    set_numeric_register_value, set_position_register,
)
from fanuc_workbench.parsers.ls import read_text, write_text
from fanuc_workbench.services.compare import compare_workspaces, text_diff
from fanuc_workbench.services.diagnostics import dcs_compare, dcs_evidence, health_summary, mastering_evidence, scan_alarms
from fanuc_workbench.services.documentation import export_all
from fanuc_workbench.services.io_map import export_io_csv, load_plc_map, save_plc_map
from fanuc_workbench.services.live_comments import set_live_comment, target_for_record
from fanuc_workbench.services.project import ProjectRobot, RobotProject
from fanuc_workbench.services.restore import restore_candidates
from fanuc_workbench.services.workspace import scan_comments, scan_programs
from fanuc_workbench.services.xref import build_xref, program_dependencies, program_flow, register_usage, unused_programs, validate_ls, CALL_RE


class PasteTableWidget(QTableWidget):
    """Spreadsheet-like QTableWidget with tab/newline clipboard paste."""
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            text = QApplication.clipboard().text()
            rows = [r.split('\t') for r in text.rstrip('\r\n').splitlines()]
            sr = max(0, self.currentRow()); sc = max(0, self.currentColumn())
            for r, values in enumerate(rows):
                for c, value in enumerate(values):
                    rr, cc = sr+r, sc+c
                    if rr < self.rowCount() and cc < self.columnCount():
                        item = self.item(rr,cc) or QTableWidgetItem()
                        if item.flags() & Qt.ItemIsEditable:
                            item.setText(value); self.setItem(rr,cc,item)
            return
        super().keyPressEvent(event)


class LsHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.rules=[]
        def fmt(color,bold=False):
            f=QTextCharFormat(); f.setForeground(QColor(color)); f.setFontWeight(QFont.Bold if bold else QFont.Normal); return f
        self.rules=[
            (re.compile(r'^/(?:PROG|ATTR|MN|POS|END)\b',re.I),fmt('#005A9C',True)),
            (re.compile(r'\b(?:CALL|RUN|IF|THEN|ELSE|SELECT|JMP|LBL|WAIT|FOR|ENDFOR|UALM|ABORT)\b',re.I),fmt('#7A3E9D',True)),
            (re.compile(r'\b(?:R|PR|SR|AR|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|F)\s*\[\s*\d+(?:\s*,\s*\d+)?\s*\]',re.I),fmt('#A15C00')),
            (re.compile(r'\b(?:UFRAME_NUM|UTOOL_NUM|UFRAME|UTOOL)\b',re.I),fmt('#006B54',True)),
            (re.compile(r'!.*$'),fmt('#6A737D')),
            (re.compile(r'"[^"\r\n]*"'),fmt('#9A2B2B')),
        ]
    def highlightBlock(self,text):
        for pat,f in self.rules:
            for m in pat.finditer(text): self.setFormat(m.start(),m.end()-m.start(),f)


def _browse_dir(edit: QLineEdit, title: str):
    p=QFileDialog.getExistingDirectory(edit,title,edit.text() or str(Path.home()))
    if p: edit.setText(p)


def _item(value, editable=False):
    x=QTableWidgetItem(str(value))
    if not editable: x.setFlags(x.flags() & ~Qt.ItemIsEditable)
    return x


class CompareRestoreTab(QWidget):
    def __init__(self, owner):
        super().__init__(); self.owner=owner; self.changes=[]; self.candidates=[]
        layout=QVBoxLayout(self); sub=QTabWidget(); layout.addWidget(sub)
        sub.addTab(self._compare_ui(),'Compare / History'); sub.addTab(self._restore_ui(),'Selective Restore')

    def _compare_ui(self):
        w=QWidget(); l=QVBoxLayout(w); form=QFormLayout()
        self.old=QLineEdit(); self.new=QLineEdit()
        for label,edit in [('Baseline backup',self.old),('Newer backup',self.new)]:
            row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(edit); b=QPushButton('Browse…'); b.clicked.connect(lambda _=False,e=edit:_browse_dir(e,'Choose backup')); h.addWidget(b); form.addRow(label,row)
        l.addLayout(form); b=QPushButton('Compare Backups'); b.clicked.connect(self.run_compare); l.addWidget(b)
        split=QSplitter(Qt.Vertical); self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(['Status','File','Old bytes','New bytes','Semantic']); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.currentCellChanged.connect(self.show_detail); split.addWidget(self.table)
        self.detail=QPlainTextEdit(); self.detail.setReadOnly(True); split.addWidget(self.detail); l.addWidget(split,1); return w

    def _restore_ui(self):
        w=QWidget(); l=QVBoxLayout(w); note=QLabel('Preview and selectively restore controller files. ASCII/diagnostic listings are never selected automatically. System data is advanced and opt-in.'); note.setWordWrap(True); l.addWidget(note)
        row=QHBoxLayout(); b=QPushButton('Load active workspace'); b.clicked.connect(self.load_restore); row.addWidget(b); self.cat=QComboBox(); self.cat.addItem('All'); self.cat.currentTextChanged.connect(self.filter_restore); row.addWidget(QLabel('Category')); row.addWidget(self.cat); row.addStretch(); l.addLayout(row)
        self.restore_table=QTableWidget(0,5); self.restore_table.setHorizontalHeaderLabels(['Restore','Category','Local file','Remote destination','Warning']); self.restore_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.restore_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch); self.restore_table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch); l.addWidget(self.restore_table,1)
        row2=QHBoxLayout(); preview=QPushButton('Preview checked files vs live robot'); preview.clicked.connect(self.preview_live_restore); row2.addWidget(preview); go=QPushButton('Restore checked files to robot…'); go.clicked.connect(self.restore); row2.addWidget(go); row2.addStretch(); l.addLayout(row2); self.restore_preview=QPlainTextEdit(); self.restore_preview.setReadOnly(True); self.restore_preview.setMaximumHeight(170); l.addWidget(self.restore_preview); return w

    def sync_workspace(self):
        ws=self.owner.workspace
        if ws:
            if not self.new.text(): self.new.setText(str(ws))
            self.load_restore()

    def run_compare(self):
        a,b=Path(self.old.text()),Path(self.new.text())
        if not a.is_dir() or not b.is_dir(): QMessageBox.warning(self,'Missing backups','Choose two backup folders.'); return
        self.changes=compare_workspaces(a,b); self.table.setRowCount(0)
        for c in self.changes:
            r=self.table.rowCount(); self.table.insertRow(r)
            for col,v in enumerate([c.status,c.relative_path,c.old_size,c.new_size,'Yes' if c.semantic else 'No']): self.table.setItem(r,col,_item(v))
        self.detail.setPlainText(f'{len(self.changes)} changed files. Select a modified file to see a semantic/text diff.')

    def show_detail(self,row,*_):
        if row<0 or row>=len(self.changes): return
        c=self.changes[row]
        if c.detail: self.detail.setPlainText(c.detail)
        else: self.detail.setPlainText('No textual content difference to display.' if c.semantic else c.status)

    def load_restore(self):
        self.restore_table.setRowCount(0); self.candidates=[]
        if not self.owner.workspace: return
        self.candidates=restore_candidates(self.owner.workspace, (self.owner.current_profile.roots[0] if self.owner.current_profile else 'md:'))
        kinds=sorted({r.category for r in self.candidates}); cur=self.cat.currentText(); self.cat.blockSignals(True); self.cat.clear(); self.cat.addItem('All'); self.cat.addItems(kinds); self.cat.setCurrentText(cur if cur in kinds else 'All'); self.cat.blockSignals(False); self.filter_restore()

    def filter_restore(self):
        self.restore_table.setRowCount(0); wanted=self.cat.currentText()
        for i,c in enumerate(self.candidates):
            if wanted not in ('','All') and c.category!=wanted: continue
            r=self.restore_table.rowCount(); self.restore_table.insertRow(r)
            chk=QTableWidgetItem(); chk.setFlags(Qt.ItemIsUserCheckable|Qt.ItemIsEnabled); chk.setCheckState(Qt.Checked if c.default_selected else Qt.Unchecked); chk.setData(Qt.UserRole,i); self.restore_table.setItem(r,0,chk)
            for col,v in enumerate([c.category,c.relative_path,c.remote_path,c.warning],1): self.restore_table.setItem(r,col,_item(v))

    def _checked_restore_rows(self):
        selected=[]
        for r in range(self.restore_table.rowCount()):
            x=self.restore_table.item(r,0)
            if x and x.checkState()==Qt.Checked: selected.append(self.candidates[int(x.data(Qt.UserRole))])
        return selected
    def preview_live_restore(self):
        if not self.owner.current_profile: QMessageBox.warning(self,'No robot','Configure a robot connection first.'); return
        selected=self._checked_restore_rows()
        if not selected: QMessageBox.information(self,'Nothing selected','Check one or more files first.'); return
        prof=self.owner.current_profile
        def work(*,progress):
            return ControllerService(prof).preview_restore(selected)
        wk=Worker(work); wk.signals.result.connect(self.restore_preview.setPlainText); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Preview failed',e)); self.owner.thread_pool.start(wk)
    def restore(self):
        if not self.owner.current_profile: QMessageBox.warning(self,'No robot','Configure a robot connection first.'); return
        selected=self._checked_restore_rows()
        if not selected: QMessageBox.information(self,'Nothing selected','Check one or more files to restore.'); return
        if any(x.category=='System Data' for x in selected):
            msg='SYSTEM DATA IS SELECTED. This can materially change controller configuration.\n\n'
        else: msg=''
        msg+=f'This will upload {len(selected)} file(s) to {self.owner.current_profile.host}. Verify the robot is in a safe state and that the backup is correct. Continue?'
        if QMessageBox.warning(self,'Selective restore',msg,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        prof=self.owner.current_profile
        def work(*,progress): return ControllerService(prof).restore(selected,progress)
        wk=Worker(work); wk.signals.result.connect(lambda x:QMessageBox.information(self,'Restore complete',f'Uploaded {len(x)} files.')); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Restore failed',e)); self.owner.thread_pool.start(wk)


class ProgramEditorTab(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.path=None; self.highlighter=None
        l=QVBoxLayout(self); top=QHBoxLayout(); self.program=QComboBox(); self.program.currentIndexChanged.connect(self.load_program); top.addWidget(QLabel('Program')); top.addWidget(self.program,1); self.point=QComboBox(); top.addWidget(QLabel('Position')); top.addWidget(self.point); go=QPushButton('Go'); go.clicked.connect(self.goto_point); top.addWidget(go); l.addLayout(top)
        find=QHBoxLayout(); self.find=QLineEdit(); self.replace=QLineEdit(); find.addWidget(QLabel('Find')); find.addWidget(self.find); find.addWidget(QLabel('Replace')); find.addWidget(self.replace); b=QPushButton('Find next'); b.clicked.connect(self.find_next); find.addWidget(b); b=QPushButton('Replace all'); b.clicked.connect(self.replace_all); find.addWidget(b); l.addLayout(find)
        self.editor=QPlainTextEdit(); self.editor.setFont(QFont('Consolas',10)); self.highlighter=LsHighlighter(self.editor.document()); l.addWidget(self.editor,1)
        buttons=QHBoxLayout()
        for text,fn in [('Save locally',self.save),('Validate',self.validate),('Follow CALL on current line',self.follow_call),('Compare to live LS',self.compare_live),('Upload LS to robot…',self.upload)]:
            b=QPushButton(text); b.clicked.connect(fn); buttons.addWidget(b)
        l.addLayout(buttons); self.results=QPlainTextEdit(); self.results.setReadOnly(True); self.results.setMaximumHeight(150); l.addWidget(self.results)

    def sync_workspace(self):
        cur=self.program.currentText(); self.program.blockSignals(True); self.program.clear()
        if self.owner.workspace:
            for p in scan_programs(self.owner.workspace): self.program.addItem(p.program_name,p.path)
        self.program.blockSignals(False); i=self.program.findText(cur); self.program.setCurrentIndex(i if i>=0 else (0 if self.program.count() else -1)); self.load_program()

    def load_program(self,*_):
        p=self.program.currentData(); self.path=Path(p) if p else None
        if not self.path: self.editor.clear(); return
        self.editor.setPlainText(read_text(self.path)); self.point.clear()
        for m in re.finditer(r'^P\[(\d+)(?::"([^"]*)")?\]',self.editor.toPlainText(),re.M|re.I): self.point.addItem(f"P[{m.group(1)}] {m.group(2) or ''}".strip(),int(m.group(1)))

    def save(self):
        if not self.path:return
        bak=self.path.with_suffix(self.path.suffix+'.before_program_edit')
        if not bak.exists(): shutil.copy2(self.path,bak)
        write_text(self.path,self.editor.toPlainText()); self.results.setPlainText(f'Saved {self.path}')

    def find_next(self):
        if self.find.text() and not self.editor.find(self.find.text()): self.editor.moveCursor(QTextCursor.MoveOperation.Start); self.editor.find(self.find.text())

    def replace_all(self):
        q=self.find.text()
        if not q:return
        old=self.editor.toPlainText(); new=old.replace(q,self.replace.text()); self.editor.setPlainText(new); self.results.setPlainText(f'Replaced {old.count(q)} occurrence(s). Save locally when ready.')

    def goto_point(self):
        idx=self.point.currentData()
        if idx is None:return
        pat=re.compile(rf'^P\[{idx}(?::"[^"]*")?\]',re.M|re.I); m=pat.search(self.editor.toPlainText())
        if m:
            cur=self.editor.textCursor(); cur.setPosition(m.start()); self.editor.setTextCursor(cur); self.editor.centerCursor()

    def validate(self):
        if not self.path:return
        self.save(); issues=validate_ls(self.path,self.owner.workspace); self.results.setPlainText('Validation OK.' if not issues else '\n'.join('• '+x for x in issues))

    def follow_call(self):
        cur=self.editor.textCursor(); cur.select(QTextCursor.SelectionType.LineUnderCursor); m=CALL_RE.search(cur.selectedText())
        if not m: self.results.setPlainText('No CALL instruction on the current line.'); return
        i=self.program.findText(m.group(1),Qt.MatchFixedString)
        if i<0: self.results.setPlainText(f'CALL target {m.group(1)} not found in workspace.'); return
        self.program.setCurrentIndex(i)

    def compare_live(self):
        if not self.path or not self.owner.current_profile: QMessageBox.warning(self,'Unavailable','Select a program and configure a robot.'); return
        prof=self.owner.current_profile; local=self.path; remote=(prof.roots[0] if prof.roots else 'md:')+local.name
        def work(*,progress):
            return ControllerService(prof).compare_file(local,remote)
        wk=Worker(work); wk.signals.result.connect(lambda d:self.results.setPlainText(d or 'No semantic/text difference.')); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Live compare failed',e)); self.owner.thread_pool.start(wk)

    def upload(self):
        if not self.path or not self.owner.current_profile:return
        self.save(); prof=self.owner.current_profile; remote=(prof.roots[0] if prof.roots else 'md:')+self.path.name
        if QMessageBox.warning(self,'Upload LS',f'Upload {self.path.name} to {prof.host} as {remote}? Loading an LS file requires controller support/options and can change robot behavior.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        def work(*,progress):
            return ControllerService(prof).upload_file(self.path,remote)
        wk=Worker(work); wk.signals.result.connect(lambda r:self.results.setPlainText(f'Uploaded to {r}')); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Upload failed',e)); self.owner.thread_pool.start(wk)


class SearchAnalysisTab(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.refs=[]
        l=QVBoxLayout(self); sub=QTabWidget(); l.addWidget(sub); sub.addTab(self._search_ui(),'Global Search / X-Ref'); sub.addTab(self._deps_ui(),'Dependencies / Unused'); sub.addTab(self._usage_ui(),'Register Usage'); sub.addTab(self._flow_ui(),'Program Flow')
        from fanuc_workbench.ui.program_diagnostics import ProgramDiagnosticsPanel
        self.program_diagnostics=ProgramDiagnosticsPanel(owner);sub.addTab(self.program_diagnostics,'Program diagnostics')

    def _search_ui(self):
        w=QWidget(); l=QVBoxLayout(w); row=QHBoxLayout(); self.q=QLineEdit(); self.q.setPlaceholderText('R[101], PR[42], DO[23], UFRAME3, CALL PICK…'); self.q.textChanged.connect(self.filter); row.addWidget(self.q,1); b=QPushButton('Re-index'); b.clicked.connect(self.sync_workspace); row.addWidget(b); l.addLayout(row)
        self.search_table=QTableWidget(0,6); self.search_table.setHorizontalHeaderLabels(['Token','Access','Program','Line','File','Context']); self.search_table.horizontalHeader().setSectionResizeMode(5,QHeaderView.Stretch); self.search_table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch); l.addWidget(self.search_table); return w
    def _deps_ui(self):
        w=QWidget(); l=QHBoxLayout(w)
        self.dependency_split=QSplitter(Qt.Horizontal);self.dependency_split.setChildrenCollapsible(False)
        self.dep_tree=QTreeWidget();self.dep_tree.setHeaderLabels(['Program / Calls']);self.dependency_split.addWidget(self.dep_tree)
        self.unused=QTableWidget(0,1);self.unused.setHorizontalHeaderLabels(['Possibly unused program']);self.unused.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.dependency_split.addWidget(self.unused)
        self.dependency_split.setStretchFactor(0,2);self.dependency_split.setStretchFactor(1,1);l.addWidget(self.dependency_split);return w
    def _usage_ui(self):
        w=QWidget(); l=QVBoxLayout(w); self.usage=QTableWidget(0,4); self.usage.setHorizontalHeaderLabels(['Item','Reads','Writes','Other Uses']); self.usage.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch); l.addWidget(self.usage); return w
    def _flow_ui(self):
        w=QWidget(); l=QVBoxLayout(w); self.flow_prog=QComboBox(); self.flow_prog.currentIndexChanged.connect(self.refresh_flow); l.addWidget(self.flow_prog); self.flow=QTableWidget(0,3); self.flow.setHorizontalHeaderLabels(['Line','Type','Instruction']); self.flow.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); l.addWidget(self.flow); return w
    def sync_workspace(self):
        self.program_diagnostics.sync_workspace()
        ws=self.owner.workspace; self.refs=build_xref(ws) if ws else []; self.filter(); self.dep_tree.clear(); self.unused.setRowCount(0); self.usage.setRowCount(0); self.flow_prog.blockSignals(True); self.flow_prog.clear()
        if not ws: self.flow_prog.blockSignals(False); return
        graph=program_dependencies(ws)
        def add(name,parent=None,stack=None):
            stack=set(stack or ())
            item=QTreeWidgetItem([name]); (parent.addChild(item) if parent else self.dep_tree.addTopLevelItem(item))
            if name in stack: item.addChild(QTreeWidgetItem(['(recursive)'])); return
            for c in sorted(graph.get(name,set())): add(c,item,stack|{name})
        called={c for v in graph.values() for c in v}; roots=[n for n in graph if n not in called] or list(graph)
        for n in sorted(roots): add(n)
        for name in unused_programs(ws): r=self.unused.rowCount(); self.unused.insertRow(r); self.unused.setItem(r,0,_item(name))
        for token,u in sorted(register_usage(ws).items()): r=self.usage.rowCount(); self.usage.insertRow(r); [self.usage.setItem(r,c,_item(v)) for c,v in enumerate([token,u['Read'],u['Write'],u['Use']])]
        for p in scan_programs(ws): self.flow_prog.addItem(p.program_name,p.path)
        self.flow_prog.blockSignals(False); self.refresh_flow()
    def filter(self):
        self.search_table.setRowCount(0); q=self.q.text().strip().lower()
        for x in self.refs:
            hay=f'{x.token} {x.program} {x.context} {x.path.name}'.lower()
            if q and q not in hay: continue
            r=self.search_table.rowCount(); self.search_table.insertRow(r); [self.search_table.setItem(r,c,_item(v)) for c,v in enumerate([x.token,x.access,x.program,x.line_number,x.path.name,x.context])]
    def refresh_flow(self,*_):
        self.flow.setRowCount(0); p=self.flow_prog.currentData()
        if not p:return
        for n,k,line in program_flow(Path(p)): r=self.flow.rowCount(); self.flow.insertRow(r); [self.flow.setItem(r,c,_item(v)) for c,v in enumerate([n,k,line])]


class DataTab(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.num_records=[]; self.pr_records=[]; self.frame_records=[]; self.comment_records=[]
        l=QVBoxLayout(self); sub=QTabWidget(); l.addWidget(sub); sub.addTab(self._grid_ui(),'Spreadsheet Data'); sub.addTab(self._pr_ui(),'Position Registers'); sub.addTab(self._frames_ui(),'Frames'); sub.addTab(self._comments_ui(),'All Comments / Descriptions')
    def _grid_ui(self):
        w=QWidget(); l=QVBoxLayout(w); info=QLabel('R[] values/comments and PR[] comments can be edited in the local ASCII workspace. Paste tab-separated cells from Excel.'); info.setWordWrap(True); l.addWidget(info); self.grid=PasteTableWidget(0,6); self.grid.setHorizontalHeaderLabels(['Type','Item','Comment','Value','Source','Record']); self.grid.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.grid.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch); l.addWidget(self.grid); b=QPushButton('Save edited selected row locally'); b.clicked.connect(self.save_grid); l.addWidget(b); return w
    def _pr_ui(self):
        w=QWidget(); l=QVBoxLayout(w); self.pr=PasteTableWidget(0,15); self.pr.setHorizontalHeaderLabels(['Group','PR','Comment','Rep','UF','UT','Config','Joint Values','X','Y','Z','W','P','R','Source']); self.pr.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); l.addWidget(self.pr); row=QHBoxLayout(); b=QPushButton('Save selected PR locally'); b.clicked.connect(self.save_pr); row.addWidget(b); b=QPushButton('Offset selected PR XYZ…'); b.clicked.connect(self.offset_pr); row.addWidget(b); row.addStretch(); l.addLayout(row); return w
    def _frames_ui(self):
        w=QWidget(); l=QVBoxLayout(w); self.frames=QTableWidget(0,9); self.frames.setHorizontalHeaderLabels(['Frame','Comment','X','Y','Z','W','P','R','Source']); self.frames.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.frames.itemSelectionChanged.connect(self.frame_used); l.addWidget(self.frames); l.addWidget(QLabel('Where selected frame is used')); self.frame_refs=QTableWidget(0,4); self.frame_refs.setHorizontalHeaderLabels(['Program','Line','File','Context']); self.frame_refs.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch); l.addWidget(self.frame_refs); return w
    def _comments_ui(self):
        w=QWidget(); l=QVBoxLayout(w); self.comments=QTableWidget(0,5); self.comments.setHorizontalHeaderLabels(['Type','Item','Description','Value/Context','Source']); self.comments.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.comments.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch); l.addWidget(self.comments); row=QHBoxLayout(); b=QPushButton('Save selected description locally'); b.clicked.connect(self.save_description); row.addWidget(b); b=QPushButton('Write selected description to robot…'); b.clicked.connect(self.write_live_description); row.addWidget(b); row.addStretch(); l.addLayout(row); return w
    def sync_workspace(self):
        self.num_records=[]; self.pr_records=[]; self.frame_records=[]; self.comment_records=[]; self.grid.setRowCount(0); self.pr.setRowCount(0); self.frames.setRowCount(0); self.comments.setRowCount(0)
        ws=self.owner.workspace
        if not ws:return
        for p in ws.rglob('*'):
            try:
                if p.name.upper()=='NUMREG.VA': self.num_records.extend(parse_numeric_registers(p))
                elif p.name.upper()=='POSREG.VA': self.pr_records.extend(parse_position_registers(p))
                elif p.name.upper()=='FRAME.DG': self.frame_records.extend(parse_frames(p))
            except Exception: pass
        for i,x in enumerate(self.num_records):
            r=self.grid.rowCount(); self.grid.insertRow(r); vals=['R',f'R[{x.index}]',x.comment,x.value,x.path.name,i]
            for c,v in enumerate(vals): self.grid.setItem(r,c,_item(v,editable=c in (2,3)))
            self.grid.item(r,0).setData(Qt.UserRole,('R',i))
        for i,x in enumerate(self.pr_records):
            r=self.grid.rowCount(); self.grid.insertRow(r); vals=['PR',f'PR[{x.index}]' if x.group==1 else f'PR[{x.group},{x.index}]',x.comment,x.representation,x.path.name,i]
            for c,v in enumerate(vals): self.grid.setItem(r,c,_item(v,editable=c==2))
            self.grid.item(r,0).setData(Qt.UserRole,('PR',i))
            rr=self.pr.rowCount(); self.pr.insertRow(rr); joints='; '.join(f'{k}={v}' for k,v in sorted(x.values.items()) if k.startswith('J'))
            vals2=[x.group,x.index,x.comment,x.representation,x.user_frame,x.user_tool,x.config,joints]+[x.values.get(k,'') for k in ('X','Y','Z','W','P','R')]+[x.path.name]
            for c,v in enumerate(vals2): self.pr.setItem(rr,c,_item(v,editable=c in (2,4,5,7,8,9,10,11,12,13)))
            self.pr.item(rr,0).setData(Qt.UserRole,i)
        for i,x in enumerate(self.frame_records):
            r=self.frames.rowCount(); self.frames.insertRow(r); vals=[f'{x.kind}[{x.index}]',x.comment]+[x.values.get(k,'') for k in ('X','Y','Z','W','P','R')]+[x.path.name]
            for c,v in enumerate(vals): self.frames.setItem(r,c,_item(v)); self.frames.item(r,0).setData(Qt.UserRole,i)
        self.comment_records=scan_comments(ws)
        for i,x in enumerate(self.comment_records):
            r=self.comments.rowCount(); self.comments.insertRow(r); vals=[x.kind,x.identifier,x.comment,x.value,x.path.name]
            for c,v in enumerate(vals): self.comments.setItem(r,c,_item(v,editable=c==2)); self.comments.item(r,0).setData(Qt.UserRole,i)
    def save_grid(self):
        r=self.grid.currentRow()
        if r<0:return
        typ,idx=self.grid.item(r,0).data(Qt.UserRole); comment=self.grid.item(r,2).text(); value=self.grid.item(r,3).text()
        try:
            if typ=='R':
                x=self.num_records[idx]; set_numeric_register_value(x,value)
                rec=next(z for z in scan_comments(self.owner.workspace) if z.path==x.path and z.identifier==f'R[{x.index}]'); set_comment(rec,comment)
            else:
                x=self.pr_records[idx]; set_position_register(x,comment=comment)
        except Exception as e: QMessageBox.critical(self,'Save failed',str(e)); return
        self.sync_workspace()
    def save_pr(self):
        r=self.pr.currentRow()
        if r<0:return
        x=self.pr_records[int(self.pr.item(r,0).data(Qt.UserRole))]
        vals={}
        for c,k in zip(range(8,14),('X','Y','Z','W','P','R')):
            t=self.pr.item(r,c).text().strip()
            if t: vals[k]=float(t)
        joints=self.pr.item(r,7).text().strip()
        for jm in re.finditer(r'\b(J\d+)\s*=\s*([-+]?\d+(?:\.\d+)?)',joints,re.I): vals[jm.group(1).upper()]=float(jm.group(2))
        try:set_position_register(x,comment=self.pr.item(r,2).text(),values=vals,user_frame=self.pr.item(r,4).text(),user_tool=self.pr.item(r,5).text())
        except Exception as e: QMessageBox.critical(self,'Save PR failed',str(e)); return
        self.sync_workspace()
    def offset_pr(self):
        r=self.pr.currentRow()
        if r<0:return
        from PySide6.QtWidgets import QDialog,QDialogButtonBox,QDoubleSpinBox
        d=QDialog(self); d.setWindowTitle('Offset PR'); f=QFormLayout(d); boxes=[]
        for k in ('X','Y','Z'):
            b=QDoubleSpinBox(); b.setRange(-100000,100000); b.setDecimals(3); f.addRow(f'{k} offset (mm)',b); boxes.append(b)
        bb=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); bb.accepted.connect(d.accept); bb.rejected.connect(d.reject); f.addRow(bb)
        if not d.exec():return
        x=self.pr_records[int(self.pr.item(r,0).data(Qt.UserRole))]; values={k:x.values.get(k,0.0)+b.value() for k,b in zip(('X','Y','Z'),boxes)}
        try:set_position_register(x,values=values)
        except Exception as e: QMessageBox.critical(self,'Offset failed',str(e)); return
        self.sync_workspace()
    def frame_used(self):
        self.frame_refs.setRowCount(0); r=self.frames.currentRow()
        if r<0 or not self.owner.workspace:return
        label=self.frames.item(r,0).text().upper()
        m=re.match(r'(UFRAME|UTOOL|JOG)\[(\d+)\]',label)
        if not m:return
        kind,idx=m.group(1),m.group(2)
        pats=[]
        if kind=='UFRAME': pats=[rf'\bUFRAME\s*\[?\s*{idx}\s*\]?',rf'\bUFRAME_NUM\s*=\s*{idx}',rf'\bUF\s*[:=]\s*{idx}']
        elif kind=='UTOOL': pats=[rf'\bUTOOL\s*\[?\s*{idx}\s*\]?',rf'\bUTOOL_NUM\s*=\s*{idx}',rf'\bUT\s*[:=]\s*{idx}']
        else: pats=[rf'\bJOG(?:FRAME)?\s*\[?\s*{idx}\s*\]?']
        for ref in build_xref(self.owner.workspace):
            if any(re.search(p,ref.context,re.I) for p in pats):
                rr=self.frame_refs.rowCount(); self.frame_refs.insertRow(rr); [self.frame_refs.setItem(rr,c,_item(v)) for c,v in enumerate([ref.program,ref.line_number,ref.path.name,ref.context])]
    def save_description(self):
        r=self.comments.currentRow()
        if r<0:return
        rec=self.comment_records[int(self.comments.item(r,0).data(Qt.UserRole))]
        try:
            bak=rec.path.with_suffix(rec.path.suffix+'.before_comment_edit')
            if not bak.exists(): shutil.copy2(rec.path,bak)
            set_comment(rec,self.comments.item(r,2).text())
        except Exception as e: QMessageBox.critical(self,'Save failed',str(e)); return
        self.sync_workspace()
    def write_live_description(self):
        r=self.comments.currentRow()
        if r<0 or not self.owner.current_profile:return
        rec=self.comment_records[int(self.comments.item(r,0).data(Qt.UserRole))]; comment=self.comments.item(r,2).text()
        try:target=target_for_record(rec)
        except Exception as e:QMessageBox.information(self,'Local-only description',str(e));return
        prof=self.owner.current_profile
        message = f"Change live controller description for {target.display_name} on {prof.host} to:\n\n{comment or '(blank)'}?\n\nThe register/position value is not changed."
        if QMessageBox.warning(self,'Write live description',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        def work(*,progress):return set_live_comment(prof.host,rec,comment,timeout=prof.timeout)
        wk=Worker(work); wk.signals.result.connect(lambda x:self.owner.statusBar().showMessage(f'Updated {x.display_name} description on {prof.host}')); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Live description failed',e)); self.owner.thread_pool.start(wk)


class IOTab(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.rows=[]; self.refs=[]
        l=QVBoxLayout(self); info=QLabel('Import PLC tag CSV/L5X exports in the PLC Tags tab below. Select an I/O row and a PLC tag to assign it, or review explicit I/O-reference suggestions. Save the mapping locally after review.'); info.setWordWrap(True); l.addWidget(info)
        self.table=PasteTableWidget(0,8); self.table.setHorizontalHeaderLabels(['I/O','Description','Value','Rack','Slot','Start','PLC Tag','Source']); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(6,QHeaderView.Stretch); self.table.itemSelectionChanged.connect(self.used); l.addWidget(self.table,2)
        row=QHBoxLayout(); b=QPushButton('Save PLC tag map'); b.clicked.connect(self.save_map); row.addWidget(b); b=QPushButton('Export CSV…'); b.clicked.connect(self.export_csv); row.addWidget(b); row.addStretch(); l.addLayout(row)
        self.details=QTabWidget();l.addWidget(self.details,1)
        used=QWidget();used_layout=QVBoxLayout(used);used_layout.addWidget(QLabel('Where selected I/O is used'));self.used_table=QTableWidget(0,4); self.used_table.setHorizontalHeaderLabels(['Program','Line','Access','Context']); self.used_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch);used_layout.addWidget(self.used_table);self.details.addTab(used,'Program cross-references')
        from fanuc_workbench.ui.plc_catalog import PlcCatalog
        self.plc_catalog=PlcCatalog(self);self.details.addTab(self.plc_catalog,'PLC Tags / Import')
    def sync_workspace(self):
        self.rows=[]; self.refs=[]; self.table.setRowCount(0); ws=self.owner.workspace
        if not ws:return
        for p in ws.rglob('*'):
            if p.name.upper()=='IOSTATE.DG':
                try:self.rows.extend(parse_io(p))
                except Exception:pass
        self.refs=build_xref(ws); mapping=load_plc_map(ws)
        for i,x in enumerate(self.rows):
            token=f'{x.io_type}[{x.index}]'; r=self.table.rowCount(); self.table.insertRow(r); vals=[token,x.comment,x.value,x.rack,x.slot,x.start,mapping.get(token,''),x.path.name]
            for c,v in enumerate(vals): self.table.setItem(r,c,_item(v,editable=c==6)); self.table.item(r,0).setData(Qt.UserRole,i)
    def used(self):
        self.used_table.setRowCount(0); r=self.table.currentRow()
        if r<0:return
        token=self.table.item(r,0).text().replace('DIN','DI').replace('DOUT','DO').replace('GIN','GI').replace('GOUT','GO').replace('AIN','AI').replace('AOUT','AO')
        for x in self.refs:
            xt=x.token.replace('DIN','DI').replace('DOUT','DO').replace('GIN','GI').replace('GOUT','GO').replace('AIN','AI').replace('AOUT','AO')
            if xt==token:
                rr=self.used_table.rowCount(); self.used_table.insertRow(rr); [self.used_table.setItem(rr,c,_item(v)) for c,v in enumerate([x.program,x.line_number,x.access,x.context])]
    def save_map(self):
        if not self.owner.workspace:return
        mapping={self.table.item(r,0).text():self.table.item(r,6).text() for r in range(self.table.rowCount()) if self.table.item(r,6).text().strip()}; save_plc_map(self.owner.workspace,mapping); self.owner.statusBar().showMessage('Saved PLC I/O tag mapping in workspace')
    def export_csv(self):
        if not self.owner.workspace:return
        p,_=QFileDialog.getSaveFileName(self,'Export I/O map',str(self.owner.workspace/'fanuc_plc_io_map.csv'),'CSV (*.csv)')
        if not p:return
        rows=[[self.table.item(r,c).text() for c in range(7)] for r in range(self.table.rowCount())]; export_io_csv(Path(p),rows); QMessageBox.information(self,'Exported',p)


class DiagnosticsTab(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner
        l=QVBoxLayout(self); sub=QTabWidget(); l.addWidget(sub); sub.addTab(self._health(),'Health'); sub.addTab(self._alarms(),'Alarm History'); sub.addTab(self._master(),'Mastering (Read-only)'); sub.addTab(self._dcs(),'DCS (Read-only)')
    def _health(self):
        w=QWidget(); l=QVBoxLayout(w); self.health=QTableWidget(0,2); self.health.setHorizontalHeaderLabels(['Item','Value']); self.health.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); l.addWidget(self.health); b=QPushButton('Probe live FTP reachability'); b.clicked.connect(self.probe); l.addWidget(b); return w
    def _alarms(self):
        w=QWidget(); l=QVBoxLayout(w); self.alarms=QTableWidget(0,6); self.alarms.setHorizontalHeaderLabels(['Time','Alarm','Severity','Message','Source','Line']); self.alarms.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch); l.addWidget(self.alarms,2); l.addWidget(QLabel('Recurring alarm counts')); self.alarm_counts=QTableWidget(0,3); self.alarm_counts.setHorizontalHeaderLabels(['Alarm','Count','Latest message']); self.alarm_counts.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); l.addWidget(self.alarm_counts,1); return w
    def _master(self):
        w=QWidget(); l=QVBoxLayout(w); n=QLabel('Inspection only. Mastering values are not editable from FanucMCPServer.'); n.setWordWrap(True); l.addWidget(n); self.master=QTableWidget(0,3); self.master.setHorizontalHeaderLabels(['Source','Line','Evidence']); self.master.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); l.addWidget(self.master,2); row=QHBoxLayout(); self.master_other=QLineEdit(); row.addWidget(QLabel('Compare with backup')); row.addWidget(self.master_other,1); b=QPushButton('Browse…'); b.clicked.connect(lambda:_browse_dir(self.master_other,'Choose mastering comparison backup')); row.addWidget(b); b=QPushButton('Compare mastering-related data'); b.clicked.connect(self.compare_mastering); row.addWidget(b); l.addLayout(row); self.master_diff=QPlainTextEdit(); self.master_diff.setReadOnly(True); l.addWidget(self.master_diff,1); return w
    def _dcs(self):
        w=QWidget(); l=QVBoxLayout(w); n=QLabel('Inspection/diff only. FanucMCPServer does not write DCS configuration.'); l.addWidget(n); self.dcs=QTableWidget(0,3); self.dcs.setHorizontalHeaderLabels(['Source','Line','Evidence']); self.dcs.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); l.addWidget(self.dcs,1); row=QHBoxLayout(); self.dcs_other=QLineEdit(); row.addWidget(QLabel('Compare with backup')); row.addWidget(self.dcs_other,1); b=QPushButton('Browse…'); b.clicked.connect(lambda:_browse_dir(self.dcs_other,'Choose DCS comparison backup')); row.addWidget(b); b=QPushButton('Compare DCS files'); b.clicked.connect(self.compare_dcs); row.addWidget(b); l.addLayout(row); self.dcs_diff=QPlainTextEdit(); self.dcs_diff.setReadOnly(True); l.addWidget(self.dcs_diff,1); return w
    def sync_workspace(self):
        for t in (self.health,self.alarms,self.alarm_counts,self.master,self.dcs):t.setRowCount(0)
        ws=self.owner.workspace
        if not ws:return
        for k,v in health_summary(ws).items(): r=self.health.rowCount(); self.health.insertRow(r); self.health.setItem(r,0,_item(k)); self.health.setItem(r,1,_item(v))
        alarms=scan_alarms(ws)
        for a in alarms: r=self.alarms.rowCount(); self.alarms.insertRow(r); [self.alarms.setItem(r,c,_item(v)) for c,v in enumerate([a.timestamp,a.code,a.severity,a.message,a.source.name,a.line_number])]
        counts={}
        for a in alarms: counts.setdefault(a.code,[0,a.message]); counts[a.code][0]+=1; counts[a.code][1]=a.message
        for code,(count,msg) in sorted(counts.items(),key=lambda kv:(-kv[1][0],kv[0])): r=self.alarm_counts.rowCount(); self.alarm_counts.insertRow(r); [self.alarm_counts.setItem(r,c,_item(v)) for c,v in enumerate([code,count,msg])]
        for table,rows in ((self.master,mastering_evidence(ws)),(self.dcs,dcs_evidence(ws))):
            for p,n,line in rows: r=table.rowCount(); table.insertRow(r); [table.setItem(r,c,_item(v)) for c,v in enumerate([p.name,n,line])]
    def probe(self):
        if not self.owner.current_profile:return
        prof=self.owner.current_profile
        def work(*,progress):
            return ControllerService(prof).test_connection()
        wk=Worker(work); wk.signals.result.connect(lambda d:QMessageBox.information(self,'Live health',f'{prof.transport.upper()} reachable.\n'+'\n'.join(f'{k}: {v} entries' for k,v in d.items())+'\n\nMode/running-program data is not claimed unless a backend exposes it.')); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Probe failed',e)); self.owner.thread_pool.start(wk)
    def compare_mastering(self):
        other=Path(self.master_other.text())
        if not self.owner.workspace or not other.is_dir():return
        ch=compare_workspaces(other,self.owner.workspace); keys=('MASTER','DMR','SERVO','SYSMAST','REFPOS','PULSE')
        rows=[x for x in ch if any(k in x.relative_path.upper() for k in keys)]
        self.master_diff.setPlainText('\n'.join(f'{x.status}: {x.relative_path}' for x in rows) or 'No mastering-related file differences detected by filename.')
    def compare_dcs(self):
        if not self.owner.workspace or not Path(self.dcs_other.text()).is_dir():return
        ch=dcs_compare(Path(self.dcs_other.text()),self.owner.workspace); self.dcs_diff.setPlainText('\n'.join(f'{x.status}: {x.relative_path}' for x in ch) or 'No DCS/safety-named file differences detected.')


class ProjectTab(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.project=None
        l=QVBoxLayout(self); top=QHBoxLayout(); self.path=QLineEdit(); top.addWidget(QLabel('Project')); top.addWidget(self.path,1)
        for text,fn in [('Create…',self.create),('Open…',self.open),('Add current robot/workspace',self.add_current)]: b=QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
        l.addLayout(top); self.robots=QTableWidget(0,7); self.robots.setHorizontalHeaderLabels(['Robot','Host','Controller','Model','Software','Backups','Notes']); self.robots.horizontalHeader().setSectionResizeMode(6,QHeaderView.Stretch); l.addWidget(self.robots,1); btn=QPushButton('Save edited robot metadata'); btn.clicked.connect(self.save_metadata); l.addWidget(btn)
        comp=QFormLayout(); self.a=QLineEdit(); self.b=QLineEdit();
        for label,e in [('Cell compare A',self.a),('Cell compare B',self.b)]:
            row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(e); btn=QPushButton('Browse…'); btn.clicked.connect(lambda _=False,x=e:_browse_dir(x,'Choose robot backup')); h.addWidget(btn); comp.addRow(label,row)
        l.addLayout(comp); row=QHBoxLayout(); btn=QPushButton('Compare robots/backups'); btn.clicked.connect(self.compare); row.addWidget(btn); btn=QPushButton('Build deployment package from A…'); btn.clicked.connect(self.clone); row.addWidget(btn); row.addStretch(); l.addLayout(row); self.result=QPlainTextEdit(); self.result.setReadOnly(True); l.addWidget(self.result,1)
    def create(self):
        p=QFileDialog.getExistingDirectory(self,'Project folder',str(Path.home()))
        if not p:return
        name=Path(p).name; self.project=RobotProject(name,Path(p)); self.project.save(); self.path.setText(p); self.refresh()
    def open(self):
        p=QFileDialog.getExistingDirectory(self,'Open project',self.path.text() or str(Path.home()))
        if not p:return
        try:self.project=RobotProject.load(Path(p))
        except Exception as e:QMessageBox.critical(self,'Open project failed',str(e));return
        self.path.setText(p); self.refresh()
    def add_current(self):
        if not self.project or not self.owner.current_profile:return
        p=self.owner.current_profile; old=next((r for r in self.project.robots if r.name==p.name),ProjectRobot(p.name)); old.host=p.host; self.project.upsert_robot(old)
        if self.owner.workspace:
            dest=self.project.add_backup(p.name,self.owner.workspace); self.owner.workspace=dest; self.owner.refresh_workspace()
        self.refresh()
    def refresh(self):
        self.robots.setRowCount(0)
        if not self.project:return
        for x in self.project.robots:
            r=self.robots.rowCount(); self.robots.insertRow(r); vals=[x.name,x.host,x.controller,x.model,x.software,len(x.backups),x.notes]
            for c,v in enumerate(vals): self.robots.setItem(r,c,_item(v,editable=c in (1,2,3,4,6)))
    def save_metadata(self):
        if not self.project:return
        for r,x in enumerate(self.project.robots):
            x.host=self.robots.item(r,1).text(); x.controller=self.robots.item(r,2).text(); x.model=self.robots.item(r,3).text(); x.software=self.robots.item(r,4).text(); x.notes=self.robots.item(r,6).text()
        self.project.save(); self.owner.statusBar().showMessage('Project robot metadata saved')
    def compare(self):
        if not Path(self.a.text()).is_dir() or not Path(self.b.text()).is_dir():return
        ch=compare_workspaces(Path(self.a.text()),Path(self.b.text())); self.result.setPlainText('\n'.join(f'{x.status:13} {x.relative_path}' for x in ch) or 'No differences.')
    def clone(self):
        src=Path(self.a.text())
        if not src.is_dir():return
        dest=QFileDialog.getExistingDirectory(self,'Deployment package destination',str(src.parent))
        if not dest:return
        proj=self.project or RobotProject('AdHoc',Path(dest)); out=proj.deployment_package(src,Path(dest)); self.result.setPlainText(f'Deployment package created:\n{out}\n\nOnly default-safe program/register/frame/application-data candidates were copied. Use Selective Restore to review before writing a robot.')


class DocumentationTab(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; l=QVBoxLayout(self); n=QLabel('Generate a documentation set from the active workspace: HTML, Excel (.xlsx), and PDF. Includes robot/backup inventory, programs, R/PR, frames, I/O, dependencies, usage, and alarms.'); n.setWordWrap(True); l.addWidget(n); row=QHBoxLayout(); self.dest=QLineEdit(str(Path.home()/'FanucReports')); row.addWidget(QLabel('Destination')); row.addWidget(self.dest,1); b=QPushButton('Browse…'); b.clicked.connect(lambda:_browse_dir(self.dest,'Report destination')); row.addWidget(b); l.addLayout(row); row2=QHBoxLayout(); self.baseline=QLineEdit(); row2.addWidget(QLabel('Optional baseline backup')); row2.addWidget(self.baseline,1); b=QPushButton('Browse…'); b.clicked.connect(lambda:_browse_dir(self.baseline,'Choose baseline backup')); row2.addWidget(b); l.addLayout(row2); b=QPushButton('Generate HTML + Excel + PDF'); b.clicked.connect(self.generate); l.addWidget(b); self.log=QPlainTextEdit(); self.log.setReadOnly(True); l.addWidget(self.log,1)
    def sync_workspace(self): pass
    def generate(self):
        if not self.owner.workspace:return
        dest=Path(self.dest.text()); root=self.owner.workspace
        baseline=Path(self.baseline.text()) if self.baseline.text().strip() else None
        def work(*,progress):return export_all(root,dest,root.name+'_documentation',baseline=baseline if baseline and baseline.is_dir() else None)
        wk=Worker(work); wk.signals.result.connect(lambda rows:self.log.setPlainText('\n'.join(str(x) for x in rows))); wk.signals.error.connect(lambda e:QMessageBox.critical(self,'Report generation failed',e)); self.owner.thread_pool.start(wk)
