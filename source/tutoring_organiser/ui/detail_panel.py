"""The right-hand student panel: stats, lessons, payments, notes and PDFs."""
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import DT_FMT
from ..helpers import (
    effective_status,
    invite_status,
    lesson_value,
    money,
    needs_calendar_delete,
    nice_dt,
    norm,
    note_line,
)
from ..parsing import parse_payments_paste
from .charts import GraphDialog, HAVE_CHARTS
from .dialogs import (
    LessonDialog,
    PasteDialog,
    PastePreviewDialog,
    PAYMENT_PLACEHOLDER,
    PAYMENT_PLACEHOLDER_BANK,
    PaymentDialog,
    StudentDialog,
)
from .theme import ACCENT, BAD, GOOD, invite_colour, MUTED
from .widgets import (
    cell,
    ElidedLabel,
    flash_table_rows,
    FlashDelegate,
    LessonDropTable,
    make_table,
    ReflowContainer,
    ResizeHook,
    Stat,
)


class DetailPanel(QWidget):
    invite_requested = Signal(int)
    delete_linked_requested = Signal(object)
    toast = Signal(str, str)               # (message, kind) -> shown by the main window
    changed = None  # set by main window (callback)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.sid = None

        self.name = QLabel("")
        self.name.setObjectName("studentName")
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        self.btn_edit = QPushButton("Edit")
        self.btn_folder = QPushButton("Open folder")
        self.btn_sync = QPushButton("Sync PDFs")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setObjectName("danger")
        self.btn_edit.clicked.connect(self.edit_student)
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_sync.clicked.connect(self.sync)
        self.btn_delete.clicked.connect(self.delete_student)

        head = QBoxLayout(QBoxLayout.LeftToRight)
        self.head_layout = head
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.note = ElidedLabel("")
        self.note.setObjectName("muted")
        name_row = QHBoxLayout()
        name_row.setSpacing(12)
        name_row.addWidget(self.name)
        name_row.addWidget(self.note, 1, Qt.AlignBottom)
        titles.addLayout(name_row)
        titles.addWidget(self.sub)
        head.addLayout(titles, 1)
        head_buttons = QHBoxLayout()
        head_buttons.setSpacing(6)
        head_buttons.addStretch(1)
        for b in (self.btn_folder, self.btn_sync, self.btn_edit, self.btn_delete):
            head_buttons.addWidget(b, 0, Qt.AlignTop)
        head.addLayout(head_buttons)

        self.s_lessons = Stat("Lessons done")
        self.s_earned = Stat("Earned from lessons")
        self.s_paid = Stat("Paid")
        self.s_bonus = Stat("Bonuses")
        self.s_balance = Stat("Balance")
        # The stat cards wrap onto extra rows when the panel is narrow.
        stats = ReflowContainer((self.s_lessons, self.s_earned, self.s_paid, self.s_bonus, self.s_balance),
                                min_item_width=140)

        # lessons tab
        self.lessons = LessonDropTable(0, 6, 5)
        self.lessons.setHorizontalHeaderLabels(["When", "Length", "Value", "Status", "Invite status", "PDF"])
        self.lessons.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lessons.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.lessons.setShowGrid(False)
        self.lessons.verticalHeader().setVisible(False)
        self.lessons.verticalHeader().setDefaultSectionSize(38)
        self.lessons.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.lessons.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.lessons.setItemDelegate(FlashDelegate(self.lessons))
        self.lessons.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lessons.setWordWrap(False)
        self._lessons_hook = ResizeHook(self.lessons.viewport(), self.fit_lesson_columns)
        self.lessons.files_dropped.connect(self.attach_dropped_to_lesson)
        self.lessons.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lessons.customContextMenuRequested.connect(lambda pos: self.row_menu(self.lessons, pos, True))
        self.lessons.itemChanged.connect(self.inline_lesson)
        self.lessons.installEventFilter(self)
        self.lessons.viewport().installEventFilter(self)
        self.lessons.doubleClicked.connect(self.lesson_double_click)
        self.lessons.cellDoubleClicked.connect(lambda *_: QTimer.singleShot(0, self.select_editor_text))
        b_add = QPushButton("Add lesson")
        b_add.setObjectName("primary")
        b_add.clicked.connect(self.add_lesson)
        b_pdf = QPushButton("Open PDF")
        b_pdf.clicked.connect(self.open_pdf)
        b_edit = QPushButton("Edit")
        b_edit.clicked.connect(self.edit_lesson)
        b_del = QPushButton("Delete selected lessons")
        b_del.setObjectName("danger")
        b_del.clicked.connect(self.delete_lesson)
        lb = ReflowContainer((b_add, b_pdf, b_edit, b_del), min_item_width=90, spacing=8, left_align=True)
        drop_hint = QLabel("Click then Shift-click lesson rows to select several. Drop a PDF on a lesson row to attach it; drag a linked PDF from its PDF cell to another app.")
        drop_hint.setObjectName("muted")
        drop_hint.setWordWrap(True)
        drop_hint.setMinimumWidth(1)
        lessons_tab = QWidget()
        lt = QVBoxLayout(lessons_tab)
        lt.setContentsMargins(0, 10, 0, 0)
        lt.setSpacing(10)
        lt.addWidget(lb)
        lt.addWidget(drop_hint)
        lt.addWidget(self.lessons, 1)

        # payments tab
        self.payments = make_table(["Date", "Type", "Amount", "Note"], stretch_col=3)
        self.payments.setContextMenuPolicy(Qt.CustomContextMenu)
        self.payments.customContextMenuRequested.connect(lambda pos: self.row_menu(self.payments, pos, False))
        self.payments.itemChanged.connect(self.inline_payment)
        self.payments.installEventFilter(self)
        self.payments.viewport().installEventFilter(self)
        self.payments.cellDoubleClicked.connect(lambda *_: QTimer.singleShot(0, self.select_editor_text))
        p_add = QPushButton("Add payment")
        p_add.setObjectName("primary")
        p_add.clicked.connect(self.add_payment)
        p_paste = QPushButton("Paste payments")
        p_paste.clicked.connect(self.paste_payments)
        p_graph = QPushButton("View graph")
        p_graph.clicked.connect(self.show_graph)
        p_edit = QPushButton("Edit")
        p_edit.clicked.connect(self.edit_payment)
        p_del = QPushButton("Delete")
        p_del.setObjectName("danger")
        p_del.clicked.connect(self.delete_payment)
        pb = ReflowContainer((p_add, p_paste, p_graph, p_edit, p_del), min_item_width=90, spacing=8, left_align=True)
        pay_tab = QWidget()
        pt = QVBoxLayout(pay_tab)
        pt.setContentsMargins(0, 10, 0, 0)
        pt.setSpacing(10)
        pt.addWidget(pb)
        pt.addWidget(self.payments, 1)

        # notes tab
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        notes_tab = QWidget()
        nt = QVBoxLayout(notes_tab)
        nt.setContentsMargins(0, 14, 0, 0)
        nt.addWidget(self.notes)

        self.tabs = QTabWidget()
        self.tabs.addTab(lessons_tab, "Lessons")
        self.tabs.addTab(pay_tab, "Payments")
        self.tabs.addTab(notes_tab, "Notes")

        # Separate white card for reference sheets and other supporting PDFs.
        self.additional = make_table(["Additional PDFs"], stretch_col=0, pdf_drag_column=0)
        self.additional.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.additional.setToolTip("Drag a PDF from this list to another app, file, or website.")
        self.additional.setMaximumHeight(125)
        self.additional.setItemDelegate(FlashDelegate(self.additional))
        self.additional.doubleClicked.connect(self.open_additional_pdf)
        self.additional.setContextMenuPolicy(Qt.CustomContextMenu)
        self.additional.customContextMenuRequested.connect(self.additional_menu)
        self.btn_additional = QPushButton("Add additional PDF")
        self.btn_additional.clicked.connect(self.choose_additional_pdfs)
        add_head = QHBoxLayout()
        add_head.addWidget(QLabel("Additional PDFs"), 1)
        add_head.addWidget(self.btn_additional)
        self.additional_card = QFrame()
        self.additional_card.setObjectName("card")
        ac = QVBoxLayout(self.additional_card)
        ac.addLayout(add_head)
        ac.addWidget(self.additional)

        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)
        bl.addLayout(head)
        bl.addWidget(stats)
        bl.addWidget(self.tabs, 1)
        bl.addWidget(self.additional_card)

        self.empty = QLabel("Select a student, or drag a folder of PDFs onto this window.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 0, 0, 0)
        lay.addWidget(self.body)
        lay.addWidget(self.empty)
        self.load(None)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < 640                 # buttons drop under the student's name
        self.head_layout.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)

    # ---- loading
    def load(self, sid):
        self.sid = sid
        s = self.store.student(sid) if sid else None
        self.body.setVisible(s is not None)
        self.empty.setVisible(s is None)
        if not s:
            return
        st = self.store.stats(sid)
        self.name.setText(s["name"])
        nl = note_line(s["notes"])
        self.note.set_full_text(("\u2013  " + nl) if nl else "")
        self.note.setVisible(bool(nl))
        folder_txt = s["folder"] if s["folder"] else "No folder linked"
        email_txt = f"   ·   {s['email']}" if s["email"] else ""
        self.sub.setText(f"{money(s['rate'])} / hour   ·   {folder_txt}{email_txt}")
        self.btn_folder.setEnabled(bool(s["folder"]))
        self.btn_sync.setEnabled(bool(s["folder"]))
        self.s_lessons.set(str(st["done"]))
        self.s_earned.set(money(st["earned"]))
        self.s_paid.set(money(st["paid"] - st["refunds"]))
        self.s_bonus.set(money(st["bonuses"]))
        bal = st["balance"]
        if abs(bal) < 0.005:
            self.s_balance.set("Settled")
        elif bal < 0:
            self.s_balance.set(f"Owes {money(-bal)}", BAD)
        else:
            self.s_balance.set(f"Credit {money(bal)}", GOOD)
        self.notes.setText(s["notes"] or "No notes yet. Use Edit to add some.")
        extras = self.store.additional_pdfs(sid)
        self.additional.setRowCount(len(extras))
        for r, p in enumerate(extras):
            it = cell(Path(p["pdf"]).name, data=p["id"])
            it.setToolTip(p["pdf"])
            it.setData(Qt.UserRole + 1, p["pdf"])
            self.additional.setItem(r, 0, it)

        # lessons
        rows = self.store.lessons(sid)
        self.lessons.blockSignals(True)
        now = datetime.now()
        self.lessons.setRowCount(len(rows))
        for r, l in enumerate(rows):
            eff = effective_status(l)
            future = datetime.strptime(l["start"], DT_FMT) >= now and l["status"] != "cancelled"
            label = "Upcoming" if future else eff.capitalize()
            colour = ACCENT if future else (MUTED if eff == "cancelled" else None)
            pdf_txt = ""
            if l["pdf"]:
                pdf_txt = Path(l["pdf"]).name if Path(l["pdf"]).exists() else "⚠ missing: " + Path(l["pdf"]).name
            self.lessons.setItem(r, 0, cell(nice_dt(l["start"]), colour, data=l["id"]))
            self.lessons.setItem(r, 1, cell(f"{l['duration']} min", colour))
            val = money(lesson_value(l)) if eff != "cancelled" else "–"
            self.lessons.setItem(r, 2, cell(val, colour))
            self.lessons.setItem(r, 3, cell(label, colour, bold=future))
            inv = invite_status(l)
            self.lessons.setItem(r, 4, cell(inv, invite_colour(inv)))
            self.lessons.item(r, 4).setFlags(self.lessons.item(r, 4).flags() & ~Qt.ItemIsEditable)
            self.lessons.setItem(r, 5, cell(pdf_txt, BAD if pdf_txt.startswith("⚠") else colour))
            self.lessons.item(r, 5).setFlags(self.lessons.item(r, 5).flags() & ~Qt.ItemIsEditable)
            self.lessons.item(r, 5).setData(Qt.UserRole + 1, l["pdf"] or "")
        self.lessons.blockSignals(False)
        self.fit_lesson_columns()

        # payments
        prow = self.store.payments(sid)
        self.payments.blockSignals(True)
        self.payments.setRowCount(len(prow))
        for r, p in enumerate(prow):
            d = datetime.strptime(p["date"], "%Y-%m-%d")
            kind = {"payment": "Payment", "bonus": "Bonus", "refund": "Refund"}[p["kind"]]
            colour = GOOD if p["kind"] == "bonus" else (BAD if p["kind"] == "refund" else None)
            sign = "-" if p["kind"] == "refund" else ""
            self.payments.setItem(r, 0, cell(f"{d:%a} {d.day} {d:%b %Y}", data=p["id"]))
            self.payments.setItem(r, 1, cell(kind, colour))
            self.payments.setItem(r, 2, cell(sign + money(p["amount"]), colour))
            self.payments.setItem(r, 3, cell(p["note"]))
        self.payments.blockSignals(False)

    def notify(self):
        if self.changed:
            self.changed()

    def row_menu(self, table, pos, lesson):
        from PySide6.QtWidgets import QMenu
        row = table.rowAt(pos.y())
        if row < 0:
            return
        if not any(index.row() == row for index in table.selectionModel().selectedRows()):
            table.selectRow(row)
        menu = QMenu(self)
        edit = menu.addAction("Edit")
        invite = menu.addAction("Send invite…") if lesson else None
        delete = menu.addAction("Delete")
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen == edit:
            (self.edit_lesson if lesson else self.edit_payment)()
        elif lesson and chosen == invite:
            lid = self.selected_id(table)
            if lid is not None:
                self.invite_requested.emit(lid)
        elif chosen == delete:
            (self.delete_lesson if lesson else self.delete_payment)()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            if self.lessons.hasFocus() and self.selected_id(self.lessons) is not None:
                self.delete_lesson(); return
            if self.payments.hasFocus() and self.selected_id(self.payments) is not None:
                self.delete_payment(); return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Delete:
            if obj is self.lessons.viewport() and self.selected_id(self.lessons) is not None:
                self.delete_lesson(); return True
            if obj is self.payments.viewport() and self.selected_id(self.payments) is not None:
                self.delete_payment(); return True
        return super().eventFilter(obj, event)

    def attach_dropped_to_lesson(self, paths, lesson_id):
        lesson = self.store.lesson(lesson_id)
        if not lesson or lesson["student_id"] != self.sid:
            return
        student = self.store.student(self.sid)
        saved = []
        for source in paths[:1]:  # A lesson record has one PDF field.
            source_path = Path(source)
            if not source_path.is_file():
                continue
            final_path = source_path
            temp_root = getattr(self, "download_temp_dir", "")
            try:
                is_temp = temp_root and os.path.commonpath([str(source_path.resolve()), str(Path(temp_root).resolve())]) == str(Path(temp_root).resolve())
            except (ValueError, OSError):
                is_temp = False
            if is_temp:
                target_root = Path(student["folder"]) if student and student["folder"] and Path(student["folder"]).is_dir() else Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "TutoringOrganiser" / "OnlineResources"
                target_root.mkdir(parents=True, exist_ok=True)
                target = target_root / source_path.name
                if target.exists() and target.resolve() != source_path.resolve():
                    target = target_root / f"{source_path.stem}_{uuid.uuid4().hex[:8]}{source_path.suffix}"
                shutil.copy2(source_path, target)
                final_path = target
            saved.append(str(final_path.resolve()))
        if not saved:
            return
        self.store.save_lesson(lesson_id, lesson["student_id"], lesson["start"], lesson["duration"],
                               lesson["rate"], lesson["status"], saved[0], lesson["notes"])
        self.load(self.sid)
        if self.changed:
            self.changed()
        if len(paths) > 1:
            QMessageBox.information(self, "PDF attached", "One lesson can hold one PDF. The first selected file was attached.")

    # Lessons table columns, most important first. Columns that don't fit are hidden rather than squashing "When".
    LESSON_COL_NEED = {0: 190, 3: 100, 5: 160, 2: 80, 1: 80, 4: 115}
    LESSON_COL_ORDER = (0, 3, 5, 2, 1, 4)

    def fit_lesson_columns(self):
        t = self.lessons
        width = t.viewport().width()
        if width <= 0:
            return
        shown, used = [], 0
        for col in self.LESSON_COL_ORDER:
            need = self.LESSON_COL_NEED[col]
            if col != 0 and used + need > width:
                break
            shown.append(col)
            used += need
        h = t.horizontalHeader()
        for col in range(t.columnCount()):
            t.setColumnHidden(col, col not in shown)
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        if 5 in shown:
            h.setSectionResizeMode(5, QHeaderView.Stretch)



    # ---- highlight where a dropped PDF landed
    def flash_assignment(self, lesson_id=None, pdf_path=None):
        if lesson_id is not None:
            self.tabs.setCurrentIndex(0)
            for r in range(self.lessons.rowCount()):
                it = self.lessons.item(r, 0)
                if it is not None and it.data(Qt.UserRole) == lesson_id:
                    self.lessons.scrollToItem(it)
                    flash_table_rows(self.lessons, [r])
                    return
        if pdf_path:
            for r in range(self.additional.rowCount()):
                it = self.additional.item(r, 0)
                if it is not None and norm(it.data(Qt.UserRole + 1) or "") == norm(pdf_path):
                    self.additional.scrollToItem(it)
                    flash_table_rows(self.additional, [r])
                    return

    def select_editor_text(self):
        editor = self.focusWidget()
        if isinstance(editor, QLineEdit):
            editor.selectAll()

    def inline_lesson(self, item):
        if not self.sid or item.column() not in (0, 1, 2, 3):
            return
        lid = self.lessons.item(item.row(), 0).data(Qt.UserRole)
        row = self.store.con.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
        try:
            start, duration, rate, status = row["start"], row["duration"], row["rate"], row["status"]
            if item.column() == 0:
                parsed = None
                for fmt in ("%a %d %b %Y, %I:%M %p", "%a %d %b %Y"):
                    try: parsed = datetime.strptime(item.text().strip(), fmt); break
                    except ValueError: pass
                if parsed is None: raise ValueError("Use the displayed date format.")
                start = parsed.strftime(DT_FMT)
            elif item.column() == 1:
                duration = int(item.text().lower().replace("min", "").strip())
                if not 5 <= duration <= 600: raise ValueError("Length must be 5–600 minutes.")
            elif item.column() == 2:
                amount = float(item.text().replace("$", "").replace(",", "").strip())
                rate = amount * 60 / duration
                if rate < 0: raise ValueError("Value cannot be negative.")
            else:
                v = item.text().strip().lower()
                status = {"upcoming":"scheduled", "scheduled":"scheduled", "completed":"completed", "cancelled":"cancelled"}[v]
            self.store.save_lesson(lid, self.sid, start, duration, rate, status, row["pdf"], row["notes"])
            self.notify()
        except (ValueError, KeyError):
            QMessageBox.warning(self, "Invalid value", "That edit is not valid. The original value has been restored.")
            self.load(self.sid)

    def inline_payment(self, item):
        if not self.sid: return
        pid = self.payments.item(item.row(), 0).data(Qt.UserRole)
        row = self.store.con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        date, amount, kind, note = row["date"], row["amount"], row["kind"], row["note"]
        try:
            if item.column() == 0:
                parsed = None
                for fmt in ("%a %d %b %Y", "%Y-%m-%d"):
                    try: parsed = datetime.strptime(item.text().strip(), fmt); break
                    except ValueError: pass
                if parsed is None: raise ValueError()
                date = parsed.strftime("%Y-%m-%d")
            elif item.column() == 1:
                kind = {"payment":"payment", "bonus":"bonus", "bonus / tip":"bonus", "refund":"refund"}[item.text().strip().lower()]
            elif item.column() == 2:
                amount = float(item.text().replace("$", "").replace(",", "").replace("-", "").strip())
                if amount <= 0: raise ValueError()
            else: note = item.text().strip()
            self.store.save_payment(pid, self.sid, date, amount, kind, note)
            self.notify()
        except (ValueError, KeyError):
            QMessageBox.warning(self, "Invalid value", "That edit is not valid. The original value has been restored.")
            self.load(self.sid)

    def choose_additional_pdfs(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose additional PDFs", "", "PDF files (*.pdf)")
        if files:
            self.store.add_additional_pdfs(self.sid, files); self.load(self.sid)

    def open_additional_pdf(self):
        rows = self.additional.selectionModel().selectedRows()
        if not rows: return
        pid = self.additional.item(rows[0].row(), 0).data(Qt.UserRole)
        row = self.store.con.execute("SELECT pdf FROM additional_pdfs WHERE id=?", (pid,)).fetchone()
        if row and Path(row["pdf"]).exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(row["pdf"]))
        elif row: QMessageBox.warning(self, "File missing", f"Can't find:\n{row['pdf']}")

    def additional_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        row = self.additional.rowAt(pos.y())
        if row < 0: return
        self.additional.selectRow(row)
        menu = QMenu(self); op = menu.addAction("Open"); rm = menu.addAction("Remove")
        chosen = menu.exec(self.additional.viewport().mapToGlobal(pos))
        pid = self.additional.item(row, 0).data(Qt.UserRole)
        if chosen == op: self.open_additional_pdf()
        elif chosen == rm:
            self.store.con.execute("DELETE FROM additional_pdfs WHERE id=?", (pid,)); self.store.con.commit(); self.load(self.sid)

    # ---- helpers
    @staticmethod
    def selected_id(table):
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        return table.item(rows[0].row(), 0).data(Qt.UserRole)

    def need_row(self, table, what):
        i = self.selected_id(table)
        if i is None:
            QMessageBox.information(self, "Select one", f"Click a {what} first.")
        return i

    # ---- student actions
    def edit_student(self):
        s = self.store.student(self.sid)
        dlg = StudentDialog(self, s)
        if dlg.exec():
            self.store.save_student(self.sid, *dlg.values())
            self.notify()

    def delete_student(self):
        s = self.store.student(self.sid)
        ok = QMessageBox.question(
            self, "Delete student",
            f"Delete {s['name']} and all their lessons and payments?\n\nYour PDF files are not touched.")
        if ok == QMessageBox.Yes:
            self.store.delete_student(self.sid)
            self.sid = None
            self.notify()

    def open_folder(self):
        s = self.store.student(self.sid)
        if s["folder"] and Path(s["folder"]).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(s["folder"]))
        else:
            QMessageBox.warning(self, "Folder not found", "That folder no longer exists. Use Edit to relink it.")

    def sync(self):
        n = self.store.sync_pdfs(self.sid)
        self.notify()
        QMessageBox.information(self, "Sync PDFs",
                                f"Linked {n} new PDF(s) from the folder." if n else "No new PDFs found.")

    # ---- lesson actions
    def add_lesson(self):
        dlg = LessonDialog(self, self.store.student(self.sid))
        if dlg.exec():
            self.store.save_lesson(None, self.sid, *dlg.values())
            self.notify()

    def edit_lesson(self):
        lid = self.need_row(self.lessons, "lesson")
        if lid is None:
            return
        row = self.store.con.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
        dlg = LessonDialog(self, self.store.student(self.sid), row)
        if dlg.exec():
            self.store.save_lesson(lid, self.sid, *dlg.values())
            self.notify()

    def delete_lesson(self):
        lids = [self.lessons.item(index.row(), 0).data(Qt.UserRole)
                for index in self.lessons.selectionModel().selectedRows()
                if self.lessons.item(index.row(), 0)]
        if not lids:
            lid = self.need_row(self.lessons, "lesson")
            if lid is not None:
                lids = [lid]
        if not lids:
            return
        rows = [self.store.lesson(lid) for lid in lids]
        rows = [row for row in rows if row]
        if not rows:
            return
        linked_count = sum(needs_calendar_delete(row) for row in rows)
        prompt = f"Delete {len(rows)} selected lesson(s)? The PDF files are not touched."
        if linked_count:
            prompt += f"\n\nThe {linked_count} linked Google Calendar booking(s) will also be removed when available."
        if QMessageBox.question(self, "Delete lesson(s)", prompt) != QMessageBox.Yes:
            return
        linked_ids = [row["id"] for row in rows if needs_calendar_delete(row)]
        if linked_ids:
            self.delete_linked_requested.emit([row["id"] for row in rows])
        else:
            for row in rows:
                self.store.delete_lesson(row["id"])
            self.notify()

    def open_pdf(self):
        lid = self.need_row(self.lessons, "lesson")
        if lid is None:
            return
        row = self.store.con.execute("SELECT pdf FROM lessons WHERE id=?", (lid,)).fetchone()
        if not row["pdf"]:
            QMessageBox.information(self, "No PDF", "This lesson has no PDF linked. Use Edit to attach one.")
        elif not Path(row["pdf"]).exists():
            QMessageBox.warning(self, "File missing", f"Can't find:\n{row['pdf']}\n\nIt may have been moved or renamed.")
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(row["pdf"]))

    def lesson_double_click(self, index):
        lid = self.selected_id(self.lessons)
        if index.column() == 5:
            row = self.store.con.execute("SELECT pdf FROM lessons WHERE id=?", (lid,)).fetchone()
            if row and row["pdf"] and Path(row["pdf"]).exists():
                self.open_pdf()
            elif row and row["pdf"]:
                QMessageBox.warning(self, "File missing", f"Can't find:\n{row['pdf']}")
            else:
                self.open_pdf()
        else:
            self.lessons.editItem(self.lessons.item(index.row(), index.column()))

    # ---- payment actions
    def add_payment(self):
        dlg = PaymentDialog(self)
        if dlg.exec():
            self.store.save_payment(None, self.sid, *dlg.values())
            self.notify()

    def show_graph(self):
        if not self.sid:
            return
        if not HAVE_CHARTS:
            QMessageBox.warning(self, "Charts not installed",
                                "The graph needs Qt Charts. Run:  pip install PySide6-Addons")
            return
        lessons, payments = self.store.graph_data(self.sid)
        if not lessons and not payments:
            QMessageBox.information(self, "Nothing to graph yet",
                                    "This student has no completed lessons or payments yet.")
            return
        s, st = self.store.student(self.sid), self.store.stats(self.sid)
        bal = st["balance"]
        state = "settled" if abs(bal) < 0.005 else (f"owes {money(-bal)}" if bal < 0 else f"credit {money(bal)}")
        summary = f"Lesson cost {money(st['earned'])}  ·  Paid {money(st['paid'] - st['refunds'])}  ·  {state}"
        GraphDialog(self, s["name"], lessons, payments, summary).exec()

    def paste_payments(self):
        if not self.sid:
            return
        name = self.store.student(self.sid)["name"]
        dlg = PasteDialog(self, f"Paste payments for {name}",
                          f"These payments will be added to {name}. The pasted list has no student names, "
                          "so make sure the right student is selected. Two layouts work: a lesson list "
                          "(month/year, weekday/day, time, $amount; each can be on its own line) or "
                          "bank-style lines (date, payer, amount). "
                          "$0 lines and upcoming lessons are skipped.",
                          PAYMENT_PLACEHOLDER + "\n\n(or bank style)\n\n" + PAYMENT_PLACEHOLDER_BANK)
        if not dlg.exec():
            return
        items, warnings = parse_payments_paste(dlg.text())
        if not items:
            QMessageBox.information(self, "Nothing found", "I couldn't find any payments in that text.\n\n"
                                    + "\n".join(warnings[:5]))
            return
        plan = self.store.plan_payments(self.sid, items)
        rows = []
        for r in plan:
            d = datetime.strptime(r["date"], "%Y-%m-%d")
            rows.append((r["kind"], [f"{d:%a} {d.day} {d:%b %Y}", money(r["amount"]), r["lesson"], r["note"]]))
        prev = PastePreviewDialog(self, "Review pasted payments", ["Date", "Amount", "Lesson time", "Note"], rows, warnings)
        if not prev.exec():
            return
        n = self.store.apply_payment_plan(self.sid, plan, prev.action == "add")
        self.notify()
        QMessageBox.information(self, "Payments added", f"Added {n} payment(s) to {name}.")

    def edit_payment(self):
        pid = self.need_row(self.payments, "payment")
        if pid is None:
            return
        row = self.store.con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        dlg = PaymentDialog(self, row)
        if dlg.exec():
            self.store.save_payment(pid, self.sid, *dlg.values())
            self.notify()

    def delete_payment(self):
        pid = self.need_row(self.payments, "payment")
        if pid is None:
            return
        if QMessageBox.question(self, "Delete payment", "Delete this payment entry?") == QMessageBox.Yes:
            self.store.delete_payment(pid)
            self.notify()
