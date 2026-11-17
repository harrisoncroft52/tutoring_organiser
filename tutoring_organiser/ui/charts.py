"""The overview line graph and the full-size graph dialog."""
import math
from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget

from .theme import ACCENT, BORDER, GOOD, MUTED, SURFACE, TEXT


try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QDateTimeAxis, QValueAxis
    HAVE_CHARTS = True
except ImportError:          # pip install PySide6-Addons
    HAVE_CHARTS = False


class OverviewLineGraph(QWidget):
    """Small theme-matched line graph, drawn without optional chart packages.
    Hover anywhere over the plot to see the nearest point's period (x) and value (y)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []            # (axis label, value[, hover label])
        self.money_axis = False
        self.hover = None           # index of the highlighted point
        self._points = []
        self.setMinimumHeight(170)
        self.setMouseTracking(True)

    def set_values(self, values):
        self.values = list(values)
        self.hover = None
        self.update()

    # ---- hover
    def hover_text(self, index):
        """(x text, y text) for one point, also used by the self-test."""
        item = self.values[index]
        x_text = item[2] if len(item) > 2 and item[2] else item[0]
        value = item[1]
        if self.money_axis:
            y_text = f"${value:,.2f}"
        else:
            y_text = f"{value:g} lesson" + ("" if value == 1 else "s")
        return x_text, y_text

    def mouseMoveEvent(self, event):
        index = None
        if self._points:
            x = event.position().x()
            index = min(range(len(self._points)), key=lambda i: abs(self._points[i].x() - x))
            if abs(self._points[index].x() - x) > max(40.0, self.width() / max(1, len(self._points))):
                index = None
        if index != self.hover:
            self.hover = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hover is not None:
            self.hover = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(44, 10, -14, -34)
        self._points = []
        if not self.values:
            p.setPen(QColor(MUTED))
            p.drawText(rect, Qt.AlignCenter, "No completed lesson data for this range yet")
            p.end()
            return
        max_value = max(1.0, max(item[1] for item in self.values))
        p.setFont(QFont(p.font().family(), 9))
        for tick in range(4):
            y = rect.bottom() - rect.height() * tick / 3
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QColor(MUTED))
            value = max_value * tick / 3
            p.drawText(QRectF(0, y - 9, 38, 18), Qt.AlignRight | Qt.AlignVCenter,
                       f"${value:,.0f}" if self.money_axis else f"{value:.0f}")
        count = len(self.values)
        points = []
        for i, item in enumerate(self.values):
            # A single point sits on the right, with a flat line leading to it.
            x = rect.left() + (rect.width() * i / (count - 1) if count > 1 else rect.width())
            y = rect.bottom() - rect.height() * item[1] / max_value
            points.append(QPointF(x, y))
        self._points = points
        line = [QPointF(rect.left(), points[0].y()), points[0]] if count == 1 else points
        path = QPainterPath(line[0])
        for point in line[1:]:
            path.lineTo(point)
        p.setPen(QPen(QColor(ACCENT), 2.5))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        for point in points:
            p.drawEllipse(point, 4, 4)
        stride = max(1, math.ceil(count / 8))
        p.setPen(QColor(MUTED))
        for i, item in enumerate(self.values):
            if i % stride == 0 or i == count - 1:
                x = min(max(points[i].x() - 34, 0.0), self.width() - 68.0)   # keep end labels inside the card
                p.drawText(QRectF(x, rect.bottom() + 6, 68, 20), Qt.AlignHCenter, item[0])
        if self.hover is not None and self.hover < count:
            self._paint_hover(p, rect, points[self.hover], self.hover)
        p.end()

    def _paint_hover(self, p, rect, point, index):
        guide = QPen(QColor(MUTED), 1, Qt.DashLine)
        p.setPen(guide)
        p.drawLine(QPointF(point.x(), rect.top()), QPointF(point.x(), rect.bottom()))
        p.setPen(QPen(QColor(SURFACE), 2))
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(point, 6, 6)
        x_text, y_text = self.hover_text(index)
        bold = QFont(p.font())
        bold.setBold(True)
        fm_bold, fm = QFontMetrics(bold), QFontMetrics(p.font())
        w = max(fm_bold.horizontalAdvance(y_text), fm.horizontalAdvance(x_text)) + 20
        h = fm_bold.height() + fm.height() + 14
        bx = point.x() + 12 if point.x() + 12 + w <= self.width() - 4 else point.x() - 12 - w
        by = min(max(4.0, point.y() - h - 8), self.height() - h - 4)
        box = QRectF(max(4.0, bx), by, w, h)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(SURFACE))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor(TEXT))
        p.setFont(bold)
        p.drawText(box.adjusted(10, 6, -10, 0), Qt.AlignLeft | Qt.AlignTop, y_text)
        p.setFont(QFont(bold.family(), bold.pointSize()))
        p.setPen(QColor(MUTED))
        p.drawText(box.adjusted(10, 6 + fm_bold.height() + 2, -10, 0), Qt.AlignLeft | Qt.AlignTop, x_text)


def _ms(dt):
    return QDateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, 0).toMSecsSinceEpoch()


def step_points(events, xmin, xmax):
    """Running total as a staircase: flat between events, a vertical step on each event date."""
    pts, total = [(xmin, 0.0)], 0.0
    for d, v in events:
        pts.append((d, total))
        total += v
        pts.append((d, total))
    pts.append((xmax, total))
    return pts


class GraphDialog(QDialog):
    """Lesson cost vs payments over time, as two line graphs."""
    def __init__(self, parent, name, lessons, payments, summary):
        super().__init__(parent)
        self.setWindowTitle(f"{name}: lesson cost vs payments")
        self.resize(960, 620)
        self.lessons, self.payments = lessons, payments

        title = QLabel(f"{name}: lesson cost vs payments")
        title.setObjectName("title")
        self.mode = QComboBox()
        self.mode.addItem("Running total", "total")
        self.mode.addItem("Each lesson and payment", "each")
        self.mode.currentIndexChanged.connect(self.draw)
        head = QHBoxLayout()
        head.addWidget(title, 1)
        head.addWidget(self.mode)

        self.chart = QChart()
        self.chart.setAnimationOptions(QChart.NoAnimation)
        self.chart.setBackgroundBrush(QBrush(QColor(SURFACE)))
        self.chart.setBackgroundRoundness(12)
        self.chart.setFont(QFont("Segoe UI", 9))
        self.chart.legend().setAlignment(Qt.AlignBottom)
        self.chart.legend().setLabelColor(QColor(TEXT))
        self.ax = QDateTimeAxis()
        self.ax.setFormat("d MMM yy")
        self.ax.setTickCount(7)
        self.ay = QValueAxis()
        self.ay.setLabelFormat("$%.0f")
        for axis in (self.ax, self.ay):
            axis.setLabelsColor(QColor(MUTED))
            axis.setLinePen(QPen(QColor(BORDER)))
            axis.setGridLinePen(QPen(QColor("#EEEBDF")))
        self.chart.addAxis(self.ax, Qt.AlignBottom)
        self.chart.addAxis(self.ay, Qt.AlignLeft)
        self.view = QChartView(self.chart)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setStyleSheet(f"QChartView {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}")

        notes = [summary,
                 "Lessons: completed lessons only (cancelled and upcoming ones are left out). "
                 "Payments: money received minus refunds; bonuses are not included."]
        if lessons and sum(v for _, v in lessons) == 0:
            notes.append("Lesson cost is $0 because this student's rate is $0. Press Edit on the student to set it.")
        note = QLabel("\n".join(notes))
        note.setObjectName("muted")
        note.setWordWrap(True)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(close)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(10)
        lay.addLayout(head)
        lay.addWidget(self.view, 1)
        lay.addWidget(note)
        lay.addLayout(foot)
        self.draw()

    def draw(self):
        running = self.mode.currentData() == "total"
        dates = [d for d, _ in self.lessons + self.payments]
        xmin, xmax = min(dates), max(max(dates), datetime.now())
        pad = max((xmax - xmin) * 0.03, timedelta(hours=12))
        xmin, xmax = xmin - pad, xmax + pad

        if running:
            sets = [("Lesson cost", ACCENT, step_points(self.lessons, xmin, xmax), self.lessons),
                    ("Payments", GOOD, step_points(self.payments, xmin, xmax), self.payments)]
        else:
            sets = [("Lesson cost", ACCENT, list(self.lessons), self.lessons),
                    ("Payments", GOOD, list(self.payments), self.payments)]

        self.chart.removeAllSeries()
        ys = [0.0]
        for label, colour, pts, events in sets:
            series = QLineSeries()
            series.hovered.connect(lambda point, state, l=label, ev=events, r=running:
                                   self.show_hover(l, ev, r, point.x(), state))
            series.setName(label)
            pen = QPen(QColor(colour))
            pen.setWidth(3)
            series.setPen(pen)
            if not running:
                series.setPointsVisible(True)
                series.setMarkerSize(9)
            for d, y in pts:
                series.append(float(_ms(d)), float(y))
                ys.append(y)
            self.chart.addSeries(series)
            series.attachAxis(self.ax)
            series.attachAxis(self.ay)
        self.ax.setRange(QDateTime.fromMSecsSinceEpoch(_ms(xmin)), QDateTime.fromMSecsSinceEpoch(_ms(xmax)))
        lo, hi = min(0.0, min(ys)), max(ys)
        raw = max(hi - lo, 1.0) / 5                      # aim for about 5 gridlines, on round numbers
        mag = 10 ** math.floor(math.log10(raw))
        step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
        lo, hi = math.floor(lo / step) * step, math.ceil(hi * 1.03 / step) * step
        if hi <= lo:
            hi = lo + step
        self.ay.setRange(lo, hi)
        self.ay.setTickCount(int(round((hi - lo) / step)) + 1)

    @staticmethod
    def hover_value(events, running, x_ms):
        """(date, value) under the cursor: the nearest lesson/payment, or for running totals the step the
        cursor is on (the last event at or before it, and the total after it)."""
        ordered = sorted(events, key=lambda e: e[0])
        if not ordered:
            return None
        if not running:
            return min(ordered, key=lambda e: abs(_ms(e[0]) - x_ms))
        total, current = 0.0, None
        for d, v in ordered:
            if _ms(d) > x_ms:
                break
            total += v
            current = (d, total)
        return current or (ordered[0][0], 0.0)

    def show_hover(self, label, events, running, x_ms, state):
        if not state:
            QToolTip.hideText()
            return
        hit = self.hover_value(events, running, x_ms)
        if hit is None:
            return
        when, value = hit
        title = f"{label} (running total)" if running else label
        QToolTip.showText(QCursor.pos(), f"{title}\n{when:%a %d %b %Y}\n${value:,.2f}", self.view)
