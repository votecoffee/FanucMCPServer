from __future__ import annotations
import json, math
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QPointF,QSignalBlocker
from PySide6.QtGui import QPainter,QPen
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QComboBox,QSpinBox,QDoubleSpinBox,QCheckBox,QFileDialog,QMessageBox,QPlainTextEdit,QTabWidget,QAbstractItemView,QSplitter)
from fanuc_workbench.backends.factory import create_backend
from fanuc_workbench.core.workers import Worker
from fanuc_workbench.services.monitoring import WatchSpec,WorkspaceSampleProvider,ControllerFileSampleProvider,TimelineRecorder,CycleRecorder,profile_cycle,compare_cycle_profiles,CycleEvent
from fanuc_workbench.services.fleet import FleetManager,FleetRobot
from fanuc_workbench.services.commissioning import create_baseline,compare_baseline,load_checklist,save_checklist,ChecklistItem
from fanuc_workbench.services.robot_lint import lint_workspace
from fanuc_workbench.services.inventory import option_inventory
from fanuc_workbench.services.roboguide import discover_roboguide,stage_backup_for_roboguide,launch_roboguide
from fanuc_workbench.services.ethernetip import scan_ethernetip
from fanuc_workbench.services.handshake import HandshakeSignal,load_handshake,save_handshake,analyze_handshake
from fanuc_workbench.services.transforms import transform_pose_between_frames
from fanuc_workbench.services.snapshots import capture_snapshot,compare_snapshots
from fanuc_workbench.services.diagnostics import alarm_intelligence
from fanuc_workbench.services.display import watch_value
from fanuc_workbench.services.profiles import copy_profile,profile_signature
from fanuc_workbench.services.live_recording import LiveRecording,recording_source

def item(v,editable=False):
    x=QTableWidgetItem('' if v is None else str(v))
    if not editable:x.setFlags(x.flags() & ~Qt.ItemIsEditable)
    return x

def browse_dir(edit,title):
    p=QFileDialog.getExistingDirectory(edit,title,edit.text() or str(Path.home()))
    if p:edit.setText(p)

def browse_file(edit,title,flt='All files (*)'):
    p,_=QFileDialog.getOpenFileName(edit,title,edit.text() or str(Path.home()),flt)
    if p:edit.setText(p)

class TrendPlot(QWidget):
    def __init__(self,parent=None):super().__init__(parent);self.samples=[];self.key='';self.setMinimumHeight(180)
    def set_data(self,samples,key):self.samples=samples;self.key=key;self.update()
    def paintEvent(self,e):
        p=QPainter(self);p.fillRect(self.rect(),self.palette().base());p.setPen(QPen(self.palette().text().color()));p.drawText(8,18,self.key or 'Select a numeric watch signal')
        vals=[]
        for s in self.samples:
            v=s.values.get(self.key)
            try:v=float(v)
            except (TypeError,ValueError):continue
            vals.append((s.timestamp,v))
        if len(vals)<2:return
        left,top,right,bottom=45,28,self.width()-12,self.height()-28
        t0,t1=vals[0][0],vals[-1][0];v0=min(v for _,v in vals);v1=max(v for _,v in vals)
        if t1==t0:t1=t0+1
        if v1==v0:v1=v0+1
        p.drawLine(left,bottom,right,bottom);p.drawLine(left,top,left,bottom);p.drawText(2,top+5,f'{v1:.3g}');p.drawText(2,bottom,f'{v0:.3g}')
        pts=[QPointF(left+(t-t0)/(t1-t0)*(right-left),bottom-(v-v0)/(v1-v0)*(bottom-top)) for t,v in vals]
        for a,b in zip(pts,pts[1:]):p.drawLine(a,b)

