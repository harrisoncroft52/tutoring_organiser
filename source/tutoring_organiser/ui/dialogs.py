"""Pop-up dialogs for students, lessons, payments and pasted data."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from ..config import DT_FMT
from ..invites import invalid_emails, parse_emails
from .theme import ACCENT, BAD, GOOD, MUTED
from .widgets import cell, make_table


class StudentDialog(QDialog):
    def __init__(self, parent, student=None):
        super().__init__(parent)
        self.setWindowTitle("Edit student" if student else "New student")
        self.setMinimumWidth(440)
        self.name = QLineEdit(student["name"] if student else "")
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 10000)
        self.rate.setPrefix("$")
        self.rate.setSuffix(" / hour")
        self.rate.setDecimals(2)
        self.rate.setValue(student["rate"] if student else 0)
        self.folder = QLineEdit(student["folder"] if student else "")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_folder)
        frow = QHBoxLayout()
        frow.addWidget(self.folder, 1)
        frow.addWidget(browse)
        self.notes = QTextEdit(student["notes"] if student else "")
        self.notes.setFixedHeight(80)
        self.email = QLineEdit(student["email"] if student and student["email"] else "")
        self.email.setPlaceholderText("student@example.com (optional)")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.addRow("Name", self.name)
        form.addRow("Email", self.email)
        form.addRow("Rate", self.rate)
        form.addRow("PDF folder", frow)
        form.addRow("Notes", self.notes)
        hint = QLabel("Setting a rate on a student whose lessons are at $0 (e.g. imported ones) fills those in.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(buttons)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the student's PDF folder", self.folder.text())
        if d:
            self.folder.setText(d)
            if not self.name.text().strip():
                self.name.setText(Path(d).name)

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Name needed", "Please enter a name.")
            return
        bad = invalid_emails(parse_emails(self.email.text()))
        if bad:
            QMessageBox.warning(self, "Check the email address",
                                f"This doesn't look like an email address:\n{', '.join(bad)}")
            return
        super().accept()

    def values(self):
        return (self.name.text().strip(), self.rate.value(), self.folder.text().strip(),
                self.notes.toPlainText().strip(), ", ".join(parse_emails(self.email.text())))


class LessonDialog(QDialog):
    def __init__(self, parent, student, lesson=None):
        super().__init__(parent)
        self.setWindowTitle("Edit lesson" if lesson else "New lesson")
        self.setMinimumWidth(460)
        self.when = QDateTimeEdit()
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("ddd d MMM yyyy, h:mm ap")
        if lesson:
            d = datetime.strptime(lesson["start"], DT_FMT)
            self.when.setDateTime(QDateTime(d.year, d.month, d.day, d.hour, d.minute, 0))
        else:
            now = QDateTime.currentDateTime()
            self.when.setDateTime(QDateTime(now.date(), now.time().addSecs(-now.time().second() - 60 * now.time().minute())))
        self.duration = QSpinBox()
        self.duration.setRange(5, 600)
        self.duration.setSingleStep(15)
        self.duration.setSuffix(" min")
        self.duration.setValue(lesson["duration"] if lesson else 60)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 10000)
        self.rate.setPrefix("$")
        self.rate.setSuffix(" / hour")
        self.rate.setValue(lesson["rate"] if lesson else student["rate"])
        self.status = QComboBox()
        for label, key in (("Scheduled", "scheduled"), ("Completed", "completed"), ("Cancelled", "cancelled")):
            self.status.addItem(label, key)
        if lesson:
            self.status.setCurrentIndex(self.status.findData(lesson["status"]))
        self.pdf = QLineEdit(lesson["pdf"] if lesson else "")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.pick_pdf)
        prow = QHBoxLayout()
        prow.addWidget(self.pdf, 1)
        prow.addWidget(browse)
        self.notes = QTextEdit(lesson["notes"] if lesson else "")
        self.notes.setFixedHeight(70)

        form = QFormLayout()
        form.addRow("When", self.when)
        form.addRow("Length", self.duration)
        form.addRow("Rate", self.rate)
        form.addRow("Status", self.status)
        form.addRow("PDF", prow)
        form.addRow("Notes", self.notes)
        hint = QLabel("Cancelled lessons are never charged. 'Scheduled' lessons count as completed once their time has passed.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(buttons)

    def pick_pdf(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose lesson PDF", self.pdf.text(), "PDF files (*.pdf)")
        if f:
            self.pdf.setText(f)

    def values(self):
        return (self.when.dateTime().toString("yyyy-MM-dd HH:mm"), self.duration.value(),
                self.rate.value(), self.status.currentData(), self.pdf.text().strip(),
                self.notes.toPlainText().strip())


class CalendarLessonDialog(QDialog):
    """Confirm a lesson dragged out on the calendar: pick the student, tweak time/length, optionally create the Google event."""
    def __init__(self, parent, students, start, duration, google_ready):
        super().__init__(parent)
        self.setWindowTitle("New lesson")
        self.setMinimumWidth(440)
        self.student = QComboBox()
        for s in students:
            self.student.addItem(s["name"], (s["id"], s["rate"]))
        self.when = QDateTimeEdit(QDateTime(start.year, start.month, start.day, start.hour, start.minute, 0))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("ddd d MMM yyyy, h:mm ap")
        self.duration = QSpinBox()
        self.duration.setRange(5, 600)
        self.duration.setSingleStep(15)
        self.duration.setSuffix(" min")
        self.duration.setValue(duration)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 10000)
        self.rate.setPrefix("$")
        self.rate.setSuffix(" / hour")
        self.student.currentIndexChanged.connect(lambda _: self.rate.setValue(self.student.currentData()[1]))
        self.rate.setValue(self.student.currentData()[1])
        self.push = QCheckBox("Also create the Google Calendar event (with a Meet link)")
        self.push.setChecked(google_ready)
        self.push.setEnabled(google_ready)
        if not google_ready:
            self.push.setToolTip("Google isn't connected. The lesson will be saved in the tracker only.")
        form = QFormLayout()
        form.addRow("Student", self.student)
        form.addRow("When", self.when)
        form.addRow("Length", self.duration)
        form.addRow("Rate", self.rate)
        note = QLabel("Google won't email the student from here. Use Send invite… on the lesson afterwards.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.Save).setText("Create lesson")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(form)
        lay.addWidget(self.push)
        lay.addWidget(note)
        lay.addWidget(buttons)

    def values(self):
        return (self.student.currentData()[0], self.when.dateTime().toString("yyyy-MM-dd HH:mm"),
                self.duration.value(), self.rate.value(), self.push.isChecked())


class PaymentDialog(QDialog):
    def __init__(self, parent, payment=None):
        super().__init__(parent)
        self.setWindowTitle("Edit payment" if payment else "New payment")
        self.setMinimumWidth(400)
        self.date = QDateTimeEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd d MMM yyyy")
        if payment:
            self.date.setDate(QDateTime.fromString(payment["date"], "yyyy-MM-dd").date())
        else:
            self.date.setDate(QDateTime.currentDateTime().date())
        self.kind = QComboBox()
        for label, key in (("Payment", "payment"), ("Bonus / tip", "bonus"), ("Refund (money returned)", "refund")):
            self.kind.addItem(label, key)
        if payment:
            self.kind.setCurrentIndex(self.kind.findData(payment["kind"]))
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 100000)
        self.amount.setPrefix("$")
        self.amount.setValue(payment["amount"] if payment else 0)
        self.note = QLineEdit(payment["note"] if payment else "")

        form = QFormLayout()
        form.addRow("Date", self.date)
        form.addRow("Type", self.kind)
        form.addRow("Amount", self.amount)
        form.addRow("Note", self.note)
        hint = QLabel("Enter amounts as positive numbers. A prepaid lesson that gets cancelled: log the payment, "
                      "then either leave it as credit or log a refund.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(buttons)

    def accept(self):
        if self.amount.value() <= 0:
            QMessageBox.warning(self, "Amount needed", "Please enter an amount above zero.")
            return
        super().accept()

    def values(self):
        return (self.date.date().toString("yyyy-MM-dd"), self.amount.value(),
                self.kind.currentData(), self.note.text().strip())


CALENDAR_PLACEHOLDER = """30
Oct 2026, Fri
3 – 4pm
Tutoring Lorenzo
4:30 – 6pm
Tutoring Noah

