"""Week and month calendar drawing: lesson chips, status icons, week grid, month grid."""
from datetime import date as Date, datetime, timedelta

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QToolTip, QVBoxLayout, QWidget

from ..config import DT_FMT
from ..helpers import effective_status, invite_status, nice_dt
from .theme import ACCENT, BAD, BORDER, MUTED, SURFACE, SURFACE_ALT, TEXT


CAL_COLOURS = ["#7A2E4F", "#4F7A5A", "#3F6E96", "#6B4C8A", "#A9792F", "#2F7F7A", "#5A6472", "#7B7F3B"]


def student_colour(sid):
    return QColor(CAL_COLOURS[(sid or 0) % len(CAL_COLOURS)])


def blend(c, other, t):
    return QColor(int(c.red() * (1 - t) + other.red() * t), int(c.green() * (1 - t) + other.green() * t),
                  int(c.blue() * (1 - t) + other.blue() * t))


def short_clock(d):
    return d.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")


def make_event(row):
    """One lesson row (joined with students.name) -> the dict the calendar widgets draw."""
    start = datetime.strptime(row["start"], DT_FMT)
    eff = effective_status(row)
    state = "cancelled" if eff == "cancelled" else ("upcoming" if start >= datetime.now() else "done")
    return {"id": row["id"], "sid": row["student_id"], "name": row["student_name"], "start": start,
            "end": start + timedelta(minutes=row["duration"]), "duration": row["duration"],
            "untimed": row["start"].endswith(" 00:00"),      # lessons created from a PDF have a date but no time
            "state": state, "invite": invite_status(row), "notes": row["notes"] or "",
            "meet_url": row["meet_url"] or "", "has_event": bool(row["calendar_event_id"]),
            "email_sent": bool(row["invite_sent_at"])}


def event_tooltip(ev):
    when = nice_dt(ev["start"].strftime(DT_FMT))
    lines = [ev["name"], when + ("" if ev["untimed"] else f" \u2013 {short_clock(ev['end'])}"),
             f"{ev['duration']} min \u00b7 {ev['state'].capitalize()}", f"Invite: {ev['invite']}",
             "Google Meet link: " + ("yes" if ev.get("meet_url") else "none \u2013 right-click to create one"),
             "Invite email: " + ("sent" if ev.get("email_sent") else "not sent")]
    if ev["notes"]:
        lines.append(ev["notes"])
    lines.append("Double-click to edit")
    return "\n".join(lines)


BADGE_W = 15      # width reserved for each status icon on a lesson chip