class LiveMonitorTab(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.recording=LiveRecording();self.inflight=False;self._workspace_identity=None
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll_once)
        l=QVBoxLayout(self);top=QHBoxLayout();self.source=QComboBox();self.source.addItems(['Active workspace snapshot','Live controller files']);top.addWidget(QLabel('Source'));top.addWidget(self.source);self.interval=QDoubleSpinBox();self.interval.setRange(.5,60);self.interval.setValue(2);self.interval.setSuffix(' s');top.addWidget(QLabel('Poll'));top.addWidget(self.interval);self.start=QPushButton('Start');self.start.clicked.connect(self.toggle);top.addWidget(self.start);b=QPushButton('Sample Once');b.clicked.connect(self.poll_once);top.addWidget(b);top.addStretch();l.addLayout(top)
        self.target_label=QLabel();self.target_label.setWordWrap(True);self.target_label.setMaximumWidth(520);top.addWidget(self.target_label);self.source.currentIndexChanged.connect(self.update_target_label)
        self.watch=QTableWidget(6,3);self.watch.setHorizontalHeaderLabels(['Expression','Alias','Live Value']);self.watch.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.watch.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        defaults=[('R[1]','PartCount'),('PR[1]','PickPR'),('DI[1]','DI1'),('DO[1]','DO1')]
        for r,(a,b) in enumerate(defaults):self.watch.setItem(r,0,item(a,True));self.watch.setItem(r,1,item(b,True));self.watch.setItem(r,2,item(''))
        row=QHBoxLayout();b=QPushButton('Add watch row');b.clicked.connect(self.add_watch);row.addWidget(b);b=QPushButton('Remove selected watch rows');b.clicked.connect(self.remove_watches);row.addWidget(b);row.addStretch();l.addLayout(row)
        b=QPushButton('New recording');b.clicked.connect(self.new_recording);row.addWidget(b)
        b=QPushButton('Export recording JSON…');b.clicked.connect(self.export_recording);row.addWidget(b)
        self.recording_label=QLabel();self.recording_label.setWordWrap(True);row.addWidget(self.recording_label)
        self.watch.setColumnWidth(2,520);self.watch.setWordWrap(True);self.watch.horizontalHeader().sectionResized.connect(lambda *_:self.watch.resizeRowsToContents())
        self.splitter=QSplitter(Qt.Vertical);self.splitter.setChildrenCollapsible(False);self.watch.setMinimumHeight(100);self.splitter.addWidget(self.watch);l.addWidget(self.splitter,1)
        sub=QTabWidget();self.recorders=sub;sub.setMinimumHeight(100);self.splitter.addWidget(sub);self.splitter.setSizes([300,450]);sub.addTab(self._trend_ui(),'Trend');sub.addTab(self._timeline_ui(),'I/O Timeline');sub.addTab(self._cycle_ui(),'Cycle Recorder / Profiler');sub.addTab(self._snapshot_ui(),'Troubleshooting Snapshot');sub.addTab(self._alarm_ui(),'Alarm Intelligence')
        self.restore_watches();self.refresh_recording_label()
        self.watch_save_timer=QTimer(self);self.watch_save_timer.setSingleShot(True);self.watch_save_timer.setInterval(250);self.watch_save_timer.timeout.connect(self.save_watches)
        self.watch.itemChanged.connect(self.watch_changed);self.source.currentIndexChanged.connect(self.source_changed)
    @property
    def trend(self):return self.recording.trend
    @property
    def timeline(self):return self.recording.timeline
    @timeline.setter
    def timeline(self,value):self.recording.timeline=value
    @property
    def cycle(self):return self.recording.cycle
    @cycle.setter
    def cycle(self,value):self.recording.cycle=value
    @property
    def last_sample(self):return self.recording.last_sample
    @property
    def cycle_recording(self):return self.recording.cycle_recording
    @cycle_recording.setter
    def cycle_recording(self,value):self.recording.cycle_recording=value
    def current_source(self):
        return recording_source(live=self.source.currentIndex()==1,profile=self.owner.current_profile,workspace=self.owner.workspace)
    def matching_recording(self):
        try:return self.recording.matches(self.current_source(),self.specs())
        except ValueError:return False
    def timeline_for_current_source(self):return self.timeline.events if self.matching_recording() else []
    def refresh_recording_label(self):
        source=self.recording.source
        label='Not started'
        if source:
            profile=source.get('profile')
            label=f"{profile['name']} ({profile['host']})" if profile else (Path(source['path']).name if source.get('path') else 'Workspace')
        self.recording_label.setText(f'Recording: {label} — {self.recording.received_samples}/{self.recording.sample_limit} samples')
        self.recording_label.setToolTip(json.dumps(source,indent=2) if source else 'A recording is bound to its source and watch definitions when collection starts.')
    def restore_watches(self):
        rows=self.owner.settings.preferences().get('live_watches')
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:return
        if not all(isinstance(row,dict) and all(isinstance(row.get(key),str) for key in ('expression','alias')) for row in rows):return
        self.watch.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,value in enumerate([row['expression'],row['alias'],'']):self.watch.setItem(r,c,item(value,c<2))
    def save_watches(self):
        self.watch_save_timer.stop()
        rows=[{'expression':self.watch.item(r,0).text() if self.watch.item(r,0) else '',
               'alias':self.watch.item(r,1).text() if self.watch.item(r,1) else ''} for r in range(self.watch.rowCount())]
        try:self.owner.settings.update_preferences(live_watches=rows)
        except (OSError,ValueError):self.owner.statusBar().showMessage('Could not save the watch list.')
    def watch_changed(self,cell):
        if cell.column()<2:self.configuration_changed('Watch definitions changed');self.watch_save_timer.start()
    def configuration_changed(self,reason):
        self.pause_sampling(reason,end_cycle=True)
        with QSignalBlocker(self.watch):
            for r in range(self.watch.rowCount()):self.watch.setItem(r,2,item(''))
        if self.recording.has_data:self.owner.statusBar().showMessage(reason+'. Export the current recording and choose New recording before collecting a different source/watch list.')
    def source_changed(self,*_):self.configuration_changed('Sampling source changed')
    def pause_sampling(self,reason,*,end_cycle=False):
        paused=self.timer.isActive() or self.inflight or self.cycle_recording
        self.timer.stop();self.start.setText('Start');self._sample_generation=getattr(self,'_sample_generation',0)+1
        if end_cycle and self.cycle_recording:self.cycle.mark('END',reason);self.cycle_recording=False;self.render_profile()
        return paused
    def new_recording(self):
        if self.recording.has_data and QMessageBox.question(self,'New recording','Start a new recording and clear the current captured data? Export the recording first if you want to keep it.',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)!=QMessageBox.Yes:return
        self.pause_sampling('New recording');self.recording=LiveRecording()
        self.events.setRowCount(0);self.profile.setRowCount(0);self.cycle_compare.clear();self.alarmtbl.setRowCount(0)
        self.plot_key.clear();self.cycle_key.clear();self.plot.set_data([], '')
        with QSignalBlocker(self.watch):
            for r in range(self.watch.rowCount()):self.watch.setItem(r,2,item(''))
        self.refresh_recording_label();self.owner.statusBar().showMessage('New recording ready. Select Sample Once or Start when ready.')
    def export_recording(self):
        path,_=QFileDialog.getSaveFileName(self,'Export recording',str(Path.home()/'fanuc_recording.json'),'JSON (*.json)')
        if not path:return
        try:self.recording.export_json(Path(path))
        except (OSError,ValueError,TypeError) as exc:QMessageBox.critical(self,'Recording export failed',str(exc));return
        self.owner.statusBar().showMessage('Recording exported: '+path)
    def add_watch(self):
        if self.watch.rowCount()>=1000:self.owner.statusBar().showMessage('Watch list reached 1000 rows. Remove a row before adding another.');return
        with QSignalBlocker(self.watch):
            row=self.watch.rowCount();self.watch.insertRow(row)
            for column in range(3):self.watch.setItem(row,column,item('',column<2))
        self.watch.setCurrentCell(row,0)
        self.configuration_changed('Watch rows changed');self.watch_save_timer.start()
    def remove_watches(self):
        rows={index.row() for index in self.watch.selectedIndexes()}
        with QSignalBlocker(self.watch):
            for row in sorted(rows,reverse=True):self.watch.removeRow(row)
        if not self.watch.rowCount():self.add_watch()
        elif rows:self.configuration_changed('Watch rows changed');self.watch_save_timer.start()
    def spec_rows(self):
        out=[]
        for r in range(self.watch.rowCount()):
            a=self.watch.item(r,0).text().strip() if self.watch.item(r,0) else ''
            b=self.watch.item(r,1).text().strip() if self.watch.item(r,1) else ''
            if a:out.append((r,WatchSpec(a,b)))
        return out
    def specs(self):return [s for _,s in self.spec_rows()]
    def _trend_ui(self):
        w=QWidget();l=QVBoxLayout(w);row=QHBoxLayout();self.plot_key=QComboBox();self.plot_key.currentTextChanged.connect(lambda k:self.plot.set_data(self.trend.samples,k));row.addWidget(QLabel('Signal'));row.addWidget(self.plot_key);b=QPushButton('Export CSV…');b.clicked.connect(self.export_trend);row.addWidget(b);b=QPushButton('Clear');b.clicked.connect(lambda:(self.trend.clear(),self.plot.update()));row.addWidget(b);row.addStretch();l.addLayout(row);self.plot=TrendPlot();l.addWidget(self.plot,1);return w
    def _timeline_ui(self):
        w=QWidget();l=QVBoxLayout(w);self.events=QTableWidget(0,4);self.events.setHorizontalHeaderLabels(['Time','Signal','Old','New']);self.events.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);l.addWidget(self.events);row=QHBoxLayout();b=QPushButton('Export timeline CSV…');b.clicked.connect(self.export_timeline);row.addWidget(b);b=QPushButton('Clear timeline');b.clicked.connect(self.clear_timeline);row.addWidget(b);row.addStretch();l.addLayout(row);return w
    def _cycle_ui(self):
        w=QWidget();l=QVBoxLayout(w);row=QHBoxLayout();self.cycle_key=QComboBox();row.addWidget(QLabel('Cycle state watch alias/expression'));row.addWidget(self.cycle_key,1);b=QPushButton('Start cycle recording');b.clicked.connect(self.start_cycle);row.addWidget(b);b=QPushButton('Stop / Profile');b.clicked.connect(self.stop_cycle);row.addWidget(b);b=QPushButton('Mark event');b.clicked.connect(self.mark_event);row.addWidget(b);l.addLayout(row);self.profile=QTableWidget(0,4);self.profile.setHorizontalHeaderLabels(['State','Seconds','% cycle','Notes']);self.profile.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);l.addWidget(self.profile);row=QHBoxLayout();b=QPushButton('Export cycle JSON…');b.clicked.connect(self.export_cycle);row.addWidget(b);b=QPushButton('Compare two cycle recordings…');b.clicked.connect(self.compare_cycles);row.addWidget(b);row.addStretch();l.addLayout(row);self.cycle_compare=QPlainTextEdit();self.cycle_compare.setReadOnly(True);l.addWidget(self.cycle_compare);return w
    def _snapshot_ui(self):
        w=QWidget();l=QVBoxLayout(w);n=QLabel('Capture an engineering snapshot of the active backup plus the latest live-watch values. This is read-only evidence for troubleshooting/handoff.');n.setWordWrap(True);l.addWidget(n);row=QHBoxLayout();b=QPushButton('Capture snapshot…');b.clicked.connect(self.capture);row.addWidget(b);b=QPushButton('Compare snapshots…');b.clicked.connect(self.compare_snap);row.addWidget(b);row.addStretch();l.addLayout(row);self.snaplog=QPlainTextEdit();self.snaplog.setReadOnly(True);l.addWidget(self.snaplog);return w
    def _alarm_ui(self):
        w=QWidget();l=QVBoxLayout(w);b=QPushButton('Analyze active backup alarms + captured timeline');b.clicked.connect(self.run_alarm_intel);l.addWidget(b);self.alarmtbl=QTableWidget(0,4);self.alarmtbl.setHorizontalHeaderLabels(['Alarm','Count','Typical message','Common preceding changes']);self.alarmtbl.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);self.alarmtbl.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch);l.addWidget(self.alarmtbl);return w
    def sync_workspace(self):
        identity=str(self.owner.workspace.resolve()) if self.owner.workspace else None
        if identity!=self._workspace_identity:
            self._workspace_identity=identity
            if self.source.currentIndex()==0:self.configuration_changed('Workspace changed')
    def toggle(self):
        if self.timer.isActive():self.pause_sampling('Polling paused');return
        ms=max(500,int(self.interval.value()*1000));
        if self.source.currentIndex()==1:ms=max(2000,ms)
        self.timer.start(ms);self.start.setText('Stop');self.poll_once()
    def update_target_label(self,*_):
        profile=self.owner.current_profile
        self.target_label.setText(f'File target: {profile.name} — {profile.host}:{profile.port}' if profile else 'No file target selected')
        self.target_label.setToolTip(self.target_label.text());self.target_label.setVisible(self.source.currentIndex()==1)
    def pause_connection_polling(self):
        if self.source.currentIndex()!=1:return False
        paused=self.timer.isActive() or self.inflight or self.cycle_recording
        self.configuration_changed('Connection changed')
        return paused
    def poll_once(self):
        if self.inflight:return
        specs=self.specs()
        if not specs:return
        if self.source.currentIndex()==0 and not self.owner.workspace:
            self.pause_sampling('No workspace');self.owner.statusBar().showMessage('Open a workspace before sampling.');return
        if self.source.currentIndex()==1 and not self.owner.current_profile:
            self.pause_sampling('No connection');self.owner.statusBar().showMessage('Select a robot connection before sampling.');return
        try:self.recording.ensure_context(self.current_source(),specs)
        except ValueError as exc:self.pause_sampling('Recording paused',end_cycle=True);self.owner.statusBar().showMessage(str(exc));return
        self.refresh_recording_label()
        if self.source.currentIndex()==0:
            if not self.owner.workspace:return
            provider=WorkspaceSampleProvider(self.owner.workspace)
            self._sample_profile=None
        else:
            if not self.owner.current_profile:return
            provider=ControllerFileSampleProvider(create_backend(self.owner.current_profile),self.owner.current_profile,max(2,self.interval.value()))
            self._sample_profile=self.owner.current_profile
        self.inflight=True
        generation=getattr(self,'_sample_generation',0);source=self.current_source();definitions=[(s.expression,s.alias) for s in specs]
        def work(*,progress):return provider.sample(specs)
        def current():
            return generation==getattr(self,'_sample_generation',0) and self.current_source()==source and definitions==[(s.expression,s.alias) for s in self.specs()]
        def ready(sample):
            if current():self.sample_ready(sample)
        def failed(error):
            if current():self.owner.statusBar().showMessage('Live sample failed: '+error.splitlines()[-1])
        wk=Worker(work);wk.signals.result.connect(ready);wk.signals.error.connect(failed);wk.signals.finished.connect(lambda:setattr(self,'inflight',False));self.owner.thread_pool.start(wk)
    def sample_ready(self,s):
        before=len(self.timeline.events)
        try:
            self.recording.ensure_context(self.current_source(),self.specs())
            specs=self.specs();cycle_key=self.cycle_key.currentText() or (specs[0].alias or specs[0].expression if specs else '')
            self.recording.add(s,cycle_key=cycle_key)
        except (ValueError,TypeError,OverflowError) as exc:self.pause_sampling('Recording paused',end_cycle=True);self.owner.statusBar().showMessage(str(exc));return
        if getattr(self,'_sample_profile',None) is not None and hasattr(self.owner,'remember_connection'):self.owner.remember_connection(self._sample_profile)
        self.refresh_recording_label()
        aliases=[]
        for r,spec in self.spec_rows():
            k=spec.alias or spec.expression;aliases.append(k);v=s.values.get(k);text,detail=watch_value(v,s.errors.get(k) if k in s.errors else None);cell=item(text);cell.setToolTip(detail);self.watch.setItem(r,2,cell)
        self.watch.resizeRowsToContents()
        current=self.plot_key.currentText();self.plot_key.blockSignals(True);self.plot_key.clear();self.plot_key.addItems(aliases);self.plot_key.setCurrentText(current if current in aliases else (aliases[0] if aliases else ''));self.plot_key.blockSignals(False)
        current2=self.cycle_key.currentText();self.cycle_key.clear();self.cycle_key.addItems(aliases);self.cycle_key.setCurrentText(current2 if current2 in aliases else (aliases[0] if aliases else ''))
        self.plot.set_data(self.trend.samples,self.plot_key.currentText())
        for e in self.timeline.events[before:]:
            r=self.events.rowCount();self.events.insertRow(r)
            for c,v in enumerate([e.iso_time,e.signal,e.old,e.new]):self.events.setItem(r,c,item(v))
        if self.recording.received_samples>=self.recording.sample_limit:self.pause_sampling('Sample limit reached',end_cycle=True);self.owner.statusBar().showMessage('Recording sample limit reached. Export it and choose New recording to continue.')
    def export_trend(self):
        p,_=QFileDialog.getSaveFileName(self,'Export trend','fanuc_trend.csv','CSV (*.csv)');
        if p:self.trend.export_csv(Path(p))
    def export_timeline(self):
        p,_=QFileDialog.getSaveFileName(self,'Export timeline','fanuc_timeline.csv','CSV (*.csv)');
        if p:self.timeline.export_csv(Path(p))
    def clear_timeline(self):self.timeline=TimelineRecorder();self.events.setRowCount(0)
    def start_cycle(self):
        if self.cycle_recording:self.owner.statusBar().showMessage('Cycle recording is already running. Stop / Profile it before starting another.');return
        try:self.recording.ensure_context(self.current_source(),self.specs())
        except ValueError as exc:self.owner.statusBar().showMessage(str(exc));return
        self.cycle=CycleRecorder();self.cycle_recording=True;self.cycle.mark('START','manual start');self.profile.setRowCount(0);self.refresh_recording_label()
    def mark_event(self):
        try:self.recording.ensure_context(self.current_source(),self.specs())
        except ValueError as exc:self.owner.statusBar().showMessage(str(exc));return
        text=self.cycle_key.currentText() or 'MARK';self.cycle.mark(text,'manual mark')
        self.refresh_recording_label()
    def stop_cycle(self):
        if self.cycle_recording:self.cycle.mark('END','manual stop');self.cycle_recording=False
        self.render_profile()
    def render_profile(self):
        prof=profile_cycle(self.cycle.events);total=sum(prof.values()) or 1;self.profile.setRowCount(0)
        for k,v in prof.items():
            r=self.profile.rowCount();self.profile.insertRow(r)
            for c,x in enumerate([k,f'{v:.3f}',f'{100*v/total:.1f}','']):self.profile.setItem(r,c,item(x))
    def export_cycle(self):
        p,_=QFileDialog.getSaveFileName(self,'Export cycle','fanuc_cycle.json','JSON (*.json)');
        if p:self.cycle.export_json(Path(p))
    def compare_cycles(self):
        a,_=QFileDialog.getOpenFileName(self,'Cycle A','','JSON (*.json)');
        if not a:return
        b,_=QFileDialog.getOpenFileName(self,'Cycle B','','JSON (*.json)');
        if not b:return
        def load(p):return [CycleEvent(**x) for x in json.loads(Path(p).read_text(encoding='utf-8'))]
        rows=compare_cycle_profiles(profile_cycle(load(a)),profile_cycle(load(b)));self.cycle_compare.setPlainText('\n'.join(f'{k:24} {x:9.3f} -> {y:9.3f}  Δ {d:+9.3f}s' for k,x,y,d in rows))
    def capture(self):
        if not self.owner.workspace:return
        d=QFileDialog.getExistingDirectory(self,'Snapshot destination',str(self.owner.workspace.parent));
        if d:self.snaplog.appendPlainText(str(capture_snapshot(self.owner.workspace,Path(d),self.last_sample if self.matching_recording() else None)))
    def compare_snap(self):
        a,_=QFileDialog.getOpenFileName(self,'Snapshot A','','JSON (*.json)');
        if not a:return
        b,_=QFileDialog.getOpenFileName(self,'Snapshot B','','JSON (*.json)');
        if not b:return
        rows=compare_snapshots(Path(a),Path(b));self.snaplog.setPlainText('\n'.join(f'{k}:\n  A={x}\n  B={y}' for k,x,y in rows) or 'No supported snapshot differences.')
    def run_alarm_intel(self):
        if not self.owner.workspace:return
        rows=alarm_intelligence(self.owner.workspace,self.timeline_for_current_source());self.alarmtbl.setRowCount(0)
        for x in rows:
            r=self.alarmtbl.rowCount();self.alarmtbl.insertRow(r);vals=[x['code'],x['count'],x['message'],', '.join(f'{k} ({n})' for k,n in x['precursors'])]
            for c,v in enumerate(vals):self.alarmtbl.setItem(r,c,item(v))