2
Nov 2026, Mon
5:30 – 6:30pm
Tutoring Zayne"""


PAYMENT_PLACEHOLDER_BANK = """12 Oct 2026
D T & K E Carpenter Monday Carpenter Ben
116.00
17 Sep 2026
D T & K E Carpenter Monday Carpenter Ben
174.00"""


PAYMENT_PLACEHOLDER = """November2026
Tue10
16:00 - 17:00
$45
Past lessons
October2026
Tue27
16:00 - 17:00
$45
Tue20
16:00 - 17:00
$0
September2026
Tue22
16:00 - 17:00
$0"""


class PasteDialog(QDialog):
    """A big box to paste text into."""
    def __init__(self, parent, title, hint, placeholder):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(540, 540)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Example:\n\n" + placeholder)
        note = QLabel(hint)
        note.setObjectName("muted")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        go = buttons.addButton("Preview", QDialogButtonBox.AcceptRole)
        go.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)
        lay.addWidget(note)
        lay.addWidget(self.edit, 1)
        lay.addWidget(buttons)

    def text(self):
        return self.edit.toPlainText()


class PastePreviewDialog(QDialog):
    """Shows what a paste would do. Duplicates are flagged and you choose to ignore or add them."""
    LABELS = {"new": ("New", GOOD), "dup": ("Duplicate", BAD),
              "merge": ("Fills in time", ACCENT), "skip": ("Skipped", MUTED)}

    def __init__(self, parent, title, headers, rows, warnings=(), offer_create=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(840, 560)
        self.action = None
        self.create_missing = True
        counts = {k: sum(1 for kind, _ in rows if kind == k) for k in self.LABELS}

        table = make_table(["Result"] + list(headers), stretch_col=len(headers))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setRowCount(len(rows))
        for r, (kind, cells) in enumerate(rows):
            label, colour = self.LABELS[kind]
            table.setItem(r, 0, cell(label, colour, bold=True))
            for c, text in enumerate(cells, 1):
                table.setItem(r, c, cell(text, MUTED if kind == "skip" else None))

        parts = []
        if counts["new"]:
            parts.append(f"{counts['new']} new")
        if counts["merge"]:
            parts.append(f"{counts['merge']} will fill in the time on an existing PDF lesson")
        if counts["dup"]:
            parts.append(f"{counts['dup']} duplicate(s)")
        if counts["skip"]:
            parts.append(f"{counts['skip']} skipped")
        summary = QLabel(" · ".join(parts) or "Nothing to add.")
        summary.setObjectName("title")
        summary.setStyleSheet("font-size: 17px;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(summary)
        if warnings:
            w = QLabel("Check these:\n" + "\n".join(warnings[:6]) + ("\n…" if len(warnings) > 6 else ""))
            w.setObjectName("muted")
            w.setWordWrap(True)
            lay.addWidget(w)
        lay.addWidget(table, 1)

        self.chk = None
        if offer_create:
            self.chk = QCheckBox("Create students that aren't in the app yet (rate $0, set it afterwards)")
            self.chk.setChecked(True)
            lay.addWidget(self.chk)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        can_add = counts["new"] + counts["merge"] > 0
        if counts["dup"]:
            ignore = QPushButton("Ignore duplicates")
            ignore.setObjectName("primary")
            ignore.setEnabled(can_add)
            ignore.clicked.connect(lambda: self.finish("skip"))
            add_dups = QPushButton("Add duplicates")
            add_dups.clicked.connect(lambda: self.finish("add"))
            row.addWidget(add_dups)
            row.addWidget(ignore)
        else:
            ok = QPushButton("Add")
            ok.setObjectName("primary")
            ok.setEnabled(can_add)
            ok.clicked.connect(lambda: self.finish("skip"))
            row.addWidget(ok)
        lay.addLayout(row)

    def finish(self, action):
        self.action = action
        self.create_missing = self.chk.isChecked() if self.chk else True
        self.accept()


def normalise_url(text):
    """'nzqa.govt.nz' -> 'https://nzqa.govt.nz/'. Returns '' if it can't be a web address."""
    text = (text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    url = QUrl(text)
    if not url.isValid() or url.scheme() not in ("http", "https") or "." not in url.host():
        return ""
    if not url.path():
        url.setPath("/")
    return url.toString()


class ManageSitesDialog(QDialog):
    """Add, remove, rename and reorder the websites on the Online resources page."""
    def __init__(self, parent, sites, defaults, current_page=None):
        super().__init__(parent)
        self.setWindowTitle("Manage websites")
        self.setMinimumSize(720, 460)
        self.defaults = list(defaults)
        self.current_page = current_page            # (title, url) of the page open in the browser, if any

        self.table = make_table(["Name", "Web address"], stretch_col=1)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.fill(sites)

        b_rename = QPushButton("Rename")
        b_rename.clicked.connect(lambda: self.edit_selected(0))
        b_address = QPushButton("Change address")
        b_address.clicked.connect(lambda: self.edit_selected(1))
        b_remove = QPushButton("Remove")
        b_remove.setObjectName("danger")
        b_remove.clicked.connect(self.remove_selected)
        b_up = QPushButton("Move up")
        b_up.clicked.connect(lambda: self.move_selected(-1))
        b_down = QPushButton("Move down")
        b_down.clicked.connect(lambda: self.move_selected(1))
        b_defaults = QPushButton("Restore defaults")
        b_defaults.clicked.connect(self.restore_defaults)
        side = QVBoxLayout()
        for b in (b_rename, b_address, b_remove, b_up, b_down):
            side.addWidget(b)
        side.addStretch(1)
        side.addWidget(b_defaults)
        middle = QHBoxLayout()
        middle.addWidget(self.table, 1)
        middle.addLayout(side)

        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("Name")
        self.new_url = QLineEdit()
        self.new_url.setPlaceholderText("Web address, e.g. nzqa.govt.nz")
        self.new_url.returnPressed.connect(self.add_site)
        b_add = QPushButton("Add")
        b_add.setObjectName("primary")
        b_add.clicked.connect(self.add_site)
        add_row = QHBoxLayout()
        add_row.addWidget(self.new_name, 2)
        add_row.addWidget(self.new_url, 3)
        add_row.addWidget(b_add)
        if current_page and current_page[1]:
            b_current = QPushButton("Add open page")
            b_current.setToolTip(current_page[1])
            b_current.clicked.connect(self.add_current_page)
            add_row.addWidget(b_current)

        self.msg = QLabel("Double-click a name or address to edit it. The first site opens when the page loads.")
        self.msg.setObjectName("muted")
        self.msg.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(middle, 1)
        lay.addWidget(QLabel("Add a website"))
        lay.addLayout(add_row)
        lay.addWidget(self.msg)
        lay.addWidget(buttons)
        self.result_sites = None

    # ---- table helpers
    def fill(self, sites):
        self.table.setRowCount(0)
        for name, url in sites:
            self.append_row(name, url)

    def append_row(self, name, url):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, cell(name, bold=True))
        self.table.setItem(r, 1, cell(url))
        return r

    def rows(self):
        out = []
        for r in range(self.table.rowCount()):
            name, url = self.table.item(r, 0), self.table.item(r, 1)
            out.append(((name.text() if name else "").strip(), (url.text() if url else "").strip()))
        return out

    def say(self, text, bad=False):
        self.msg.setText(text)
        self.msg.setStyleSheet(f"color: {BAD};" if bad else "")

    def selected_row(self):
        r = self.table.currentRow()
        if r < 0:
            self.say("Select a website in the list first.", True)
        return r

    # ---- actions
    def edit_selected(self, column):
        r = self.selected_row()
        if r >= 0:
            self.table.setCurrentCell(r, column)
            self.table.editItem(self.table.item(r, column))

    def remove_selected(self):
        r = self.selected_row()
        if r >= 0:
            name = self.table.item(r, 0).text()
            self.table.removeRow(r)
            self.say(f"Removed {name}. Press Save to keep the change.")

    def move_selected(self, step):
        r = self.selected_row()
        to = r + step
        if r < 0 or not 0 <= to < self.table.rowCount():
            return
        rows = self.rows()
        rows[r], rows[to] = rows[to], rows[r]
        self.fill(rows)
        self.table.selectRow(to)

    def restore_defaults(self):
        if QMessageBox.question(self, "Restore defaults",
                                "Replace this list with the three built-in websites?") == QMessageBox.Yes:
            self.fill(self.defaults)
            self.say("Built-in websites restored. Press Save to keep them.")

    def _add(self, name, address):
        url = normalise_url(address)
        if not url:
            return self.say("That doesn't look like a web address. Try something like nzqa.govt.nz", True)
        name = name.strip() or QUrl(url).host().removeprefix("www.")
        if name.lower() in (n.lower() for n, _ in self.rows()):
            return self.say(f"There's already a website called '{name}'. Choose another name.", True)
        r = self.append_row(name, url)
        self.table.selectRow(r)
        self.say(f"Added {name}. Press Save to keep it.")
        return True

    def add_site(self):
        if self._add(self.new_name.text(), self.new_url.text()):
            self.new_name.clear()
            self.new_url.clear()
            self.new_name.setFocus()

    def add_current_page(self):
        title, url = self.current_page
        self._add(self.new_name.text() or title, url)

    def save(self):
        clean, seen = [], set()
        for i, (name, address) in enumerate(self.rows(), 1):
            url = normalise_url(address)
            if not name:
                return self.say(f"Row {i} has no name.", True)
            if not url:
                return self.say(f"'{name}' doesn't have a valid web address.", True)
            if name.lower() in seen:
                return self.say(f"Two websites are called '{name}'. Rename one of them.", True)
            seen.add(name.lower())
            clean.append((name, url))
        self.result_sites = clean
        self.accept()
