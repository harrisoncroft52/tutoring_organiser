"""The main window: student list, page switching, drag-and-drop of PDFs and folders."""
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import DATA_DIR, DT_FMT, ICON_PATH
from ..database import Store
from ..google_auth import google_status
from ..helpers import money, note_line, short_dt
from ..updater import UpdateManager
from ..version import __version__ as APP_VERSION
from .calendar_page import CalendarPageMixin
from .detail_panel import DetailPanel
from .dialogs import StudentDialog
from .invite_page import InvitePage
from .overview_page import OverviewPageMixin
from .resources_page import ResourcesPageMixin
from .settings_page import SettingsPage
from .theme import ACCENT, BAD, GOOD, MUTED
from .widgets import (
    cell,
    flash_table_rows,
    FlashDelegate,
    make_table,
    Pulse,
    PulseMenuBar,
    ResizeHook,
    ResizeWatcher,
    SmartSplitter,
    Toast,
)


class MainWindow(CalendarPageMixin, ResourcesPageMixin, OverviewPageMixin, QMainWindow):
    """The app window. Page-specific methods live in the mixins in calendar_page, resources_page and overview_page."""
    def __init__(self):
        super().__init__()
        self.store = Store()
        self.setWindowTitle("Tutoring Organiser")
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1280, 780)
        self.setMinimumSize(640, 520)
        self.setAcceptDrops(True)

        root = ResizeWatcher()
        root.setObjectName("root")
        self.pages = QStackedWidget()
        self.pages.addWidget(root)
        self.setCentralWidget(self.pages)
        self.toast = Toast(self)

        title = QLabel("Students")
        title.setObjectName("title")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search students")
        self.search.textChanged.connect(self.refresh)
        btn_new = QPushButton("New student")
        btn_new.setObjectName("primary")
        btn_new.clicked.connect(self.new_student)
        btn_import = QPushButton("Import folder")
        btn_import.clicked.connect(self.import_dialog)
        btn_sync = QPushButton("Sync PDFs")
        btn_sync.clicked.connect(self.sync_all)

        btn_box = QWidget()
        bl = QHBoxLayout(btn_box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        for b in (btn_import, btn_sync, btn_new):
            bl.addWidget(b)
        top = QBoxLayout(QBoxLayout.LeftToRight)
        top.addWidget(title, 1)
        top.addWidget(btn_box)

        self.table = make_table(["Student", "Notes", "Next lesson", "Last lesson", "Balance"], stretch_col=1)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.table.horizontalHeader().setMinimumSectionSize(70)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # never a sideways scrollbar: columns adapt
        self.table.setWordWrap(False)
        self.table.setItemDelegate(FlashDelegate(self.table))
        self._table_hook = ResizeHook(self.table.viewport(), self.fit_student_columns)

        self.drop_note = QLabel("Drag student folders anywhere onto this window to import them. "
                                "Drop a PDF to file it under the right student.")
        self.drop_note.setWordWrap(True)
        self.drop_note.setMinimumWidth(1)
        self.drop_note.setObjectName("muted")

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        ll.addLayout(top)
        ll.addWidget(self.search)
        ll.addWidget(self.table, 1)
        ll.addWidget(self.drop_note)

        self.detail = DetailPanel(self.store)
        self.detail.changed = self.refresh
        self.detail.toast.connect(lambda text, kind: self.toast.show_message(text, kind))
        self.detail.invite_requested.connect(self.invite_for_lesson)
        self.detail.delete_linked_requested.connect(self.delete_linked_lesson)
        self.download_temp = tempfile.TemporaryDirectory(prefix="TutoringOrganiser-Online-")
        self.detail.download_temp_dir = self.download_temp.name

        split = self.split = SmartSplitter(left, self.detail, sizes=(680, 600), min_sizes=(330, 440), stretch=(5, 6))
        saved = self.store.setting("students_split", "")
        if saved:
            try:
                split.setSizes([max(1, int(v)) for v in saved.split(",")][:2])
            except ValueError:
                pass
        self._split_save = QTimer(self)                    # remember the divider position between sessions
        self._split_save.setSingleShot(True)
        self._split_save.setInterval(500)
        self._split_save.timeout.connect(self.save_split_sizes)
        split.splitterMoved.connect(lambda *_: self._split_save.start())
        # Compact windows stack the two panes instead of squeezing them side by side.
        root.resized.connect(lambda w: split.setOrientation(Qt.Vertical if w < 1040 else Qt.Horizontal))

        def reflow_head():                                 # title above the buttons once the list pane gets narrow
            narrow = left.width() < 470
            top.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
            top.setStretchFactor(title, 0 if narrow else 1)
        self._head_hook = ResizeHook(left, reflow_head)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(split)

        self.calendar_page = self.build_calendar_page()
        self.online_page = self.build_online_page()
        self.overview_page = self.build_overview_page()
        self.invite_page = InvitePage(self.store)
        self.invite_page.changed = self.refresh
        self.invite_page.google_connected = lambda: self.sync_google_calendar(silent=True)
        self.resources_page = self.build_resources_page()
        self.settings_page = SettingsPage(self.store, self.invite_page)
        self.settings_page.saved = self.apply_settings
        self.updates = UpdateManager(self, self.store, before_install=self.backup_database)
        self.settings_page.updater = self.updates
        for page in (self.overview_page, self.calendar_page, self.invite_page, self.online_page, self.resources_page,
                     self.settings_page):
            self.pages.addWidget(page)

        bar = PulseMenuBar(self)
        self.setMenuBar(bar)
        bar.addAction("Overview", lambda: self.show_page(self.pages.indexOf(self.overview_page)))
        bar.addAction("Students", lambda: self.show_page(0))
        bar.addAction("Invite", self.show_invite)
        bar.addAction("Calendar", self.show_calendar)
        bar.addAction("Online resources", lambda: self.show_page(self.pages.indexOf(self.online_page)))
        bar.addAction("My resources", self.show_resources)
        self.settings_action = bar.addAction("Settings", self.show_settings)

        # Expired Google sign-in: Settings in the menu bar and the Google card in Settings pulse until fixed.
        self.google_pulse = Pulse(self)
        bar.set_alert(None, self.google_pulse)
        self.settings_page.follow_pulse(self.google_pulse)
        self._google_state = None
        self.google_watch = QTimer(self)                 # cheap local file check, no network
        self.google_watch.timeout.connect(self.check_google_alert)
        self.google_watch.start(2000)

        self.calendar_sync_job = None
        self.calendar_sync_timer = QTimer(self)
        self.calendar_sync_timer.timeout.connect(lambda: self.sync_google_calendar(silent=True))
        self.calendar_sync_timer.start(5 * 60 * 1000)
        if google_status() == "ready":
            QTimer.singleShot(3000, lambda: self.sync_google_calendar(silent=True))

        self.refresh()
        self.check_google_alert()
        self.show_page(self.pages.indexOf(self.overview_page))     # always open on the Overview
        self.updates.check_on_startup()

    def check_google_alert(self):
        """Turn the Settings pulse on/off to match the Google sign-in, and refresh status labels when it changes."""
        state = google_status()
        if state == self._google_state:
            return
        was_ready = self._google_state == "ready"
        self._google_state = state
        expired = state == "expired"
        self.menuBar().set_alert(self.settings_action if expired else None)
        self.google_pulse.set_running(expired)
        self.settings_page.refresh_google()
        self.invite_page.update_google_status()
        if expired and was_ready:
            self.toast.show_message("Your Google sign-in has expired. Open Settings and press Connect Google "
                                    "to keep Calendar sync and invitation emails working.", "bad")

    def backup_database(self):
        """Copy the live database into DATA_DIR/backups (keeps the newest 5). Used before updates."""
        folder = DATA_DIR / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"tutoring-{datetime.now():%Y%m%d-%H%M%S}-v{APP_VERSION}.db"
        out = sqlite3.connect(dest)
        try:
            self.store.con.backup(out)
        finally:
            out.close()
        for old in sorted(folder.glob("tutoring-*.db"))[:-5]:
            old.unlink(missing_ok=True)

    def closeEvent(self, event):
        self.download_temp.cleanup()
        super().closeEvent(event)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)

    def invite_for_lesson(self, lesson_id):
        row = self.store.lesson(lesson_id)
        if not row:
            return
        self.invite_page.on_show(row["student_id"])
        self.show_page(self.pages.indexOf(self.invite_page))
        if row["calendar_event_id"]:
            self.invite_page.load_lesson(row)
            if not row["meet_url"]:
                self.invite_page.check_meet()
            else:
                self.invite_page.say("Calendar booking and Meet link checked. Review the email and send when ready.", "ok")
        else:
            self.invite_page.prepare_for_lesson(row)

    def show_settings(self):
        self.settings_page.on_show()
        self.show_page(self.pages.indexOf(self.settings_page))

    def apply_settings(self):
        """Called after Settings are saved."""
        if self.invite_page.draft is None:
            self.invite_page.load_defaults(schedule=True)
        self.cal_view = self.store.setting("default_cal_view", "week")
        self.populate_calendar()

    def show_invite(self):
        self.invite_page.on_show(self.detail.sid)
        self.show_page(self.pages.indexOf(self.invite_page))

    def build_placeholder_page(self, title, message):
        page = QWidget()
        page.setObjectName("root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel(title)
        heading.setObjectName("title")
        description = QLabel(message)
        description.setObjectName("muted")
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addStretch(1)
        return page

    # ---- calendar helpers

    # ---- Google Meet links from the calendar

    # ---- list
    def sort_key(self, s, st):
        if st["next"]:
            return (0, st["next"], s["name"].lower())          # soonest upcoming first
        if st["last"]:
            # newest past first: invert the string order by negating the timestamp
            ts = datetime.strptime(st["last"], DT_FMT).timestamp()
            return (1, -ts, s["name"].lower())
        return (2, 0, s["name"].lower())

    def refresh(self):
        keep = self.detail.sid
        q = self.search.text().strip().lower()
        rows = []
        for s in self.store.students():
            if q and q not in s["name"].lower():
                continue
            st = self.store.stats(s["id"])
            rows.append((self.sort_key(s, st), s, st))
        rows.sort(key=lambda r: r[0])

        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        select_row = -1
        for r, (_, s, st) in enumerate(rows):
            upcoming = st["next"] is not None
            self.table.setItem(r, 0, cell(s["name"], bold=True, data=s["id"]))
            nl = note_line(s["notes"])
            note_cell = cell(nl, MUTED)
            note_cell.setToolTip(s["notes"] or "")
            self.table.setItem(r, 1, note_cell)
            self.table.setItem(r, 2, cell(short_dt(st["next"]) if upcoming else "–",
                                          ACCENT if upcoming else MUTED, bold=upcoming))
            self.table.setItem(r, 3, cell(short_dt(st["last"]) if st["last"] else "–", None if st["last"] else MUTED))
            bal = st["balance"]
            if abs(bal) < 0.005:
                self.table.setItem(r, 4, cell("Settled", MUTED))
            elif bal < 0:
                self.table.setItem(r, 4, cell(f"Owes {money(-bal)}", BAD))
            else:
                self.table.setItem(r, 4, cell(f"Credit {money(bal)}", GOOD))
            if s["id"] == keep:
                select_row = r
        if select_row >= 0:
            self.table.selectRow(select_row)
        self.table.blockSignals(False)

        if select_row >= 0:
            self.detail.load(keep)
        else:
            self.detail.load(None)
        self.fit_student_columns()
        self.refresh_overview()

    # Column -> narrowest useful width. Columns are kept in this order of importance; the rest are
    # hidden when the list is too narrow (Notes first). Hidden notes are still in the tooltip and detail view.
    STUDENT_COL_NEED = {0: 150, 4: 120, 2: 118, 3: 118, 1: 170}
    STUDENT_COL_ORDER = (0, 4, 2, 3, 1)

    def fit_student_columns(self):
        t = self.table
        width = t.viewport().width()
        if width <= 0:
            return
        shown, used = [], 0
        for col in self.STUDENT_COL_ORDER:
            need = self.STUDENT_COL_NEED[col]
            if col != 0 and used + need > width:
                break
            shown.append(col)
            used += need
        for col in range(t.columnCount()):
            t.setColumnHidden(col, col not in shown)
        h = t.horizontalHeader()
        for col in (2, 3, 4):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        if 1 in shown:                      # notes get the spare room; names are capped and elided
            h.setSectionResizeMode(0, QHeaderView.Interactive)
            t.setColumnWidth(0, min(max(150, t.sizeHintForColumn(0) + 24), 240))
        else:                               # no notes column: the name column takes the spare room
            h.setSectionResizeMode(0, QHeaderView.Stretch)

    def save_split_sizes(self):
        if self.split.orientation() == Qt.Horizontal and all(n > 0 for n in self.split.sizes()):
            self.store.set_setting("students_split", ",".join(str(n) for n in self.split.sizes()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "toast", None) is not None and self.toast.isVisible():
            self.toast.place()

    def on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.detail.load(self.table.item(rows[0].row(), 0).data(Qt.UserRole))
        else:
            self.detail.load(None)

    # ---- actions
    def select_student(self, sid):
        self.detail.sid = sid
        self.refresh()

    def new_student(self):
        dlg = StudentDialog(self)
        if dlg.exec():
            name, rate, folder, notes, email = dlg.values()
            sid = self.store.save_student(None, name, rate, folder, notes, email)
            if folder:
                self.store.sync_pdfs(sid)
            self.select_student(sid)

    def import_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a student folder, or a folder that contains student folders")
        if d:
            answer = QMessageBox.question(self, "Classify PDFs",
                "Import PDFs as additional/reference PDFs? Choose No to import them as lessons.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            self.do_import([d], as_additional=answer == QMessageBox.Yes)

    def do_import(self, folders, as_additional=False):
        total_s = total_p = 0
        for f in folders:
            s, p = self.store.import_folder(f, as_additional=as_additional)
            total_s += s
            total_p += p
        self.refresh()
        QMessageBox.information(
            self, "Import finished",
            f"New students: {total_s}\nPDFs linked as lessons: {total_p}\n\n"
            "Imported students start at $0/hour. Open each one, press Edit, and set the rate "
            "(this also fills in their imported lessons).\n"
            "Lesson dates come from the PDF file name when it contains a date, otherwise from the file's modified date.")

    def sync_all(self):
        n = self.store.sync_all_pdfs()
        self.refresh()
        self.toast.show_message(f"Synced {n} PDF(s) across all student folders." if n
                                else "No new PDFs found in any student folder.", "ok" if n else "info")

    # ---- drag and drop
    def dragEnterEvent(self, e):
        if e.source() is None and e.mimeData().hasUrls():      # ignore drags that started inside this app
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.source() is not None:
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        folders = [p for p in paths if os.path.isdir(p)]
        pdfs = [p for p in paths if os.path.isfile(p) and p.lower().endswith(".pdf")]
        if folders:
            self.do_import(folders)
        if pdfs:
            self.assign_dropped_pdfs(pdfs)
        e.acceptProposedAction()

    def assign_dropped_pdfs(self, pdfs):
        """Work out each PDF's student, copy it into their folder, file it under the lesson, then flash where it went."""
        done, failed = [], []
        for path in pdfs:
            sid = self.store.match_student_for_pdf(path)
            if sid is None:
                failed.append(Path(path).name)
                continue
            try:
                done.append(self.store.assign_pdf(sid, path))
            except (OSError, shutil.Error) as err:
                failed.append(f"{Path(path).name} ({err})")
        if done:
            if self.search.text().strip():                    # a search filter could hide the student
                self.search.blockSignals(True)
                self.search.clear()
                self.search.blockSignals(False)
            last = done[-1]
            self.select_student(last["sid"])                  # reloads the list and the detail view
            sids = {r["sid"] for r in done}
            flash_table_rows(self.table, [r for r in range(self.table.rowCount())
                                          if self.table.item(r, 0).data(Qt.UserRole) in sids])
            in_lessons = last["where"] in ("lesson", "new_lesson")
            self.detail.flash_assignment(last["lesson_id"] if in_lessons else None,
                                         None if in_lessons else last["path"])
        lines = [self.describe_pdf_result(r) for r in done[:4]]
        if len(done) > 4:
            lines.append(f"\u2026and {len(done) - 4} more")
        if failed:
            lines.append("Couldn't match to a student: " + ", ".join(failed[:3]) + (" \u2026" if len(failed) > 3 else "")
                         + ". Put the student's name in the file name, or drop it from their folder.")
        self.toast.show_message("\n".join(lines), "bad" if failed else "ok")

    @staticmethod
    def describe_pdf_result(r):
        day = f"{r['when'].day} {r['when']:%b}"
        where = {"lesson": f"lesson on {day}", "new_lesson": f"new lesson on {day}",
                 "additional": "Additional PDFs", "duplicate": "already filed"}[r["where"]]
        extra = (" \u00b7 copied to their folder" if r["copied"]
                 else " \u00b7 no folder set, so not copied" if r["folder_missing"] else "")
        return f"{Path(r['path']).name} \u2192 {r['name']}: {where}{extra}"