class FleetTab(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.running=False;self.timer=QTimer(self);self.timer.setInterval(60000);self.timer.timeout.connect(self.run_due)
        l=QVBoxLayout(self);row=QHBoxLayout();self.dest=QLineEdit(str(Path.home()/'FanucFleetBackups'));row.addWidget(QLabel('Backup root'));row.addWidget(self.dest,1);b=QPushButton('Browse…');b.clicked.connect(lambda:browse_dir(self.dest,'Fleet backup root'));row.addWidget(b);b=QPushButton('Load connection profiles');b.clicked.connect(self.load_profiles);row.addWidget(b);l.addLayout(row)
        self.table=QTableWidget(0,8);self.table.setHorizontalHeaderLabels(['Run','Robot','Host','Transport','Interval min','Last backup','Changed','Status']);self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.Stretch);self.table.horizontalHeader().setSectionResizeMode(7,QHeaderView.Stretch);l.addWidget(self.table,1)
        row=QHBoxLayout();b=QPushButton('Back up checked now');b.clicked.connect(self.backup_checked);row.addWidget(b);self.schedule=QCheckBox('Enable in-app scheduled backups');self.schedule.toggled.connect(lambda on:self.timer.start() if on else self.timer.stop());row.addWidget(self.schedule);row.addStretch();l.addLayout(row);self.log=QPlainTextEdit();self.log.setReadOnly(True);l.addWidget(self.log,1);self.load_profiles()
    def sync_workspace(self):pass
    def manager(self):return FleetManager(Path(self.dest.text())/'fleet.json')
    def load_profiles(self):
        if self.running:
            self._reload_pending=True
            return
        previous={self.table.item(r,0).data(Qt.UserRole):
            (self.table.item(r,0).checkState(),[self.table.item(r,c).text() for c in range(4,8)])
            for r in range(self.table.rowCount())}
        first_load=not getattr(self,'_profiles_loaded',False)
        self.table.setRowCount(0)
        for p in self.owner.profiles:
            signature=profile_signature(p)
            state=previous.get(signature,(Qt.Checked if first_load else Qt.Unchecked,[1440,'','','']))
            r=self.table.rowCount();self.table.insertRow(r);chk=QTableWidgetItem();chk.setFlags(chk.flags()|Qt.ItemIsUserCheckable);chk.setCheckState(state[0]);chk.setData(Qt.UserRole,signature);self.table.setItem(r,0,chk)
            for c,v in enumerate([p.name,p.host,p.transport,*state[1]],start=1):self.table.setItem(r,c,item(v,editable=(c==4)))
        self._profiles_loaded=True
    def _rows(self,checked_only=True):
        out=[]
        by={profile_signature(p):p for p in self.owner.profiles}
        for r in range(self.table.rowCount()):
            enabled=self.table.item(r,0).checkState()==Qt.Checked
            if checked_only and not enabled:continue
            p=by.get(self.table.item(r,0).data(Qt.UserRole))
            if p:
                try:
                    interval=int(self.table.item(r,4).text() or 1440)
                    if not 1<=interval<=525600:raise ValueError()
                except ValueError:
                    self.table.setItem(r,7,item('Interval must be 1–525600 minutes'));continue
                out.append((r,FleetRobot(copy_profile(p),enabled,interval,self.table.item(r,5).text(),'')))
            else:self.table.setItem(r,7,item('Connection changed; reload profiles'))
        return out
    def backup_checked(self):self._run(self._rows(True))
    def run_due(self):
        mgr=self.manager();rows=[x for x in self._rows(False) if mgr.due(x[1])];self._run(rows)
    def _run(self,rows):
        if self.running or not rows:return
        current=self._rows(False)
        requested={idx:profile_signature(fr.profile) for idx,fr in rows}
        jobs=[(idx,fr) for idx,fr in current if requested.get(idx)==profile_signature(fr.profile)]
        if not jobs:return
        try:
            dest=Path(self.dest.text());dest.mkdir(parents=True,exist_ok=True)
            mgr=self.manager();mgr.robots=[fr for _,fr in current];mgr.save()
        except (OSError,ValueError) as exc:
            self.log.appendPlainText('Fleet backup could not start: '+str(exc));return
        self.running=True
        def work(*,progress):return [(idx,mgr.backup_one(fr,dest,progress=progress)) for idx,fr in jobs]
        wk=Worker(work);wk.signals.progress.connect(lambda x:self.log.appendPlainText(x.get('message',str(x)) if isinstance(x,dict) else str(x)));wk.signals.result.connect(self._done);wk.signals.error.connect(lambda e:self.log.appendPlainText(e));wk.signals.finished.connect(self._finished);self.owner.thread_pool.start(wk)
    def _finished(self):
        self.running=False
        if getattr(self,'_reload_pending',False):
            self._reload_pending=False;self.load_profiles()
    def _done(self,rows):
        for r,res in rows:
            self.table.setItem(r,5,item(res.backup));self.table.setItem(r,6,item(res.changed_files));self.table.setItem(r,7,item(res.status+(' — CHANGED' if res.changed_files else '')));self.log.appendPlainText(f'{res.robot}: {res.status}, changed files={res.changed_files} {res.message}')

