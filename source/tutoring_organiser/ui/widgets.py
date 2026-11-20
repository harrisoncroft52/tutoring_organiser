"""Reusable widgets: tables, the draggable splitter, toast messages, flash highlight, reflowing layouts."""
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QMimeData, QObject, QRectF, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMenuBar,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, BAD, GOOD, SURFACE


class FileDragTable(QTableWidget):
    """Drag PDF file(s) out of the table into another app as file URLs.
    Default: the PDF cell of the current row. whole_row=True: any cell, and several rows at once."""
    def __init__(self, rows, columns, pdf_column, whole_row=False):
        super().__init__(rows, columns)
        self.pdf_column = pdf_column
        self.whole_row = whole_row
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)

    def drag_paths(self):
        if self.whole_row:
            rows = sorted({i.row() for i in self.selectedIndexes()})
        else:
            row = self.currentRow()
            rows = [row] if row >= 0 and self.currentColumn() == self.pdf_column else []
        out = []
        for r in rows:
            item = self.item(r, self.pdf_column)
            path = item.data(Qt.UserRole + 1) if item else None
            if path and Path(path).is_file():
                out.append(str(Path(path).resolve()))
        return out

    def startDrag(self, supported_actions):
        paths = self.drag_paths()
        if not paths:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class LessonDropTable(FileDragTable):
    files_dropped = Signal(object, int)

    def __init__(self, rows, columns, pdf_column):
        super().__init__(rows, columns, pdf_column)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf")]
        row = self.rowAt(event.position().toPoint().y())
        item = self.item(row, 0) if row >= 0 else None
        lesson_id = item.data(Qt.UserRole) if item else None
        if paths and lesson_id:
            self.files_dropped.emit(paths, lesson_id)
            event.acceptProposedAction()
        else:
            event.ignore()


def make_table(headers, stretch_col=0, pdf_drag_column=None, whole_row_drag=False):
    t = (FileDragTable(0, len(headers), pdf_drag_column, whole_row_drag) if pdf_drag_column is not None
         else QTableWidget(0, len(headers)))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection if whole_row_drag else QAbstractItemView.SingleSelection)
    t.setFocusPolicy(Qt.StrongFocus)
    t.verticalHeader().setDefaultSectionSize(38)
    h = t.horizontalHeader()
    h.setHighlightSections(False)
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return t


class _ResetHandle(QSplitterHandle):
    def mouseDoubleClickEvent(self, e):            # double-click the divider to restore the default split
        self.splitter().reset_sizes()
        super().mouseDoubleClickEvent(e)


class SmartSplitter(QSplitter):
    """A two-pane splitter that behaves: panes never snap shut, each has a real minimum size (so dragging is
    smooth right up to the limit), the divider is easy to grab, and double-clicking it resets the split."""
    def __init__(self, first, second, sizes, min_sizes, stretch=(1, 1), parent=None):
        super().__init__(parent)
        self._default_sizes = list(sizes)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(10)
        self.setOpaqueResize(True)
        for pane, minimum in zip((first, second), min_sizes):
            # An explicit minimum replaces the contents' own minimumSizeHint, which is what used to make the
            # divider jump between "stuck" and "fully closed".
            pane.setMinimumWidth(minimum)
            pane.setMinimumHeight(min(minimum, 220))
            self.addWidget(pane)
        for i, factor in enumerate(stretch):
            self.setStretchFactor(i, factor)
        self.setSizes(self._default_sizes)

    def createHandle(self):
        return _ResetHandle(self.orientation(), self)

    def reset_sizes(self):
        self.setSizes(self._default_sizes)


FLASH_ROLE = Qt.UserRole + 20


class FlashDelegate(QStyledItemDelegate):
    """Paints a translucent highlight over cells flagged with FLASH_ROLE (used to show where a PDF landed)."""
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.data(FLASH_ROLE):
            painter.save()
            painter.fillRect(option.rect, QColor(255, 190, 60, 120))
            painter.restore()


def flash_table_rows(table, rows, ms=2800):
    """Highlight whole rows of a table for a moment, then clear it."""
    items = []
    for r in rows:
        for c in range(table.columnCount()):
            it = table.item(r, c)
            if it is not None:
                it.setData(FLASH_ROLE, True)
                items.append(it)
    table.viewport().update()

    def clear():
        for it in items:
            try:
                it.setData(FLASH_ROLE, None)
            except RuntimeError:               # the table was rebuilt in the meantime
                pass
        try:
            table.viewport().update()
        except RuntimeError:
            pass
    QTimer.singleShot(ms, clear)


