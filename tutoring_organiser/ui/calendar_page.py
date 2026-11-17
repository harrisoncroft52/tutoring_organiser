"""The Calendar page: week/month/list views, Google sync, Meet links, booking lessons (mixed into MainWindow)."""
import uuid
from datetime import date as Date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..calendar_api import (
    calendar_create_event,
    calendar_delete_event,
    calendar_resume_event,
    calendar_sync_tutoring,
)
from ..config import DT_FMT
from ..google_auth import friendly_error, google_credentials, google_status, mark_signin_expired, needs_signin
from ..helpers import needs_calendar_delete, nice_dt
from ..parsing import parse_calendar_paste
from .calendar_widgets import make_event, MonthView, WeekView
from .dialogs import CALENDAR_PLACEHOLDER, CalendarLessonDialog, LessonDialog, PasteDialog, PastePreviewDialog
from .jobs import Job
from .theme import ACCENT, invite_colour, MUTED
from .widgets import cell, make_table


class CalendarPageMixin:
    """Methods for the Calendar page and Google Calendar actions. Mixed into MainWindow."""

    def sync_google_calendar(self, silent=False):
        if self.calendar_sync_job:
            return
        if google_status() != "ready":
            if not silent and hasattr(self, "calendar_sync_status"):
                self.calendar_sync_status.setText("Connect Google to sync tutoring bookings.")
            return
        lesson_rows = [dict(row) for row in self.store.calendar_lessons()]
        student_rows = [dict(row) for row in self.store.students()]
        self.calendar_sync_status.setText("Syncing tutoring bookings…")
        self.calendar_sync_button.setEnabled(False)
        self.calendar_sync_job = Job(lambda: calendar_sync_tutoring(
            google_credentials(interactive=False), lesson_rows, student_rows), self)
        self.calendar_sync_job.done.connect(lambda result, err: self.calendar_sync_done(result, err, silent))
        self.calendar_sync_job.start()

    def calendar_sync_done(self, result, err, silent=False):
        self.calendar_sync_job = None
        self.calendar_sync_button.setEnabled(True)
        if err:
            if needs_signin(err):
                mark_signin_expired()           # turns on the Settings alert straight away
                self.check_google_alert()
            message = friendly_error(err)
            self.calendar_sync_status.setText("Calendar sync needs attention: " + message)
            if not silent:
                QMessageBox.warning(self, "Google Calendar sync", message)
            return
        for action in result["actions"]:
            self.store.apply_calendar_event(action["sid"], action["event"])
        for event_id in result["missing_ids"]:
            self.store.apply_calendar_missing(event_id)
        self.refresh()
        self.populate_calendar()
        self.calendar_sync_status.setText(
            f"Synced tutoring bookings at {datetime.now():%I:%M %p}. "
            f"{len(result['actions'])} booking(s) checked; {len(result['missing_ids'])} missing event(s) marked cancelled.")

    def delete_linked_lesson(self, lesson_ids):
        self.delete_lesson_ids(lesson_ids if isinstance(lesson_ids, (list, tuple, set)) else [lesson_ids])

    def delete_lesson_ids(self, lesson_ids):
        ids = list(dict.fromkeys(int(lid) for lid in lesson_ids if lid is not None))
        rows = [self.store.lesson(lid) for lid in ids]
        rows = [row for row in rows if row]
        if not rows:
            return
        linked = [row for row in rows if needs_calendar_delete(row)]     # only these need a Google request
        if not linked:
            for row in rows:
                self.store.delete_lesson(row["id"])
            self.refresh(); self.populate_calendar()
            return
        if self.calendar_sync_job:
            QMessageBox.information(self, "Calendar is syncing", "Wait for the current Google Calendar sync to finish, then try again.")
            return
        if google_status() != "ready":
            if QMessageBox.question(self, "Google Calendar is unavailable",
                    "Delete the selected lesson(s) from the tracker anyway? Google Calendar couldn't be checked, "
                    "so any booking that still exists there may remain.") != QMessageBox.Yes:
                return
            for row in rows:
                self.store.delete_lesson(row["id"])
            self.refresh(); self.populate_calendar()
            self.calendar_sync_status.setText("Selected lesson(s) deleted from the tracker. Google Calendar was unavailable.")
            return
        self.calendar_sync_status.setText("Removing linked Google Calendar bookings…")
        self.calendar_sync_button.setEnabled(False)
        # Credential refresh is handled in the worker along with each remote deletion.
        def work():
            credentials = google_credentials(interactive=False)
            deleted, failures = [], []
            for row in linked:
                try:
                    calendar_delete_event(credentials, row["calendar_event_id"])
                    deleted.append(row["id"])
                except Exception as exc:
                    failures.append((row["id"], friendly_error(exc)))
            return deleted, failures
        self.calendar_sync_job = Job(work, self)
        self.calendar_sync_job.done.connect(lambda result, err: self.calendar_delete_done(rows, result, err))
        self.calendar_sync_job.start()

    def calendar_delete_done(self, rows, result, err):
        self.calendar_sync_job = None
        self.calendar_sync_button.setEnabled(True)
        if err:
            failures = [(row["id"], friendly_error(err)) for row in rows if needs_calendar_delete(row)]
            deleted = []
        else:
            deleted, failures = result
        delete_ids = set(deleted)
        for row in rows:
            if not needs_calendar_delete(row):
                delete_ids.add(row["id"])
        for lid in delete_ids:
            self.store.delete_lesson(lid)
        if failures:
            details = "\n".join(dict.fromkeys(message for _, message in failures))
            question = (f"Google couldn't remove {len(failures)} booking(s):\n\n{details}\n\n"
                        "Delete those lesson(s) from the tracker anyway? A booking that still exists in Google may remain.")
            if QMessageBox.question(self, "Couldn't remove every Calendar booking", question) == QMessageBox.Yes:
                for lid, _ in failures:
                    self.store.delete_lesson(lid)
            self.calendar_sync_status.setText("Some Google Calendar bookings could not be confirmed.")
        else:
            self.calendar_sync_status.setText("Selected lesson(s) and linked Calendar bookings deleted.")
        self.refresh()
        self.populate_calendar()

    def build_calendar_page(self):
        page = QWidget()
        page.setObjectName("root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        heading = QLabel("Calendar")
        heading.setObjectName("title")
        self.cal_search = QLineEdit()
        self.cal_search.setPlaceholderText("Search lessons (student, notes, PDF, status)")
        self.cal_search.setClearButtonEnabled(True)
        self.cal_search.setFixedWidth(300)
        self.cal_search.textChanged.connect(lambda _: self.populate_calendar())
        self.cal_search.returnPressed.connect(self.jump_to_match)
        paste_button = QPushButton("Paste calendar sessions")
        paste_button.setObjectName("primary")
        paste_button.clicked.connect(self.paste_lessons)
        self.calendar_sync_button = QPushButton("Sync Google Calendar")
        self.calendar_sync_button.clicked.connect(lambda: self.sync_google_calendar(silent=False))
        top = QHBoxLayout()
        top.addWidget(heading, 1)
        top.addWidget(self.cal_search)
        top.addWidget(self.calendar_sync_button)
        top.addWidget(paste_button)
        layout.addLayout(top)

        self.btn_today = QPushButton("Today")
        self.btn_today.clicked.connect(self.cal_today)
        self.btn_prev = QPushButton("\u2039")
        self.btn_next = QPushButton("\u203a")
        for b_, step in ((self.btn_prev, -1), (self.btn_next, 1)):
            b_.setFixedWidth(40)
            b_.clicked.connect(lambda _=False, st=step: self.cal_step(st))
        self.cal_title = QLabel("")
        self.cal_title.setObjectName("studentName")
        self.view_buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        nav = QHBoxLayout()
        nav.setSpacing(6)
        nav.addWidget(self.btn_today)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.btn_next)
        nav.addSpacing(10)
        nav.addWidget(self.cal_title, 1)
        for label, key in (("Week", "week"), ("Month", "month"), ("List", "list")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumWidth(76)
            group.addButton(btn)
            btn.clicked.connect(lambda _=False, k=key: self.set_cal_view(k))
            self.view_buttons[key] = btn
            nav.addWidget(btn)
        layout.addLayout(nav)
        self.btn_delete_calendar_lessons = QPushButton("Delete selected lessons")
        self.btn_delete_calendar_lessons.setObjectName("danger")
        self.btn_delete_calendar_lessons.setToolTip("In List view, click a lesson then Shift-click others to select several.")
        self.btn_delete_calendar_lessons.clicked.connect(self.delete_selected_calendar_lessons)
        layout.addWidget(self.btn_delete_calendar_lessons, 0, Qt.AlignRight)

        self.week_view = WeekView()
        self.week_view.lesson_activated.connect(self.edit_lesson_from_calendar)
        self.week_view.lesson_context_requested.connect(self.calendar_canvas_context_menu)
        self.week_view.slot_selected.connect(self.create_lesson_from_slot)
        self.month_view = MonthView()
        self.month_view.lesson_activated.connect(self.edit_lesson_from_calendar)
        self.month_view.lesson_context_requested.connect(self.calendar_canvas_context_menu)
        self.month_view.day_activated.connect(self.open_day_in_week)
        self.calendar_table = make_table(["Date", "Student", "Lesson", "Length", "Status", "Invite status", "PDF"],
                                         stretch_col=2)
        self.calendar_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.calendar_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.calendar_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.calendar_table.doubleClicked.connect(self.list_double_click)
        self.calendar_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.calendar_table.customContextMenuRequested.connect(self.calendar_list_context_menu)
        self.cal_stack = QStackedWidget()
        for w in (self.week_view, self.month_view, self.calendar_table):
            self.cal_stack.addWidget(w)
        self.calendar_hint = QLabel("")
        self.calendar_hint.setObjectName("muted")
        self.calendar_sync_status = QLabel("Google Calendar sync checks every five minutes while connected.")
        self.calendar_sync_status.setObjectName("muted")
        layout.addWidget(self.cal_stack, 1)
        layout.addWidget(self.calendar_hint)
        layout.addWidget(self.calendar_sync_status)

        self.cal_anchor = Date.today()
        view = self.store.setting("default_cal_view", "week")
        self.cal_view = view if view in self.view_buttons else "week"
        self._cal_events = []
        self.cal_timer = QTimer(self)               # keeps the red 'now' line moving
        self.cal_timer.timeout.connect(lambda: self.week_view.grid.update())
        self.cal_timer.start(60000)
        self.populate_calendar(focus=True)
        return page

    def paste_lessons(self):
        dlg = PasteDialog(self, "Paste calendar sessions",
                          "In Google Calendar, search for 'Tutoring', copy the results and paste them here. "
                          "Events need to be titled 'Tutoring <student name>'.",
                          CALENDAR_PLACEHOLDER)
        if not dlg.exec():
            return
        items, warnings = parse_calendar_paste(dlg.text())
        if not items:
            QMessageBox.information(self, "Nothing found", "I couldn't find any sessions in that text.\n\n"
                                    + "\n".join(warnings[:5]))
            return
        plan = self.store.plan_lessons(items)
        rows = []
        for r in plan:
            label = r["name"] + (" (new)" if r["new_student"] and r["kind"] != "skip" else "")
            rows.append((r["kind"], [nice_dt(r["start"]), label, f"{r['duration']} min", r["note"]]))
        prev = PastePreviewDialog(self, "Review pasted sessions", ["When", "Student", "Length", "Note"], rows, warnings,
                                  offer_create=any(r["new_student"] and r["kind"] != "skip" for r in plan))
        if not prev.exec():
            return
        added, updated, created, skipped = self.store.apply_lesson_plan(plan, prev.action == "add", prev.create_missing)
        self.refresh()
        self.populate_calendar()
        msg = [f"Added {added} lesson(s)."]
        if updated:
            msg.append(f"Filled in the time on {updated} existing PDF lesson(s).")
        if created:
            msg.append(f"Created {created} new student(s) at $0/hour. Open each one and press Edit to set the rate.")
        if skipped:
            msg.append(f"Skipped {skipped} lesson(s) for students that don't exist.")
        QMessageBox.information(self, "Sessions added", "\n".join(msg))

    def week_start_of(self, d):
        offset = d.weekday() if self.store.setting("week_start", "mon") == "mon" else (d.weekday() + 1) % 7
        return d - timedelta(days=offset)

    @staticmethod
    def week_title(days):
        a, z = days[0], days[-1]
        if a.year != z.year:
            return f"{a.day} {a:%b %Y} \u2013 {z.day} {z:%b %Y}"
        if a.month != z.month:
            return f"{a.day} {a:%b} \u2013 {z.day} {z:%b %Y}"
        return f"{a.day} \u2013 {z.day} {z:%b %Y}"

    def populate_calendar(self, *_, focus=False):
        """Re-draw the current Calendar view from the database, applying the search box."""
        if not hasattr(self, "cal_stack"):
            return
        tokens = self.cal_search.text().lower().split()
        pairs = []
        total = 0
        for row in self.store.calendar_lessons():
            total += 1
            ev = make_event(row)
            text = " ".join([ev["name"], ev["notes"], Path(row["pdf"]).name if row["pdf"] else "", ev["state"],
                             "scheduled" if row["status"] == "scheduled" else row["status"],
                             "" if ev["invite"] == "\u2013" else ev["invite"],
                             nice_dt(row["start"])]).lower()
            if all(t in text for t in tokens):
                pairs.append((row, ev))
        events = [ev for _, ev in pairs]
        self._cal_events = events

        # List view: newest first
        pairs_desc = sorted(pairs, key=lambda pr: pr[0]["start"], reverse=True)
        t = self.calendar_table
        t.setRowCount(len(pairs_desc))
        for i, (row, ev) in enumerate(pairs_desc):
            status = "Upcoming" if ev["state"] == "upcoming" else ev["state"].replace("done", "completed").capitalize()
            t.setItem(i, 0, cell(nice_dt(row["start"]), data=row["id"]))
            t.setItem(i, 1, cell(ev["name"]))
            t.setItem(i, 2, cell(row["notes"] or "Lesson"))
            t.setItem(i, 3, cell(f"{row['duration']} min"))
            t.setItem(i, 4, cell(status, ACCENT if status == "Upcoming" else (MUTED if status == "Cancelled" else None)))
            t.setItem(i, 5, cell(ev["invite"], invite_colour(ev["invite"])))
            t.setItem(i, 6, cell(Path(row["pdf"]).name if row["pdf"] else ""))

        a = self.cal_anchor
        if self.cal_view == "week":
            days = [self.week_start_of(a) + timedelta(i) for i in range(7)]
            self.week_view.set_data(days, events, focus=focus)
            self.cal_title.setText(self.week_title(days))
            n = sum(1 for e in events if days[0] <= e["start"].date() <= days[-1])
            period = f"{n} lesson(s) this week"
        elif self.cal_view == "month":
            first = Date(a.year, a.month, 1)
            nxt = Date(a.year + (a.month == 12), a.month % 12 + 1, 1)
            start = self.week_start_of(first)
            rows_n = -(-((first - start).days + (nxt - first).days) // 7)
            cells = [start + timedelta(i) for i in range(rows_n * 7)]
            self.month_view.set_data(cells, a.month, events, [c.strftime("%a") for c in cells[:7]])
            self.cal_title.setText(f"{a:%B %Y}")
            n = sum(1 for e in events if e["start"].year == a.year and e["start"].month == a.month)
            period = f"{n} lesson(s) this month"
        else:
            self.cal_title.setText("All lessons, newest first")
            period = f"{len(events)} lesson(s)"
        self.cal_stack.setCurrentIndex({"week": 0, "month": 1, "list": 2}[self.cal_view])
        self.btn_delete_calendar_lessons.setVisible(self.cal_view == "list")
        for key, btn in self.view_buttons.items():
            btn.setChecked(key == self.cal_view)
        for b_ in (self.btn_prev, self.btn_next):
            b_.setEnabled(self.cal_view != "list")
        if tokens:
            self.calendar_hint.setText(f"{len(events)} of {total} lessons match your search \u2013 {period}. "
                                       "Press Enter to jump to the next match.")
        elif self.cal_view == "list":
            self.calendar_hint.setText(period + ". Click a lesson, then Shift-click to select a range; use Delete selected lessons.")
        else:
            self.calendar_hint.setText(period + ". Double-click a lesson to edit it, right-click for more."
                                       + (" Drag on an empty time slot to book a lesson." if self.cal_view == "week" else ""))

    def set_cal_view(self, view):
        self.cal_view = view
        self.populate_calendar(focus=True)

    def cal_today(self):
        self.cal_anchor = Date.today()
        if self.cal_view == "list":
            now = datetime.now().strftime(DT_FMT)
            for i in range(self.calendar_table.rowCount()):
                lid = self.calendar_table.item(i, 0).data(Qt.UserRole)
                row = self.store.lesson(lid)
                if row and row["start"] <= now:
                    self.calendar_table.scrollToItem(self.calendar_table.item(i, 0), QAbstractItemView.PositionAtTop)
                    break
        self.populate_calendar(focus=True)

    def cal_step(self, direction):
        a = self.cal_anchor
        if self.cal_view == "week":
            self.cal_anchor = a + timedelta(days=7 * direction)
        elif self.cal_view == "month":
            idx = a.year * 12 + (a.month - 1) + direction
            self.cal_anchor = Date(idx // 12, idx % 12 + 1, 1)
        self.populate_calendar(focus=True)

    def open_day_in_week(self, day):
        self.cal_anchor = day
        self.cal_view = "week"
        self.populate_calendar(focus=True)

    def jump_to_match(self):
        """Enter in the search box: go to the next upcoming match (or the latest past one)."""
        if not self._cal_events:
            return
        now = datetime.now()
        later = sorted((e for e in self._cal_events if e["start"] >= now), key=lambda e: e["start"])
        target = later[0] if later else max(self._cal_events, key=lambda e: e["start"])
        self.cal_anchor = target["start"].date()
        self.populate_calendar(focus=True)

    def edit_lesson_from_calendar(self, lid):
        row = self.store.lesson(lid)
        if not row:
            return
        dlg = LessonDialog(self, self.store.student(row["student_id"]), row)
        if dlg.exec():
            self.store.save_lesson(lid, row["student_id"], *dlg.values())
            self.refresh()
            self.populate_calendar()

    def calendar_list_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        row = self.calendar_table.rowAt(pos.y())
        if row < 0:
            return
        if not any(index.row() == row for index in self.calendar_table.selectionModel().selectedRows()):
            self.calendar_table.selectRow(row)
        lid = self.calendar_table.item(row, 0).data(Qt.UserRole)
        menu = QMenu(self)
        edit = menu.addAction("Edit lesson")
        invite = menu.addAction("Send invite…")
        meet = self.add_meet_action(menu, lid)
        delete = menu.addAction("Delete selected lesson(s)")
        chosen = menu.exec(self.calendar_table.viewport().mapToGlobal(pos))
        if chosen == edit:
            self.edit_lesson_from_calendar(lid)
        elif chosen == invite:
            self.invite_for_lesson(lid)
        elif meet is not None and chosen == meet:
            self.run_meet_action(lid)
        elif chosen == delete:
            self.delete_selected_calendar_lessons()

    def delete_selected_calendar_lessons(self):
        lids = [self.calendar_table.item(index.row(), 0).data(Qt.UserRole)
                for index in self.calendar_table.selectionModel().selectedRows()
                if self.calendar_table.item(index.row(), 0)]
        self.confirm_delete_lessons(lids)

    def confirm_delete_lessons(self, lids):
        rows = [r for r in (self.store.lesson(l) for l in lids) if r]
        if not rows:
            return
        live = sum(needs_calendar_delete(r) for r in rows)
        what = "this lesson" if len(rows) == 1 else f"these {len(rows)} lessons"
        if live:
            text = (f"Delete {what}? {live} linked Google Calendar event(s) will also be deleted from Google. "
                    "PDF files are not touched.")
        else:
            text = (f"Delete {what} from the tracker? No Google Calendar request will be sent "
                    "(nothing live exists there). PDF files are not touched.")
        if QMessageBox.question(self, "Delete lesson(s)", text) != QMessageBox.Yes:
            return
        self.delete_lesson_ids([r["id"] for r in rows])

    def calendar_canvas_context_menu(self, lesson_id, global_pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        edit = menu.addAction("Edit lesson")
        invite = menu.addAction("Send invite…")
        meet = self.add_meet_action(menu, lesson_id)
        menu.addSeparator()
        delete = menu.addAction("Delete lesson")
        chosen = menu.exec(global_pos)
        if chosen == edit:
            self.edit_lesson_from_calendar(lesson_id)
        elif chosen == invite:
            self.invite_for_lesson(lesson_id)
        elif meet is not None and chosen == meet:
            self.run_meet_action(lesson_id)
        elif chosen == delete:
            self.confirm_delete_lessons([lesson_id])

    def add_meet_action(self, menu, lid):
        """Adds 'Create Meet link' (no link yet) or 'Copy Meet link' (has one). None for cancelled/untimed lessons."""
        row = self.store.lesson(lid)
        if row is None or row["status"] == "cancelled" or row["start"].endswith(" 00:00"):
            return None
        return menu.addAction("Copy Meet link" if row["meet_url"] else "Create Meet link")

    def run_meet_action(self, lid):
        row = self.store.lesson(lid)
        if row is None:
            return
        if row["meet_url"]:
            QApplication.clipboard().setText(row["meet_url"])
            self.calendar_sync_status.setText("Meet link copied.")
        else:
            self.create_meet_for_lesson(lid)

    def create_meet_for_lesson(self, lid):
        """Adds a Google Meet link to the lesson's Calendar event (creating the event first if it isn't in Google
        Calendar yet). Google sends nothing to the student: the invite email is still yours to review and send."""
        row = self.store.lesson(lid)
        if row is None:
            return
        if google_status() != "ready":
            QMessageBox.information(self, "Google Calendar", "Connect your Google account first (Settings \u2192 Google account).")
            return
        if self.calendar_sync_job:
            QMessageBox.information(self, "Calendar is syncing", "Wait for the current Google Calendar sync to finish, then try again.")
            return
        name = self.store.student(row["student_id"])["name"]
        start, duration = datetime.strptime(row["start"], DT_FMT), row["duration"]
        event_id = row["calendar_event_id"] or ""
        new_id = event_id or uuid.uuid4().hex
        self._meet_ctx = {"lid": lid, "new_event": not event_id, "event_id": new_id}
        self.calendar_sync_status.setText("Creating the Meet link\u2026")
        self.calendar_sync_button.setEnabled(False)

        def work():
            creds = google_credentials(interactive=False)
            if event_id:
                return calendar_resume_event(creds, event_id)
            return calendar_create_event(creds, event_id=new_id, summary=f"Tutoring {name}",
                                         description=f"Online tutoring lesson with {name}.",
                                         start=start, duration=duration, emails=[])
        self.calendar_sync_job = Job(work, self)
        self.calendar_sync_job.done.connect(self.calendar_meet_made)
        self.calendar_sync_job.start()

    def calendar_meet_made(self, res, err):
        self.calendar_sync_job = None
        self.calendar_sync_button.setEnabled(True)
        ctx = self._meet_ctx
        if err:
            message = friendly_error(err)
            self.calendar_sync_status.setText("Couldn't create the Meet link: " + message)
            QMessageBox.warning(self, "Couldn't create the Meet link", message)
            return
        row = self.store.lesson(ctx["lid"])
        if row is not None:
            if ctx["new_event"]:
                self.store.attach_event(row["student_id"], row["start"], row["duration"], res["event_id"], res["meet_url"])
            elif res["meet_url"]:
                self.store.set_meet_url(ctx["lid"], res["meet_url"])
        self.refresh()
        self.populate_calendar()
        if res["meet_url"]:
            self.calendar_sync_status.setText("Meet link added to the Google Calendar event.")
        else:
            self.calendar_sync_status.setText("Google is still creating the link. Right-click the lesson and try again in a minute."
                                              + (f" ({res['warning']})" if res.get("warning") else ""))

    def create_lesson_from_slot(self, start, end):
        """A drag on the week grid selected start..end. Ask which student, then save and (optionally) create the event."""
        students = self.store.students()
        if not students:
            QMessageBox.information(self, "No students yet", "Add a student first, then drag on the calendar to book a lesson.")
            return
        minutes = max(5, int((end - start).total_seconds() // 60))
        dlg = CalendarLessonDialog(self, students, start, minutes, google_status() == "ready")
        if not dlg.exec():
            return
        sid, start_s, duration, rate, push = dlg.values()
        if self.store.lesson_at(sid, start_s):
            QMessageBox.warning(self, "Lesson already exists",
                                "That student already has a lesson at that time. Double-click it to edit instead.")
            return
        if push and self.calendar_sync_job:
            QMessageBox.information(self, "Calendar is syncing", "Wait for the current Google Calendar sync to finish, then try again.")
            return
        self.store.save_lesson(None, sid, start_s, duration, rate, "scheduled", "", "")
        self.refresh(); self.populate_calendar()
        if not push:
            self.calendar_sync_status.setText("Lesson saved in the tracker only. No Google Calendar request was sent.")
            return
        name = self.store.student(sid)["name"]
        start_dt = datetime.strptime(start_s, DT_FMT)
        event_id = uuid.uuid4().hex             # caller-chosen id: a retry can't create a duplicate
        self.calendar_sync_status.setText("Creating the Google Calendar event…")
        self.calendar_sync_button.setEnabled(False)

        def work():
            return calendar_create_event(google_credentials(interactive=False), event_id=event_id,
                                         summary=f"Tutoring {name}", description=f"Online tutoring lesson with {name}.",
                                         start=start_dt, duration=duration, emails=[])
        self.calendar_sync_job = Job(work, self)
        self.calendar_sync_job.done.connect(lambda res, err: self.calendar_event_made(sid, start_s, duration, res, err))
        self.calendar_sync_job.start()

    def calendar_event_made(self, sid, start_s, duration, res, err):
        self.calendar_sync_job = None
        self.calendar_sync_button.setEnabled(True)
        if err:
            message = friendly_error(err)
            self.calendar_sync_status.setText("Lesson saved, but the Google event wasn't created: " + message)
            QMessageBox.warning(self, "Couldn't create the Calendar event",
                                message + "\n\nThe lesson is saved in the tracker. If it is in the future, the next "
                                "Google Calendar sync will try to create the event again.")
            return
        self.store.attach_event(sid, start_s, duration, res["event_id"], res["meet_url"])
        self.refresh(); self.populate_calendar()
        extra = f" (Meet link not ready yet: {res['warning']})" if res.get("warning") else ""
        self.calendar_sync_status.setText("Lesson saved and Google Calendar event created. "
                                          "Right-click it and choose Send invite… to email the student." + extra)

    def list_double_click(self, index):
        lid = self.calendar_table.item(index.row(), 0).data(Qt.UserRole)
        if index.column() == 6:
            self.open_calendar_pdf(index)
        else:
            self.edit_lesson_from_calendar(lid)

    def show_calendar(self):
        self.populate_calendar()
        self.show_page(self.pages.indexOf(self.calendar_page))

    def open_calendar_pdf(self, index):
        lid = self.calendar_table.item(index.row(), 0).data(Qt.UserRole)
        row = self.store.con.execute("SELECT pdf FROM lessons WHERE id=?", (lid,)).fetchone()
        if row and row["pdf"] and Path(row["pdf"]).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(row["pdf"]))
        elif row and row["pdf"]:
            QMessageBox.warning(self, "File missing", f"Can't find:\n{row['pdf']}")