class CommissioningTab(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.items=[];l=QVBoxLayout(self);sub=QTabWidget();l.addWidget(sub);sub.addTab(self._baseline(),'Baseline / Checklist');sub.addTab(self._lint(),'Robot Lint');sub.addTab(self._options(),'Option Inventory')
    def _baseline(self):
        w=QWidget();l=QVBoxLayout(w);row=QHBoxLayout();self.base_dir=QLineEdit(str(Path.home()/'FanucBaselines'));row.addWidget(QLabel('Baseline folder'));row.addWidget(self.base_dir,1);b=QPushButton('Browse…');b.clicked.connect(lambda:browse_dir(self.base_dir,'Baseline folder'));row.addWidget(b);b=QPushButton('Create commissioned baseline');b.clicked.connect(self.create_base);row.addWidget(b);b=QPushButton('Compare active workspace');b.clicked.connect(self.compare_base);row.addWidget(b);l.addLayout(row);self.baseline_log=QPlainTextEdit();self.baseline_log.setReadOnly(True);self.baseline_log.setMaximumHeight(130);l.addWidget(self.baseline_log)
        self.check=QTableWidget(0,3);self.check.setHorizontalHeaderLabels(['Done','Commissioning check','Note']);self.check.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.check.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);l.addWidget(self.check);b=QPushButton('Save checklist');b.clicked.connect(self.save_checks);l.addWidget(b);return w
    def _lint(self):
        w=QWidget();l=QVBoxLayout(w);b=QPushButton('Run engineering quality checks');b.clicked.connect(self.run_lint);l.addWidget(b);self.lint=QTableWidget(0,5);self.lint.setHorizontalHeaderLabels(['Severity','Check','File','Line','Message']);self.lint.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch);l.addWidget(self.lint);return w
    def _options(self):
        w=QWidget();l=QVBoxLayout(w);n=QLabel('Inventory is evidence-based: “Not determined” means the readable backup did not prove installation or absence.');n.setWordWrap(True);l.addWidget(n);b=QPushButton('Scan controller option evidence');b.clicked.connect(self.scan_options);l.addWidget(b);self.options=QTableWidget(0,4);self.options.setHorizontalHeaderLabels(['Option','Status','Source','Evidence']);self.options.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch);l.addWidget(self.options);return w
    def sync_workspace(self):
        self.items=[]
        if self.owner.workspace:self.items=load_checklist(self.owner.workspace/'commissioning_checklist.json')
        self.check.setRowCount(0)
        for x in self.items:
            r=self.check.rowCount();self.check.insertRow(r);c=QTableWidgetItem();c.setFlags(c.flags()|Qt.ItemIsUserCheckable);c.setCheckState(Qt.Checked if x.done else Qt.Unchecked);self.check.setItem(r,0,c);self.check.setItem(r,1,item(x.name));self.check.setItem(r,2,item(x.note,True))
    def create_base(self):
        if not self.owner.workspace:return
        d=Path(self.base_dir.text());d.mkdir(parents=True,exist_ok=True);out=create_baseline(self.owner.workspace,d,'commissioned');self.baseline_log.setPlainText(f'Created protected copy: {out}')
    def compare_base(self):
        if not self.owner.workspace:return
        b=Path(self.base_dir.text())/'commissioned'
        if not b.is_dir():return
        ch=compare_baseline(b,self.owner.workspace);self.baseline_log.setPlainText('\n'.join(f'{x.status}: {x.relative_path}' for x in ch) or 'No differences from baseline.')
    def save_checks(self):
        if not self.owner.workspace:return
        rows=[]
        for r in range(self.check.rowCount()):rows.append(ChecklistItem(self.check.item(r,1).text(),self.check.item(r,0).checkState()==Qt.Checked,self.check.item(r,2).text() if self.check.item(r,2) else ''))
        save_checklist(self.owner.workspace/'commissioning_checklist.json',rows);self.items=rows
    def run_lint(self):
        if not self.owner.workspace:return
        rows=lint_workspace(self.owner.workspace);self.lint.setRowCount(0)
        for x in rows:
            r=self.lint.rowCount();self.lint.insertRow(r)
            for c,v in enumerate([x.severity,x.code,Path(x.file).name,x.line,x.message]):self.lint.setItem(r,c,item(v))
    def scan_options(self):
        if not self.owner.workspace:return
        rows=option_inventory(self.owner.workspace);self.options.setRowCount(0)
        for x in rows:
            r=self.options.rowCount();self.options.insertRow(r)
            for c,v in enumerate([x.option,x.status,Path(x.source).name if x.source else '',x.evidence]):self.options.setItem(r,c,item(v))

