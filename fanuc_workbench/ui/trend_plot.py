"""Gap-aware plot shared by live collection and saved-recording review."""
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from fanuc_workbench.services.live_recording import numeric_segments


class TrendPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples, self.key, self.break_before = [], '', []
        self.setMinimumHeight(180)

    def set_data(self, samples, key, *, break_before=()):
        self.samples, self.key, self.break_before = samples, key, list(break_before)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setPen(QPen(self.palette().text().color()))
        painter.drawText(8, 18, self.key or 'Select a numeric watch signal')
        segments = numeric_segments(self.samples, self.key, break_before=self.break_before)
        values = [point for segment in segments for point in segment]
        if not values:
            return
        left, top, right, bottom = 45, 28, self.width() - 12, self.height() - 28
        t0, t1 = min(t for t, _ in values), max(t for t, _ in values)
        v0, v1 = min(v for _, v in values), max(v for _, v in values)
        if t1 == t0:
            t1 = t0 + 1
        painter.drawLine(left, bottom, right, bottom)
        painter.drawLine(left, top, left, bottom)
        painter.drawText(2, top + 5, f'{v1:.3g}')
        painter.drawText(2, bottom, f'{v0:.3g}')
        scale = max(abs(v0), abs(v1), 1)
        low, high = v0 / scale, v1 / scale
        for segment in segments:
            points = [QPointF(left + (t-t0)/(t1-t0)*(right-left),
                      bottom - ((v/scale-low)/(high-low) if high != low else .5)*(bottom-top))
                      for t, v in segment]
            if len(points) == 1:
                painter.drawPoint(points[0])
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)
