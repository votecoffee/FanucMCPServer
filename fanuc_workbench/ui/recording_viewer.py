"""Read-only desktop recording review, independent of live collection."""
from datetime import datetime, timezone
import html
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSpinBox, QStatusBar,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from fanuc_workbench.services.display import watch_value
from fanuc_workbench.ui.layout_state import DesktopLayout
from fanuc_workbench.ui.trend_plot import TrendPlot


def utc_time(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds')


def value_text(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


class RecordingRows(QWidget):
    """Render one page at a time, including for large event histories."""
    PAGE_SIZE = 200

    def __init__(self, headers, widths):
        super().__init__()
        self.row_count, self.row_values = 0, lambda index: []
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(headers))
        self.table.setObjectName('rows')
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.previous, self.next = QPushButton('Previous'), QPushButton('Next')
        self.page, self.range_label = QSpinBox(), QLabel()
        self.page.setRange(1, 1)
        self.page.valueChanged.connect(self.render)
        self.previous.clicked.connect(lambda: self.page.setValue(self.page.value()-1))
        self.next.clicked.connect(lambda: self.page.setValue(self.page.value()+1))
        controls.addWidget(self.previous)
        controls.addWidget(QLabel('Page'))
        controls.addWidget(self.page)
        controls.addWidget(self.next)
        controls.addWidget(self.range_label)
        controls.addStretch()
        layout.addLayout(controls)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self.table.resizeRowsToContents())

    def set_rows(self, count, row_values):
        self.row_count, self.row_values = count, row_values
        self.page.setMaximum(max(1, (count+self.PAGE_SIZE-1)//self.PAGE_SIZE))
        self.render()

    def render(self, *_):
        start = (self.page.value()-1)*self.PAGE_SIZE
        end = min(self.row_count, start+self.PAGE_SIZE)
        self.table.setRowCount(end-start)
        for index in range(start, end):
            for column, value in enumerate(self.row_values(index)):
                # Tuples carry the compact display and full captured/error detail.
                text, detail = value if isinstance(value, tuple) else (str(value), str(value))
                cell = QTableWidgetItem(text[:2048])
                suffix = '\n[Display truncated; full value remains in the original JSON.]' if len(detail)>16384 else ''
                cell.setToolTip('<pre>'+html.escape(detail[:16384]+suffix)+'</pre>')
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(index-start, column, cell)
        self.table.resizeRowsToContents()
        self.previous.setEnabled(self.page.value()>1)
        self.next.setEnabled(end<self.row_count)
        self.range_label.setText(f'Rows {start+1}–{end} of {self.row_count}' if self.row_count else 'No rows')


class RecordingViewer(QDialog):
    def __init__(self, recording, settings, parent=None):
        super().__init__(parent)
        self.settings, self.recording = settings, recording
        self.data, self.samples = recording.document(), recording.samples
        self.setWindowTitle('Saved recording — FanucMCPServer')
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        source = self.data['source'] or {}
        profile = source.get('profile') or {}
        source_name = (f"{profile.get('name', 'Controller')} ({profile.get('host', 'unspecified')})"
                       if source.get('kind') == 'controller_files' else source.get('path') or 'Workspace (unspecified)')
        count, received = len(self.samples), self.data['received_samples']
        self.summary = QLabel(f'{source_name[:512]}\n{count} retained samples / {received} received; '
                              f"created {self.data['created_at_utc']}")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.summary)
        note = QLabel('Offline review. Opening a file leaves live collection unchanged. '
                      'Times below are UTC. Source metadata and supplied events are unverified.')
        note.setWordWrap(True)
        layout.addWidget(note)
        signal_row = QHBoxLayout()
        signal_row.addWidget(QLabel('Sample / trend signal'))
        self.signal = QComboBox()
        self.signal.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.signal.setMinimumContentsLength(24)
        signals = {watch['alias'].strip() or watch['expression'].strip() for watch in self.data['watches']}
        for sample in self.samples:
            signals.update(sample.values)
            signals.update(sample.errors)
        self.signal.addItems(sorted(signals))
        signal_row.addWidget(self.signal, 1)
        layout.addLayout(signal_row)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.sample_rows = RecordingRows(['Sample # (0-based)', 'Time (UTC)', 'Value', 'Error'], [150, 250, 420, 300])
        self.tabs.addTab(self.sample_rows, 'Samples')
        trend = QWidget()
        trend_layout = QVBoxLayout(trend)
        self.plot = TrendPlot()
        trend_layout.addWidget(self.plot, 1)
        trend_layout.addWidget(QLabel('Lines break at missing/error values and recorded pauses; only numeric values are plotted.'))
        self.tabs.addTab(trend, 'Trend')
        self.timeline_rows = RecordingRows(['Time (UTC)', 'Signal', 'Old', 'New'], [250, 180, 300, 300])
        timeline = self.data['timeline']
        self.timeline_rows.set_rows(len(timeline), lambda i: [utc_time(timeline[i]['timestamp']), timeline[i]['signal'],
                                                            value_text(timeline[i]['old']), value_text(timeline[i]['new'])])
        self.tabs.addTab(self.timeline_rows, 'I/O Timeline')
        self.cycle_rows = RecordingRows(['Time (UTC)', 'State', 'Detail'], [250, 250, 450])
        cycle = self.data['cycle']
        self.cycle_rows.set_rows(len(cycle), lambda i: [utc_time(cycle[i]['timestamp']), cycle[i]['state'], cycle[i]['detail']])
        self.tabs.addTab(self.cycle_rows, 'Cycle events')
        self.pause_rows = RecordingRows(['Time (UTC)', 'After sample # (0-based)', 'Reason'], [250, 200, 450])
        pauses = self.data['pauses']
        self.pause_rows.set_rows(len(pauses), lambda i: [utc_time(pauses[i]['timestamp']), pauses[i]['after_sample'], pauses[i]['reason']])
        self.tabs.addTab(self.pause_rows, 'Pauses')
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        metadata = {key: value for key, value in self.data.items() if key not in ('samples', 'timeline', 'cycle', 'pauses')}
        metadata.update(selected_file=str(recording.path), file_bytes=recording.byte_count, file_sha256=recording.sha256)
        self.details.setPlainText(json.dumps(metadata, indent=2, ensure_ascii=False))
        self.tabs.addTab(self.details, 'Source / watches / hash')
        self._status = QStatusBar()
        if self.data['cycle_recording']:
            self._status.showMessage('Cycle was open when exported. This viewer does not resume recording.')
        layout.addWidget(self._status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.signal.currentTextChanged.connect(self.show_signal)
        self.show_signal()
        for index, name in enumerate(('samples', 'trend', 'timeline', 'cycle', 'pauses', 'metadata')):
            self.tabs.widget(index).setObjectName(name)
        self.desktop_layout = DesktopLayout(self, preference_key='recording_viewer_layout')
        self.finished.connect(self.desktop_layout.save)

    def statusBar(self):
        return self._status

    def show_signal(self, *_):
        key = self.signal.currentText()
        first = self.data['first_retained_sample']
        def row(index):
            sample = self.samples[index]
            return [first+index, utc_time(sample.timestamp),
                    watch_value(sample.values.get(key), sample.errors.get(key)), sample.errors.get(key, '')]
        self.sample_rows.set_rows(len(self.samples), row)
        self.plot.set_data(self.samples, key, break_before=self.recording.trend_breaks)