class IntegrationTab(QWidget):
    def __init__(self,owner):super().__init__();self.owner=owner;l=QVBoxLayout(self);sub=QTabWidget();l.addWidget(sub);sub.addTab(self._eip(),'EtherNet/IP');sub.addTab(self._handshake(),'PLC Handshake');sub.addTab(self._transform(),'Frame Transform')
    def _eip(self):
        w=QWidget();l=QVBoxLayout(w);n=QLabel('Reads evidence from ASCII/diagnostic backup files; it does not change EtherNet/IP configuration.');n.setWordWrap(True);l.addWidget(n);b=QPushButton('Scan EtherNet/IP configuration evidence');b.clicked.connect(self.scan_eip);l.addWidget(b);self.eip=QTableWidget(0,9);self.eip.setHorizontalHeaderLabels(['Role','Conn','IP','Slot','Input size','Output size','RPI','Status','Source/raw']);self.eip.horizontalHeader().setSectionResizeMode(8,QHeaderView.Stretch);l.addWidget(self.eip);return w
    def _handshake(self):
        w=QWidget();l=QVBoxLayout(w);self.hs=QTableWidget(8,5);self.hs.setHorizontalHeaderLabels(['Name','Robot signal','PLC tag','Direction','Expected peer signal']);self.hs.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.hs.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);l.addWidget(self.hs);row=QHBoxLayout();b=QPushButton('Save map');b.clicked.connect(self.save_hs);row.addWidget(b);b=QPushButton('Analyze captured live timeline');b.clicked.connect(self.analyze_hs);row.addWidget(b);row.addStretch();l.addLayout(row);self.hs_result=QPlainTextEdit();self.hs_result.setReadOnly(True);l.addWidget(self.hs_result);return w
    def _transform(self):
        w=QWidget();l=QVBoxLayout(w);n=QLabel('Convert a Cartesian pose from one user-frame coordinate system to another while preserving the physical pose. W/P/R convention is Rz(R)·Ry(P)·Rx(W). Verify the result on a virtual controller/teach pendant before production use.');n.setWordWrap(True);l.addWidget(n);f=QFormLayout();self.old=QLineEdit('0,0,0,0,0,0');self.new=QLineEdit('0,0,0,0,0,0');self.pose=QLineEdit('0,0,0,0,0,0');self.out=QLineEdit();self.out.setReadOnly(True);f.addRow('Old frame X,Y,Z,W,P,R',self.old);f.addRow('New frame X,Y,Z,W,P,R',self.new);f.addRow('Pose in old frame',self.pose);f.addRow('Pose in new frame',self.out);l.addLayout(f);b=QPushButton('Transform');b.clicked.connect(self.do_transform);l.addWidget(b);return w
    def sync_workspace(self):
        if self.owner.workspace:
            p=self.owner.workspace/'plc_handshake.json';rows=load_handshake(p);self.hs.setRowCount(max(8,len(rows)))
            for r,x in enumerate(rows):
                for c,v in enumerate([x.name,x.robot_signal,x.plc_tag,x.direction,x.expected_peer]):self.hs.setItem(r,c,item(v,True))
    def scan_eip(self):
        if not self.owner.workspace:return
        rows=scan_ethernetip(self.owner.workspace);self.eip.setRowCount(0)
        for x in rows:
            r=self.eip.rowCount();self.eip.insertRow(r);vals=[x.role,x.connection,x.ip,x.slot,x.input_size,x.output_size,x.rpi,x.status,Path(x.source).name+' — '+x.raw]
            for c,v in enumerate(vals):self.eip.setItem(r,c,item(v))
    def save_hs(self):
        if not self.owner.workspace:return
        rows=[]
        for r in range(self.hs.rowCount()):
            vals=[self.hs.item(r,c).text().strip() if self.hs.item(r,c) else '' for c in range(5)]
            if vals[0] or vals[1]:rows.append(HandshakeSignal(*vals))
        save_handshake(self.owner.workspace/'plc_handshake.json',rows)
    def analyze_hs(self):
        live=getattr(self.owner,'live_tab',None)
        if not live:return
        rows=[]
        for r in range(self.hs.rowCount()):
            vals=[self.hs.item(r,c).text().strip() if self.hs.item(r,c) else '' for c in range(5)]
            if vals[1]:rows.append(HandshakeSignal(*vals))
        res=analyze_handshake(live.timeline_for_current_source(),rows);self.hs_result.setPlainText('\n'.join(f'{name}: {status} at {t} — {detail}' for name,t,status,detail in res) or 'No mapped handshake transitions found in the captured timeline.')
    def do_transform(self):
        try:
            parse=lambda e:[float(x.strip()) for x in e.text().split(',')]
            old,new,pose=parse(self.old),parse(self.new),parse(self.pose)
            if any(len(x)!=6 for x in (old,new,pose)):raise ValueError('Each pose must have six comma-separated numbers')
            out=transform_pose_between_frames(pose,old,new);self.out.setText(', '.join(f'{x:.3f}' for x in out))
        except Exception as e:QMessageBox.warning(self,'Transform error',str(e))