def paint_status_badges(p, rect, ev, fg, fill):
    """Two tiny icons in a chip's top-right corner: a camera (Google Meet link) and an envelope (invite emailed).
    Solid = yes. Faded outline (camera also struck through) = not yet."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    solid, faint = QColor(fg), QColor(fg)
    faint.setAlpha(120)
    y = rect.top() + 4
    # envelope (rightmost)
    x = rect.right() - BADGE_W - 1
    env = QRectF(x, y + 1, 12, 8)
    if ev.get("email_sent"):
        p.setPen(Qt.NoPen); p.setBrush(solid); p.drawRoundedRect(env, 1.5, 1.5)
        p.setPen(QPen(fill, 1.2)); p.setBrush(Qt.NoBrush)          # the flap is drawn in the chip's own colour
        p.drawLine(QPointF(x + 1, y + 2), QPointF(x + 6, y + 6)); p.drawLine(QPointF(x + 11, y + 2), QPointF(x + 6, y + 6))
    else:
        p.setPen(QPen(faint, 1.1)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(env, 1.5, 1.5)
        p.drawLine(QPointF(x + 1, y + 2), QPointF(x + 6, y + 6)); p.drawLine(QPointF(x + 11, y + 2), QPointF(x + 6, y + 6))
    # camera
    x -= BADGE_W
    body, lens = QRectF(x, y + 1.5, 8, 7), QPolygonF([QPointF(x + 8.6, y + 4.3), QPointF(x + 12.5, y + 2), QPointF(x + 12.5, y + 8), QPointF(x + 8.6, y + 5.7)])
    if ev.get("meet_url"):
        p.setPen(Qt.NoPen); p.setBrush(solid); p.drawRoundedRect(body, 1.5, 1.5); p.drawPolygon(lens)
    else:
        p.setPen(QPen(faint, 1.1)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(body, 1.5, 1.5); p.drawPolygon(lens)
        p.setPen(QPen(faint, 1.4)); p.drawLine(QPointF(x - 1, y + 10), QPointF(x + 13, y))
    p.restore()


def paint_chip(p, rect, ev, lines, badges=False):
    """A rounded lesson 'pill'. Upcoming = solid student colour, past = pale tint, cancelled = grey + strikethrough."""
    col = student_colour(ev["sid"])
    state = ev["state"]
    if state == "upcoming":
        fill, fg = col, QColor("white")
    elif state == "done":
        fill, fg = blend(col, QColor("white"), 0.8), QColor(TEXT)
    else:
        fill, fg = QColor("#EFE9EB"), QColor(MUTED)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, 5, 5)
    p.fillPath(path, fill)
    if state == "done":
        p.setClipPath(path)
        p.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()), col)
        p.setClipping(False)
    show_badges = badges and state != "cancelled" and rect.width() >= 72 and rect.height() >= 20
    if show_badges:
        paint_status_badges(p, rect, ev, fg, fill)
    p.setPen(fg)
    f = QFont(p.font())
    y = rect.top() + 3
    for n, (text, bold) in enumerate(lines):
        f.setBold(bold)
        f.setStrikeOut(state == "cancelled")
        f.setPixelSize(12 if bold else 11)
        p.setFont(f)
        fm = QFontMetrics(f)
        if y + fm.height() > rect.bottom() + 1:
            break
        reserve = 2 * BADGE_W + 2 if (show_badges and n == 0) else 0   # keep the name clear of the icons
        r = QRectF(rect.left() + 7, y, rect.width() - 10 - reserve, fm.height())
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(text, Qt.ElideRight, int(r.width())))
        y += fm.height()
    p.restore()


class _HitMixin:
    """Shared tooltip + hit-testing for widgets that keep self.rects = [(QRectF, event dict)]."""
    def _event_at(self, pos):
        for rect, ev in reversed(self.rects):
            if rect.contains(pos):
                return ev
        return None

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            ev = self._event_at(e.pos())
            if ev:
                QToolTip.showText(e.globalPos(), event_tooltip(ev), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            event = self._event_at(e.position())
            if event:
                self.lesson_context_requested.emit(event["id"], self.mapToGlobal(e.position().toPoint()))
                e.accept()
                return
        super().mousePressEvent(e)


class WeekHeader(_HitMixin, QWidget):
    """Day names/numbers across the top, plus a strip for lessons that have a date but no time."""
    lesson_activated = Signal(int)
    lesson_context_requested = Signal(int, object)
    GUTTER, HEAD, CHIP = 58, 52, 20

    def __init__(self):
        super().__init__()
        self.days, self.events, self.rects = [], [], []
        self.setFixedHeight(self.HEAD)

    def set_data(self, days, events):
        self.days = days
        self.events = [e for e in events if e["untimed"]]
        rows = max([sum(1 for e in self.events if e["start"].date() == d) for d in days] or [0])
        self.setFixedHeight(self.HEAD + (rows * (self.CHIP + 2) + 6 if rows else 0))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(SURFACE))
        cw = (self.width() - self.GUTTER) / 7
        today = Date.today()
        self.rects = []
        for i, d in enumerate(self.days):
            x = self.GUTTER + i * cw
            p.setPen(QColor(BORDER))
            p.drawLine(int(x), 0, int(x), self.height())
            is_today = d == today
            f = QFont(p.font()); f.setPixelSize(11); f.setBold(True); p.setFont(f)
            p.setPen(QColor(ACCENT if is_today else MUTED))
            p.drawText(QRectF(x, 6, cw, 14), Qt.AlignCenter, d.strftime("%a").upper())
            circle = QRectF(x + cw / 2 - 15, 21, 30, 26)
            if is_today:
                p.setPen(Qt.NoPen); p.setBrush(QColor(ACCENT)); p.drawEllipse(circle)
                p.setBrush(Qt.NoBrush)
            f.setPixelSize(16); f.setBold(False); p.setFont(f)
            p.setPen(QColor("white") if is_today else QColor(TEXT))
            p.drawText(circle, Qt.AlignCenter, str(d.day))
            evs = sorted((e for e in self.events if e["start"].date() == d), key=lambda e: e["name"].lower())
            for k, ev in enumerate(evs):
                r = QRectF(x + 2, self.HEAD + 3 + k * (self.CHIP + 2), cw - 4, self.CHIP)
                paint_chip(p, r, ev, [(ev["name"], True)])
                self.rects.append((r, ev))
        p.setPen(QColor(BORDER))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def mouseDoubleClickEvent(self, e):
        ev = self._event_at(e.position())
        if ev:
            self.lesson_activated.emit(ev["id"])


class WeekGrid(_HitMixin, QWidget):
    """24-hour time grid with each lesson drawn as a block at its day and time."""
    lesson_activated = Signal(int)
    lesson_context_requested = Signal(int, object)
    slot_selected = Signal(object, object)          # (start datetime, end datetime) from a drag on empty space
    drag_y = Signal(int)
    GUTTER, HOUR_H, SLOT = 58, 52, 15               # SLOT = snap size in minutes

    def __init__(self):
        super().__init__()
        self.days, self.events, self.rects = [], [], []
        self.setMinimumHeight(24 * self.HOUR_H)
        self.setFocusPolicy(Qt.StrongFocus)
        self._drag = None                           # {"col", "s0", "s1", "y0"} while the mouse is down

    def _slot_at(self, y):
        slots = 24 * 60 // self.SLOT
        return max(0, min(slots - 1, int(y / (self.HOUR_H * self.SLOT / 60))))

    def _col_at(self, x):
        cw = (self.width() - self.GUTTER) / 7
        i = int((x - self.GUTTER) // cw) if x >= self.GUTTER else -1
        return i if 0 <= i < len(self.days) else None

    def _drag_range(self):
        """(start_minute, end_minute) of the current drag, snapped to SLOT."""
        d = self._drag
        return min(d["s0"], d["s1"]) * self.SLOT, (max(d["s0"], d["s1"]) + 1) * self.SLOT

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._event_at(e.position()):
            col = self._col_at(e.position().x())
            if col is not None:
                s = self._slot_at(e.position().y())
                self._drag = {"col": col, "s0": s, "s1": s, "y0": e.position().y()}
                self.setFocus()
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            self._drag["s1"] = self._slot_at(e.position().y())
            self.drag_y.emit(int(e.position().y()))
            self.update()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        d = self._drag
        if d and e.button() == Qt.LeftButton:
            # Mouse release can arrive between move events; use its final y
            # coordinate so the selected end time matches where the user let go.
            d["s1"] = self._slot_at(e.position().y())
            if abs(e.position().y() - d["y0"]) >= 6:      # a plain click creates nothing
                s = min(d["s0"], d["s1"]) * self.SLOT
                t = (max(d["s0"], d["s1"]) + 1) * self.SLOT
            else:
                s = t = None
            self._drag = None
            self.update()
            if s is not None:
                day = self.days[d["col"]]
                base = datetime(day.year, day.month, day.day)
                self.slot_selected.emit(base + timedelta(minutes=s), base + timedelta(minutes=t))
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self._drag:           # Esc cancels a drag in progress
            self._drag = None
            self.update()
            return
        super().keyPressEvent(e)

    def set_data(self, days, events):
        self.days = days
        self.events = [e for e in events if not e["untimed"]]
        self.update()

    def layout_rects(self):
        """Overlapping lessons sit side by side, like Google Calendar."""
        out, cw = [], (self.width() - self.GUTTER) / 7
        for i, day in enumerate(self.days):
            evs = sorted((e for e in self.events if e["start"].date() == day), key=lambda e: (e["start"], e["end"]))
            clusters, cur, cur_end = [], [], None
            for e in evs:
                if cur and e["start"] >= cur_end:
                    clusters.append(cur); cur, cur_end = [], None
                cur.append(e)
                cur_end = e["end"] if cur_end is None else max(cur_end, e["end"])
            if cur:
                clusters.append(cur)
            for cl in clusters:
                col_ends, assign = [], []
                for e in cl:
                    for ci, last in enumerate(col_ends):
                        if last <= e["start"]:
                            col_ends[ci] = e["end"]; assign.append(ci); break
                    else:
                        col_ends.append(e["end"]); assign.append(len(col_ends) - 1)
                n = len(col_ends)
                for e, ci in zip(cl, assign):
                    y = (e["start"].hour + e["start"].minute / 60) * self.HOUR_H
                    h = min(max(20, e["duration"] / 60 * self.HOUR_H), 24 * self.HOUR_H - y) - 2
                    slot = (cw - 6) / n
                    out.append((QRectF(self.GUTTER + i * cw + 2 + ci * slot, y + 1, slot - 1, h), e))
        return out

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(SURFACE))
        cw = (self.width() - self.GUTTER) / 7
        today = Date.today()
        for i, d in enumerate(self.days):                       # tint today's column
            if d == today:
                p.fillRect(QRectF(self.GUTTER + i * cw, 0, cw, self.height()), QColor("#FBF4F6"))
        f = QFont(p.font()); f.setPixelSize(10); p.setFont(f)
        for h in range(24):
            y = h * self.HOUR_H
            p.setPen(QColor("#F1EBED"))
            p.drawLine(self.GUTTER, y, self.width(), y)
            if h:
                p.setPen(QColor(MUTED))
                label = f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"
                p.drawText(QRectF(0, y - 7, self.GUTTER - 8, 14), Qt.AlignRight | Qt.AlignVCenter, label)
        p.setPen(QColor(BORDER))
        for i in range(8):
            x = int(self.GUTTER + i * cw)
            p.drawLine(x, 0, x, self.height())
        self.rects = self.layout_rects()
        for r, ev in self.rects:
            lines = [(ev["name"], True)]
            if r.height() >= 34:
                lines.append((f"{short_clock(ev['start'])} \u2013 {short_clock(ev['end'])}", False))
            paint_chip(p, r, ev, lines, badges=True)
        if self._drag:                                           # the slot being dragged out
            s, t = self._drag_range()
            x0 = self.GUTTER + self._drag["col"] * cw
            r = QRectF(x0 + 2, s / 60 * self.HOUR_H, cw - 6, (t - s) / 60 * self.HOUR_H)
            sel = QColor(ACCENT); sel.setAlpha(70)
            p.setPen(QPen(QColor(ACCENT), 1.5)); p.setBrush(sel)
            p.drawRoundedRect(r, 5, 5)
            f2 = QFont(p.font()); f2.setPixelSize(11); f2.setBold(True); p.setFont(f2)
            p.setPen(QColor(TEXT))
            a, b = datetime(2000, 1, 1) + timedelta(minutes=s), datetime(2000, 1, 1) + timedelta(minutes=min(t, 1439))
            label = f"{short_clock(a)} \u2013 {short_clock(b) if t < 1440 else '12am'}"
            p.drawText(r.adjusted(6, 3, -4, 0), Qt.AlignLeft | Qt.AlignTop, label)
            p.setBrush(Qt.NoBrush)
        for i, d in enumerate(self.days):                       # 'now' line
            if d == today:
                now = datetime.now()
                y = (now.hour + now.minute / 60) * self.HOUR_H
                p.setPen(QPen(QColor(BAD), 2))
                p.drawLine(QPointF(self.GUTTER + i * cw, y), QPointF(self.GUTTER + (i + 1) * cw, y))
                p.setBrush(QColor(BAD)); p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(self.GUTTER + i * cw, y), 4, 4)

    def mouseDoubleClickEvent(self, e):
        ev = self._event_at(e.position())
        if ev:
            self.lesson_activated.emit(ev["id"])


class WeekView(QFrame):
    lesson_activated = Signal(int)
    lesson_context_requested = Signal(int, object)
    slot_selected = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.header, self.grid = WeekHeader(), WeekGrid()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.grid)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # wheel still scrolls; keeps columns aligned with the header
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(self.scroll, 1)
        legend = QLabel("Icons on each lesson:  camera = Google Meet link on the event  \u00b7  envelope = invite "
                        "email sent  \u00b7  faded = not yet (right-click a lesson to create a Meet link)")
        legend.setObjectName("muted")
        legend.setWordWrap(True)
        legend.setContentsMargins(10, 5, 10, 5)
        lay.addWidget(legend)
        self.header.lesson_activated.connect(self.lesson_activated)
        self.grid.lesson_activated.connect(self.lesson_activated)
        self.header.lesson_context_requested.connect(self.lesson_context_requested)
        self.grid.lesson_context_requested.connect(self.lesson_context_requested)
        self.grid.slot_selected.connect(self.slot_selected)
        self.grid.drag_y.connect(lambda y: self.scroll.ensureVisible(0, y, 0, 40))   # auto-scroll while dragging

    def set_data(self, days, events, focus=False):
        self.header.set_data(days, events)
        self.grid.set_data(days, events)
        if focus:                                   # start the view an hour above the week's first lesson
            timed = [e["start"].hour for e in events if not e["untimed"] and e["start"].date() in days]
            hour = max(0, (min(timed) if timed else 8) - 1)
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(max(0, hour * WeekGrid.HOUR_H - 10)))


class MonthView(_HitMixin, QFrame):
    lesson_activated = Signal(int)
    lesson_context_requested = Signal(int, object)
    day_activated = Signal(object)
    HEAD, CHIP_H = 30, 19

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.cells, self.month, self.by_day, self.labels = [], 1, {}, []
        self.rects, self.more, self.cell_rects = [], [], []
        self.setMinimumHeight(360)

    def set_data(self, cells, month, events, labels):
        self.cells, self.month, self.labels = cells, month, labels
        self.by_day = {}
        for e in events:
            self.by_day.setdefault(e["start"].date(), []).append(e)
        for lst in self.by_day.values():
            lst.sort(key=lambda e: (not e["untimed"], e["start"], e["name"].lower()))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(SURFACE))
        w = self.width() / 7
        rows = max(1, len(self.cells) // 7)
        ch = (self.height() - self.HEAD) / rows
        f = QFont(p.font()); f.setPixelSize(11); f.setBold(True); p.setFont(f)
        p.setPen(QColor(MUTED))
        for c, lab in enumerate(self.labels):
            p.drawText(QRectF(c * w, 0, w, self.HEAD), Qt.AlignCenter, lab.upper())
        self.rects, self.more, self.cell_rects = [], [], []
        today = Date.today()
        for idx, d in enumerate(self.cells):
            r, c = divmod(idx, 7)
            x, y = c * w, self.HEAD + r * ch
            cell = QRectF(x, y, w, ch)
            self.cell_rects.append((cell, d))
            if d.month != self.month:
                p.fillRect(cell, QColor(SURFACE_ALT))
            p.setBrush(Qt.NoBrush)                      # the 'today' circle below sets a brush; don't leak it into cell borders
            p.setPen(QColor(BORDER))
            p.drawRect(cell)
            circle = QRectF(x + 5, y + 3, 22, 20)
            f.setBold(False); f.setPixelSize(12); p.setFont(f)
            if d == today:
                p.setPen(Qt.NoPen); p.setBrush(QColor(ACCENT)); p.drawEllipse(circle)
                p.setBrush(Qt.NoBrush)
                p.setPen(QColor("white"))
            else:
                p.setPen(QColor(TEXT if d.month == self.month else "#B5A9AF"))
            p.drawText(circle, Qt.AlignCenter, str(d.day))
            evs = self.by_day.get(d, [])
            avail = max(0, int((ch - 28) // (self.CHIP_H + 2)))
            show = evs if len(evs) <= avail else evs[:max(0, avail - 1)]
            for k, ev in enumerate(show):
                rect = QRectF(x + 3, y + 26 + k * (self.CHIP_H + 2), w - 6, self.CHIP_H)
                text = ev["name"] if ev["untimed"] else f"{short_clock(ev['start'])} {ev['name']}"
                paint_chip(p, rect, ev, [(text, False)])
                self.rects.append((rect, ev))
            if len(evs) > len(show):
                rect = QRectF(x + 3, y + 26 + len(show) * (self.CHIP_H + 2), w - 6, self.CHIP_H)
                f.setPixelSize(11); f.setBold(True); p.setFont(f); p.setPen(QColor(MUTED))
                p.drawText(rect.adjusted(4, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, f"+{len(evs) - len(show)} more")
                self.more.append((rect, d))

    def _day_at(self, pos):
        for rect, d in self.cell_rects:
            if rect.contains(pos):
                return d
        return None

    def mouseReleaseEvent(self, e):
        for rect, d in self.more:
            if rect.contains(e.position()):
                self.day_activated.emit(d)

    def mouseDoubleClickEvent(self, e):
        ev = self._event_at(e.position())
        if ev:
            self.lesson_activated.emit(ev["id"])
        else:
            d = self._day_at(e.position())
            if d:
                self.day_activated.emit(d)