class Toast(QLabel):
    """Small non-blocking message that fades away on its own, shown near the bottom of the window."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setContentsMargins(14, 8, 14, 8)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text, kind="ok", ms=None):
        colour = {"ok": GOOD, "bad": BAD}.get(kind, ACCENT)
        self.setStyleSheet(f"QLabel {{ background: {SURFACE}; border: 1px solid {colour}; "
                           f"border-left: 5px solid {colour}; border-radius: 10px; }}")
        self.setText(text)
        self.place()
        self.show()
        self.raise_()
        self._timer.start(ms or (8000 if kind == "bad" else 5000))

    def place(self):
        parent = self.parentWidget()
        width = max(240, min(580, parent.width() - 48))
        self.setFixedWidth(width)
        self.setFixedHeight(self.heightForWidth(width) + 8)
        self.move((parent.width() - width) // 2, parent.height() - self.height() - 28)


class ResizeHook(QObject):
    """Calls callback (once the event loop is free) whenever the watched widget is resized."""
    def __init__(self, widget, callback):
        super().__init__(widget)
        self._cb = callback
        widget.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            QTimer.singleShot(0, self._cb)
        return False


class ElidedLabel(QLabel):
    """One-line label that shows '…' instead of forcing the window wider; full text in the tooltip."""
    def __init__(self, *a):
        super().__init__(*a)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def set_full_text(self, text):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, self.width() - 2)))


def cell(text, color=None, bold=False, data=None):
    it = QTableWidgetItem(text)
    if color:
        it.setForeground(QColor(color))
    if bold:
        f = it.font()
        f.setWeight(QFont.DemiBold)
        it.setFont(f)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


class NoTextMenu(QObject):
    """App-wide filter: no Undo/Redo/Cut/Copy/Paste/Select All pop-up on text fields."""
    TEXT = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.ContextMenu and isinstance(obj, QWidget):
            if isinstance(obj, self.TEXT) or isinstance(obj.parent(), (QTextEdit, QPlainTextEdit)):
                return True
        return False


class ReflowContainer(QWidget):
    """Lays widgets out in as many equal columns as fit, wrapping onto new rows as the window narrows.
    Rows are balanced (5 items in 3 columns -> 3 + 2, never 4 + 1). Pure grid re-placement on resize,
    so it needs no height-for-width support from parent layouts."""
    def __init__(self, items, min_item_width=130, spacing=10, left_align=False):
        super().__init__()
        self._items, self._min_w, self._cols = list(items), min_item_width, 0
        self._left = left_align
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self._grid.setSizeConstraint(QLayout.SetNoConstraint)   # width is decided by the window, not by the cards
        for w in self._items:
            w.setMinimumWidth(min(min_item_width, w.sizeHint().width()) if left_align else min_item_width)
        self.setMinimumWidth(min_item_width)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._place(len(self._items) or 1)

    def _place(self, cols):
        cols = max(1, cols)
        if cols == self._cols:
            return
        self._cols = cols
        for w in self._items:
            self._grid.removeWidget(w)
        for c in range(self._grid.columnCount() + 1):
            self._grid.setColumnStretch(c, 0)
        for i, w in enumerate(self._items):
            self._grid.addWidget(w, i // cols, i % cols)
        for c in range(cols):
            self._grid.setColumnStretch(c, 0 if self._left else 1)
        if self._left:
            self._grid.setColumnStretch(cols, 1)             # spare width goes to an empty trailing column
        self.updateGeometry()

    def resizeEvent(self, e):
        n = len(self._items)
        if n:
            gap = self._grid.horizontalSpacing()
            per_row = max(1, min(n, (self.width() + gap) // (self._min_w + gap)))
            rows = -(-n // per_row)
            self._place(-(-n // rows))
        super().resizeEvent(e)


class ResizeWatcher(QWidget):
    resized = Signal(int)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit(self.width())


class Stat(QFrame):
    def __init__(self, label):
        super().__init__()
        self.setObjectName("card")
        self.value = QLabel("–")
        self.value.setObjectName("statValue")
        self.label = QLabel(label)
        self.label.setObjectName("statLabel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lay.addWidget(self.label)
        lay.addWidget(self.value)

    def set_label(self, text):
        self.label.setText(text)

    def set(self, text, color=None):
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color};" if color else "")


# ---------------------------------------------------------------- attention pulse (expired Google sign-in)
def _mix(a, b, t):
    """Blend two colours: t=0 gives a, t=1 gives b."""
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


class Pulse(QObject):
    """A slow, endless 0 -> 1 -> 0 breathing value. Several widgets can follow the same pulse so they stay in step."""
    changed = Signal(float)

    def __init__(self, parent=None, period_ms=1800):
        super().__init__(parent)
        self.level = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(period_ms)
        self.anim.setStartValue(0.0)
        self.anim.setKeyValueAt(0.5, 1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.InOutSine)
        self.anim.setLoopCount(-1)
        self.anim.valueChanged.connect(self._tick)

    def _tick(self, value):
        self.level = float(value)
        self.changed.emit(self.level)

    def set_running(self, on):
        if on and self.anim.state() != QVariantAnimation.Running:
            self.anim.start()
        elif not on and self.anim.state() == QVariantAnimation.Running:
            self.anim.stop()
            self._tick(0.0)

    def running(self):
        return self.anim.state() == QVariantAnimation.Running


class PulseMenuBar(QMenuBar):
    """Menu bar that can highlight one action with a slowly pulsing badge (used for Settings)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.alert_action = None
        self.level = 0.0

    def set_alert(self, action, pulse=None):
        self.alert_action = action
        if pulse is not None:
            pulse.changed.connect(self._on_pulse)
        self.update()

    def _on_pulse(self, level):
        self.level = level
        if self.alert_action is not None:
            self.update(self.actionGeometry(self.alert_action))

    def paintEvent(self, event):
        super().paintEvent(event)
        action = self.alert_action
        if action is None or not action.isVisible():
            return
        rect = QRectF(self.actionGeometry(action)).adjusted(1.5, 2.5, -1.5, -2.5)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(BAD), 1.4))
        p.setBrush(_mix("#FBE9E4", "#EBA391", self.level))
        p.drawRoundedRect(rect, 6, 6)
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        p.setPen(_mix(BAD, "#7A2516", self.level))
        p.drawText(rect, Qt.AlignCenter, action.text().replace("&", ""))
        p.end()


class AlertCard(QFrame):
    """A normal 'card' frame that can draw a pulsing red outline on top of itself."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.alert = False
        self.level = 0.0

    def follow(self, pulse):
        pulse.changed.connect(self._on_pulse)

    def set_alert(self, on):
        self.alert = bool(on)
        self.update()

    def _on_pulse(self, level):
        self.level = level
        if self.alert:
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.alert:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        colour = QColor(BAD)
        colour.setAlpha(round(110 + 145 * self.level))
        p.setPen(QPen(colour, 1.5 + 1.5 * self.level))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 12, 12)
        p.end()