class RoboGuideTab(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;l=QVBoxLayout(self);n=QLabel('ROBOGUIDE integration uses a documented-safe handoff: stage a backup for a virtual controller and launch ROBOGUIDE. FanucMCPServer deliberately does not automate unverified private ROBOGUIDE UI/COM calls.');n.setWordWrap(True);l.addWidget(n);f=QFormLayout();self.exe=QLineEdit(str(discover_roboguide() or ''));row=QHBoxLayout();row.addWidget(self.exe);b=QPushButton('Browse…');b.clicked.connect(lambda:browse_file(self.exe,'ROBOGUIDE executable','Executable (*.exe)'));row.addWidget(b);rw=QWidget();rw.setLayout(row);f.addRow('ROBOGUIDE executable',rw);self.cell=QLineEdit();row=QHBoxLayout();row.addWidget(self.cell);b=QPushButton('Browse workcell…');b.clicked.connect(lambda:browse_file(self.cell,'ROBOGUIDE workcell'));row.addWidget(b);rw=QWidget();rw.setLayout(row);f.addRow('Optional workcell',rw);l.addLayout(f);row=QHBoxLayout();b=QPushButton('Stage active backup for ROBOGUIDE…');b.clicked.connect(self.stage);row.addWidget(b);b=QPushButton('Launch ROBOGUIDE');b.clicked.connect(self.launch);row.addWidget(b);row.addStretch();l.addLayout(row);self.log=QPlainTextEdit();self.log.setReadOnly(True);l.addWidget(self.log)
    def sync_workspace(self):pass
    def stage(self):
        if not self.owner.workspace:return
        d=QFileDialog.getExistingDirectory(self,'Staging destination',str(self.owner.workspace.parent));
        if d:self.log.appendPlainText('Staged: '+str(stage_backup_for_roboguide(self.owner.workspace,Path(d))))
    def launch(self):
        try:launch_roboguide(Path(self.exe.text()) if self.exe.text() else None,Path(self.cell.text()) if self.cell.text() else None);self.log.appendPlainText('ROBOGUIDE launched.')
        except Exception as e:QMessageBox.critical(self,'ROBOGUIDE launch failed',str(e))
