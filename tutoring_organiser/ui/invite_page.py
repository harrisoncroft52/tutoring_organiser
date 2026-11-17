"""The Invite page: pick a lesson, create the Calendar event + Meet link, review and send the email."""
import html
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTime, QUrl
from PySide6.QtGui import QImage, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..calendar_api import calendar_create_event, calendar_find_student_events, calendar_resume_event
from ..config import DT_FMT
from ..google_auth import (
    CREDENTIALS_FILE,
    friendly_error,
    google_credentials,
    GOOGLE_DIR,
    google_status,
    mark_signin_expired,
    GoogleError,
    HAVE_GOOGLE,
    install_credentials,
    needs_signin,
    PIP_GOOGLE,
    sign_out,
)
from ..helpers import (
    auckland_now,
    default_attachments,
    fmt_clock_time,
    fmt_duration,
    nice_dt,
    setting_int,
    setting_time,
)
from ..invites import (
    check_attachments,
    DEFAULT_MESSAGE,
    DEFAULT_SUBJECT,
    gmail_send,
    IMAGE_FILTER,
    inline_template_images,
    invalid_emails,
    MEET_PENDING,
    parse_emails,
    register_inline_image_resources,
    render_invite,
    render_invite_html,
)
from .jobs import Job
from .theme import ACCENT, BAD, GOOD, MUTED
from .widgets import SmartSplitter


class InvitePage(QWidget):
    """Step 1 (left): choose student/time and create the Calendar event + Meet link.
    Step 2 (right): review/edit the email and send it - a separate, explicitly confirmed action.
    The Calendar event and the email are independent: a failed email never loses the event, and
    retrying it never creates another event."""

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.setObjectName("root")
        self.draft = None        # the invitation whose Calendar event exists (dict), else None
        self.pending = None      # {"id", "sig"}: event id reused if a create attempt failed part-way
        self.job = None
        self.busy = False
        self._email_sid = None   # which student the email box was last filled for

        # ---- header
        heading = QLabel("Invite")
        heading.setObjectName("title")
        self.g_status = QLabel()
        self.btn_connect = QPushButton("Connect Google")
        self.btn_connect.clicked.connect(self.connect_google)
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.clicked.connect(self.disconnect_google)
        top = QHBoxLayout()
        top.addWidget(heading)
        top.addStretch(1)
        top.addWidget(self.g_status)
        top.addWidget(self.btn_connect)
        top.addWidget(self.btn_disconnect)

        self.msg = QLabel()
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")

        # ---- left card: the lesson
        self.student = QComboBox()
        self.student.currentIndexChanged.connect(self.on_student_changed)
        self.email = QLineEdit()
        self.email.setPlaceholderText("student@example.com (separate several with commas)")
        self.save_email = QCheckBox("Save this address to the student's record")
        self.save_email.setChecked(True)
        tomorrow = QDate.currentDate().addDays(1)
        self.date = QDateEdit(tomorrow)
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd d MMM yyyy")
        self.time = QTimeEdit(setting_time(self.store))
        self.time.setDisplayFormat("h:mm ap")
        self.duration = QSpinBox()
        self.duration.setRange(5, 600)
        self.duration.setSingleStep(15)
        self.duration.setSuffix(" min")
        self.duration.setValue(setting_int(self.store, "default_duration", 60, 5, 600))
        self.attachments = default_attachments(self.store)
        self.subject = QLineEdit(self.store.setting("invite_subject", DEFAULT_SUBJECT))
        self.message = QTextEdit()
        self.message_inline_images = inline_template_images(self.store)
        register_inline_image_resources(self.message, self.message_inline_images)
        saved_message_html = self.store.setting("invite_message_html", "")
        if saved_message_html:
            self.message.setHtml(saved_message_html)
        else:
            self.message.setPlainText(self.store.setting("invite_message", DEFAULT_MESSAGE))
        self.message.setMinimumHeight(150)
        hint = QLabel("Placeholders: {first_name} {name} {date} {time} {duration} {meet_link} \u2013 filled in "
                      "when the event is created. You can still edit the final email before sending.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)

        self.resume_label = QLabel()
        self.resume_label.setWordWrap(True)
        self.btn_resume = QPushButton("Resume")
        self.btn_resume.clicked.connect(self.resume_unsent)
        self.resume_row = QWidget()
        rr = QHBoxLayout(self.resume_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(self.resume_label, 1)
        rr.addWidget(self.btn_resume)
        self.resume_row.hide()

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.addRow("Student", self.student)
        form.addRow("Email", self.email)
        form.addRow("", self.save_email)
        form.addRow("Date", self.date)
        form.addRow("Start time", self.time)
        form.addRow("Length", self.duration)
        form.addRow("Subject", self.subject)
        self.btn_create = QPushButton("Create Calendar Event + Meet Link")
        self.btn_create.setObjectName("primary")
        self.btn_create.clicked.connect(self.create_event)
        self.btn_find = QPushButton("Find existing booking")
        self.btn_find.clicked.connect(self.find_existing_booking)
        self.btn_new = QPushButton("New invitation")
        self.btn_new.clicked.connect(self.new_invitation)
        self.btn_new.hide()
        brow = QHBoxLayout()
        brow.addWidget(self.btn_find)
        brow.addWidget(self.btn_create, 1)
        brow.addWidget(self.btn_new)

        self.left = QFrame()
        self.left.setObjectName("card")
        self.left.setMinimumWidth(430)
        ll = QVBoxLayout(self.left)
        ll.setContentsMargins(18, 16, 18, 16)
        ll.setSpacing(10)
        ll.addWidget(self.resume_row)
        ll.addLayout(form)
        ll.addWidget(QLabel("Message"))
        self.btn_embed_message = QPushButton("Embed image in this message…")
        self.btn_embed_message.clicked.connect(self.add_inline_message_image)
        ll.addWidget(self.btn_embed_message, 0, Qt.AlignLeft)
        ll.addWidget(self.message, 1)
        ll.addWidget(hint)
        ll.addLayout(brow)

        # ---- right card: the email preview
        self.empty = QLabel("Create the Calendar event and the email preview will appear here.\n"
                            "Nothing is emailed until you press Send Invitation Email and confirm.")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)

        self.p_to = QLineEdit()
        self.p_to.setPlaceholderText("Student email address")
        self.p_subject = QLineEdit()
        self.p_student = QLabel()
        self.p_date = QLabel()
        self.p_time = QLabel()
        self.p_len = QLabel()
        self.p_meet = QLabel()
        self.p_meet.setTextFormat(Qt.RichText)
        self.p_meet.setOpenExternalLinks(True)
        self.p_meet.setWordWrap(True)
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.clicked.connect(self.copy_meet)
        for lab in (self.p_student, self.p_date, self.p_time, self.p_len, self.p_meet):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        meet_row = QHBoxLayout()
        meet_row.addWidget(self.p_meet, 1)
        meet_row.addWidget(self.btn_copy)
        for r, (name, w) in enumerate((("To", self.p_to), ("Subject", self.p_subject), ("Student", self.p_student),
                                       ("Date", self.p_date), ("Start time", self.p_time), ("Length", self.p_len))):
            lab = QLabel(name)
            lab.setObjectName("muted")
            grid.addWidget(lab, r, 0, Qt.AlignTop)
            grid.addWidget(w, r, 1)
        lab = QLabel("Meet link")
        lab.setObjectName("muted")
        grid.addWidget(lab, 6, 0, Qt.AlignTop)
        grid.addLayout(meet_row, 6, 1)
        self.p_attach = QLabel()
        self.p_attach.setWordWrap(True)
        self.btn_add_img = QPushButton("Add image\u2026")
        self.btn_add_img.clicked.connect(self.add_images)
        self.btn_clear_img = QPushButton("Clear")
        self.btn_clear_img.clicked.connect(self.clear_images)
        att_row = QHBoxLayout()
        att_row.addWidget(self.p_attach, 1)
        att_row.addWidget(self.btn_add_img)
        att_row.addWidget(self.btn_clear_img)
        lab = QLabel("Attachments")
        lab.setObjectName("muted")
        grid.addWidget(lab, 7, 0, Qt.AlignTop)
        grid.addLayout(att_row, 7, 1)
        grid.setColumnStretch(1, 1)
        self.p_body = QTextEdit()
        self.p_body.setMinimumHeight(150)
        self.inline_images = {}
        self.btn_embed_img = QPushButton("Embed image…")
        self.btn_embed_img.clicked.connect(self.add_inline_image)
        self.btn_send = QPushButton("Send Invitation Email")
        self.btn_send.setObjectName("primary")
        self.btn_send.clicked.connect(self.send_email)
        self.sent_label = QLabel()
        self.sent_label.setObjectName("muted")

        self.preview = QWidget()
        pl = QVBoxLayout(self.preview)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        title = QLabel("Email preview")
        title.setObjectName("studentName")
        pl.addWidget(title)
        pl.addLayout(grid)
        pl.addWidget(QLabel("Message (editable)"))
        pl.addWidget(self.btn_embed_img, 0, Qt.AlignLeft)
        pl.addWidget(self.p_body, 1)
        srow = QHBoxLayout()
        srow.addWidget(self.sent_label, 1)
        srow.addWidget(self.btn_send)
        pl.addLayout(srow)
        self.preview.hide()

        self.right = QFrame()
        self.right.setObjectName("card")
        rl = QVBoxLayout(self.right)
        rl.setContentsMargins(18, 16, 18, 16)
        rl.addWidget(self.empty, 1)
        rl.addWidget(self.preview, 1)

        split = SmartSplitter(self.left, self.right, sizes=(520, 680), min_sizes=(320, 360), stretch=(5, 6))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(10)
        lay.addLayout(top)
        lay.addWidget(self.msg)
        lay.addWidget(split, 1)
        self.update_google_status()

    # ---- helpers ---------------------------------------------------------
    def say(self, text, kind="muted"):
        self.msg.setText(text)
        color = {"ok": GOOD, "bad": BAD, "muted": MUTED}[kind]
        self.msg.setStyleSheet(f"color: {color};")

    def set_busy(self, busy, text=""):
        self.busy = busy
        for w in (self.btn_create, self.btn_find, self.btn_send, self.btn_connect, self.btn_disconnect, self.btn_resume, self.btn_new):
            w.setEnabled(not busy)
        self.btn_find.setEnabled(not busy and self.draft is None)
        if busy:
            self.say(text)
        else:
            self.update_create_button()
            self.update_google_status()

    def run(self, fn, callback, text):
        """Start fn on a background thread; callback(result, error) runs on the GUI thread."""
        self.set_busy(True, text)
        self.job = Job(fn, self)
        self._callback = callback
        self.job.done.connect(self._job_done)
        self.job.start()

    def _job_done(self, result, err):
        cb, self.job = self._callback, None
        self.set_busy(False)
        cb(result, err)

    def fail(self, err, title, extra=""):
        text = friendly_error(err) + (("\n\n" + extra) if extra else "")
        self.say(friendly_error(err), "bad")
        if needs_signin(err):
            mark_signin_expired()
            self.update_google_status()
        QMessageBox.warning(self, title, text)

    def update_google_status(self):
        st = google_status()
        text, color = {
            "missing_libs": ("Google libraries not installed", BAD),
            "no_credentials": ("Not connected \u2013 credentials file needed", MUTED),
            "needs_signin": ("Credentials loaded \u2013 sign-in needed", MUTED),
            "expired": ("Google sign-in expired \u2013 press Connect Google", BAD),
            "ready": ("Connected to Google", GOOD),
        }[st]
        self.g_status.setText(text)
        self.g_status.setStyleSheet(f"color: {color};")
        self.btn_connect.setText("Connect Google" if st != "ready" else "Reconnect")
        self.btn_disconnect.setVisible(st == "ready")

    # ---- Google connection ----------------------------------------------
    def ensure_credentials_file(self):
        if CREDENTIALS_FILE.exists():
            return True
        QMessageBox.information(
            self, "Choose your Google credentials file",
            "Next, choose the OAuth credentials JSON you downloaded from Google Cloud Console "
            "(APIs & Services > Credentials > your 'Desktop app' OAuth client > Download JSON). "
            "It is usually named client_secret_XXXX.json.\n\n"
            f"The app keeps a private copy in:\n{GOOGLE_DIR}\n(outside your project folder, so it can't be "
            "committed to GitHub). You can delete the original download afterwards.")
        path, _ = QFileDialog.getOpenFileName(self, "Choose Google credentials JSON", str(Path.home() / "Downloads"),
                                              "JSON files (*.json)")
        if not path:
            return False
        try:
            install_credentials(path)
        except GoogleError as e:
            QMessageBox.warning(self, "Credentials file", str(e))
            return False
        self.update_google_status()
        return True

    def google_ready_to_run(self):
        if not HAVE_GOOGLE:
            QMessageBox.warning(self, "Google libraries missing",
                                f"Install the Google libraries, then restart the app:\n\n{PIP_GOOGLE}")
            return False
        return self.ensure_credentials_file()

    def connect_google(self):
        if self.busy or not self.google_ready_to_run():
            return
        if google_status() == "ready":      # 'Reconnect': force a fresh sign-in
            sign_out()
        self.run(lambda: google_credentials(interactive=True) and True, self._connected,
                 "Waiting for you to finish signing in, in your web browser\u2026")

    def _connected(self, _result, err):
        if err:
            self.fail(err, "Google sign-in")
        else:
            self.say("Connected to Google.", "ok")
            callback = getattr(self, "google_connected", None)
            if callback:
                callback()

    def find_existing_booking(self):
        sid = self.student.currentData()
        if self.busy or not sid or not self.google_ready_to_run():
            return
        name = self.store.student(sid)["name"]
        self.run(lambda: calendar_find_student_events(google_credentials(interactive=True), name),
                 self.existing_bookings_found, "Searching Google Calendar for tutoring bookings…")

    def existing_bookings_found(self, events, err):
        if err:
            return self.fail(err, "Couldn't search Google Calendar")
        if not events:
            return self.say("No future 'Tutoring <student name>' booking was found. You can create one here.", "bad")
        labels = [f"{nice_dt(ev['start'].strftime(DT_FMT))} · {ev['duration']} min · {ev['event_id'][-6:]}"
                  + (" · Meet link ready" if ev["meet_url"] else " · No Meet link yet") for ev in events]
        label, ok = QInputDialog.getItem(self, "Choose a booking", "Google Calendar tutoring bookings:", labels, 0, False)
        if not ok:
            return
        event = events[labels.index(label)]
        sid = self.student.currentData()
        lid = self.store.apply_calendar_event(sid, event)
        row = self.store.lesson(lid)
        self.load_lesson(row)
        if not event["meet_url"]:
            self.say("Found the booking. Checking Google Meet and adding a link if needed…")
            self.check_meet()
        else:
            self.say("Found the existing booking and its Meet link. Review the email, then send it.", "ok")
        self.notify()

    def disconnect_google(self):
        if QMessageBox.question(self, "Disconnect Google",
                                "Forget the saved Google sign-in on this computer?\n\n"
                                "Existing Calendar events and sent emails are not affected.") == QMessageBox.Yes:
            sign_out()
            self.update_google_status()
            self.say("Disconnected.")

    # ---- students --------------------------------------------------------
    def on_show(self, selected_sid=None):
        """Called whenever the page is opened (the student list may have changed)."""
        self.update_google_status()
        if self.draft:
            return
        want = selected_sid or self.student.currentData()
        self.student.blockSignals(True)
        self.student.clear()
        for s in sorted(self.store.students(), key=lambda r: r["name"].lower()):
            self.student.addItem(s["name"], s["id"])
        i = self.student.findData(want)
        self.student.setCurrentIndex(i if i >= 0 else (0 if self.student.count() else -1))
        self.student.blockSignals(False)
        self.on_student_changed()

    def on_student_changed(self):
        sid = self.student.currentData()
        if sid != self._email_sid:                  # don't wipe an address typed but not yet used
            self._email_sid = sid
            s = self.store.student(sid) if sid else None
            self.email.setText((s["email"] or "") if s else "")
        self.refresh_resume()

    def refresh_resume(self):
        sid = self.student.currentData()
        rows = self.store.unsent_invites(sid) if (sid and not self.draft) else []
        self.resume_row.setVisible(bool(rows))
        if rows:
            more = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
            self.resume_label.setText(f"Unsent invitation: {nice_dt(rows[0]['start'])}{more}. "
                                      "Its Calendar event already exists.")
            self._resume_id = rows[0]["id"]

    def resume_unsent(self):
        row = self.store.lesson(self._resume_id)
        if row:
            self.load_lesson(row)

    # ---- form <-> draft --------------------------------------------------
    def lock_form(self, locked):
        for w in (self.student, self.email, self.save_email, self.date, self.time, self.duration,
                  self.subject, self.message):
            w.setEnabled(not locked)
        self.btn_new.setVisible(locked)
        self.btn_find.setEnabled(not locked and not self.busy)

    def update_create_button(self):
        done = bool(self.draft and self.draft["meet_url"])
        if not self.draft:
            self.btn_create.setText("Create Calendar Event + Meet Link")
        elif not done:
            self.btn_create.setText("Check for Meet Link")
        else:
            self.btn_create.setText("Calendar event created \u2713")
        self.btn_create.setEnabled(not self.busy and not done)
        # the 'primary' style would hide the greyed-out look, so drop it once there's nothing left to press
        self.btn_create.setObjectName("" if done else "primary")
        self.btn_create.style().unpolish(self.btn_create)
        self.btn_create.style().polish(self.btn_create)

    def read_form(self):
        sid = self.student.currentData()
        if not sid:
            QMessageBox.information(self, "Choose a student", "Add a student first, then pick them here.")
            return None
        emails = parse_emails(self.email.text())
        if not emails:
            QMessageBox.warning(self, "Email needed", "Enter the student's email address.")
            return None
        bad = invalid_emails(emails)
        if bad:
            QMessageBox.warning(self, "Check the email address", f"This doesn't look like an email address:\n{', '.join(bad)}")
            return None
        if not self.subject.text().strip() or not self.message.toPlainText().replace("\ufffc", "").strip():
            QMessageBox.warning(self, "Subject and message", "The subject and message can't be empty.")
            return None
        d, t = self.date.date(), self.time.time()
        start = datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())
        if start < auckland_now():
            if QMessageBox.question(self, "Date in the past",
                                    f"{nice_dt(start.strftime(DT_FMT))} has already passed (Auckland time). "
                                    "Create the event anyway?") != QMessageBox.Yes:
                return None
        return {"sid": sid, "emails": emails, "start": start, "duration": self.duration.value()}

    def create_event(self):
        if self.busy:
            return
        if self.draft:                                  # event exists; only the Meet link is missing
            return self.check_meet()
        v = self.read_form()
        if not v or not self.google_ready_to_run():
            return
        start_s = v["start"].strftime(DT_FMT)
        existing = self.store.lesson_at(v["sid"], start_s)
        if existing and existing["calendar_event_id"]:
            if QMessageBox.question(self, "Already has a Calendar event",
                                    "This student already has a lesson with a Calendar event at that time.\n\n"
                                    "Load it (to check the Meet link or send the email) instead of creating "
                                    "a second event?") == QMessageBox.Yes:
                self.load_lesson(existing)
            return
        student = self.store.student(v["sid"])
        sig = (v["sid"], start_s, v["duration"], tuple(v["emails"]))
        if not self.pending or self.pending["sig"] != sig:
            self.pending = {"id": uuid.uuid4().hex, "sig": sig}
        event_id = self.pending["id"]
        name = student["name"]
        v.update(name=name, save=self.save_email.isChecked(), start_s=start_s,
                 subject_tpl=self.subject.text().strip(),
                 message_tpl=self.message.toPlainText().replace("\ufffc", "").strip(),
                 message_html_tpl=self.message.toHtml(),
                 message_inline_images=dict(self.message_inline_images),
                 linked_existing=bool(existing), event_id=event_id)

        def work():
            creds = google_credentials(interactive=True)
            return calendar_create_event(creds, event_id=event_id, summary=f"Tutoring {name}",
                                         description=f"Online tutoring lesson with {name}.",
                                         start=v["start"], duration=v["duration"], emails=v["emails"])
        self.run(work, lambda res, err: self.event_created(v, res, err),
                 "Creating the Calendar event and Meet link\u2026")

    def event_created(self, v, res, err):
        if err:
            return self.fail(err, "Couldn't create the Calendar event",
                             "Nothing was emailed. Press the button again to retry.")
        lid = self.store.attach_event(v["sid"], v["start_s"], v["duration"], res["event_id"], res["meet_url"])
        self.pending = None
        joined = ", ".join(v["emails"])
        if v["save"] and joined != (self.store.student(v["sid"])["email"] or ""):
            self.store.set_student_email(v["sid"], joined)
        subject, body = render_invite(v["subject_tpl"], v["message_tpl"], v["name"], v["start"],
                                      v["duration"], res["meet_url"])
        html_body = render_invite_html(v["message_html_tpl"], v["name"], v["start"],
                                       v["duration"], res["meet_url"])
        self.draft = {"lesson_id": lid, "event_id": res["event_id"], "sid": v["sid"], "name": v["name"],
                      "emails": v["emails"], "start": v["start"], "duration": v["duration"],
                      "meet_url": res["meet_url"], "sent": False}
        self.lock_form(True)
        self.show_preview(subject, body, html_body, v["message_inline_images"])
        self.update_create_button()
        self.resume_row.hide()
        note = " It was added to the existing lesson at that time." if v["linked_existing"] else ""
        if res["meet_url"]:
            self.say("Calendar event created and saved. No email has been sent yet \u2013 review it on the "
                     "right, then press Send Invitation Email." + note, "ok")
        else:
            self.say("Calendar event created and saved, but Google hasn't produced the Meet link yet"
                     + (f" ({res['warning']})" if res["warning"] else "")
                     + ". Press 'Check for Meet Link' in a moment \u2013 this will NOT create another event.", "bad")
        self.notify()

    def check_meet(self):
        d = self.draft
        if not self.google_ready_to_run():
            return

        def work():
            return calendar_resume_event(google_credentials(interactive=True), d["event_id"])
        self.run(work, self.meet_checked, "Asking Google for the Meet link\u2026")

    def meet_checked(self, res, err):
        if err:
            return self.fail(err, "Couldn't get the Meet link")
        if not res["meet_url"]:
            return self.say("Google is still preparing the Meet link. Try again in a minute.", "bad")
        d = self.draft
        d["meet_url"] = res["meet_url"]
        self.store.set_meet_url(d["lesson_id"], res["meet_url"])
        cursor = self.p_body.document().find(MEET_PENDING)
        while not cursor.isNull():
            cursor.insertText(res["meet_url"])
            cursor = self.p_body.document().find(MEET_PENDING)
        self.p_subject.setText(self.p_subject.text().replace(MEET_PENDING, res["meet_url"]))
        self.show_meet(res["meet_url"])
        self.update_create_button()
        self.say("Got the Meet link. Review the email, then send it.", "ok")
        self.notify()

    def load_lesson(self, row):
        """Show an existing lesson's invitation (its Calendar event already exists)."""
        s = self.store.student(row["student_id"])
        start = datetime.strptime(row["start"], DT_FMT)
        self.student.blockSignals(True)
        self.student.setCurrentIndex(max(0, self.student.findData(s["id"])))
        self.student.blockSignals(False)
        self._email_sid = s["id"]
        self.email.setText(s["email"] or "")
        self.date.setDate(QDate(start.year, start.month, start.day))
        self.time.setTime(QTime(start.hour, start.minute))
        self.duration.setValue(row["duration"])
        emails = parse_emails(self.email.text())
        message_tpl = self.message.toPlainText().replace("\ufffc", "")
        subject, body = render_invite(self.subject.text(), message_tpl, s["name"], start,
                                      row["duration"], row["meet_url"])
        html_body = render_invite_html(self.message.toHtml(), s["name"], start, row["duration"], row["meet_url"])
        self.draft = {"lesson_id": row["id"], "event_id": row["calendar_event_id"], "sid": s["id"],
                      "name": s["name"], "emails": emails, "start": start, "duration": row["duration"],
                      "meet_url": row["meet_url"] or "", "sent": bool(row["invite_sent_at"])}
        self.lock_form(True)
        self.show_preview(subject, body, html_body, self.message_inline_images)
        self.update_create_button()
        self.resume_row.hide()
        self.say("Loaded the existing Calendar event \u2013 no new event will be created.", "ok")

    def prepare_for_lesson(self, row):
        """Prefill an invite from a local lesson; its event can be created from here."""
        self.new_invitation()
        student = self.store.student(row["student_id"])
        index = self.student.findData(row["student_id"])
        if index >= 0:
            self.student.setCurrentIndex(index)
        start = datetime.strptime(row["start"], DT_FMT)
        self.email.setText(student["email"] or "")
        self.date.setDate(QDate(start.year, start.month, start.day))
        self.time.setTime(QTime(start.hour, start.minute))
        self.duration.setValue(row["duration"])
        if self.email.text().strip():
            self.create_event()
        else:
            self.say("Add the student's email address, then create the Calendar event and review the invite.")

    def new_invitation(self):
        if self.busy:
            return
        self.draft = None
        self.pending = None
        self.load_defaults(schedule=False)
        self.lock_form(False)
        self.preview.hide()
        self.empty.show()
        self.update_create_button()
        self.say("")
        self.refresh_resume()

    # ---- preview + sending -----------------------------------------------
    def show_meet(self, url):
        if url:
            self.p_meet.setText(f'<a href="{html.escape(url)}" style="color:{ACCENT}">{html.escape(url)}</a>')
        else:
            self.p_meet.setText(MEET_PENDING)
        self.btn_copy.setEnabled(bool(url))

    def show_preview(self, subject, body, html_body=None, inline_images=None):
        d = self.draft
        self.p_to.setText(", ".join(d["emails"]))
        self.p_subject.setText(subject)
        self.p_student.setText(d["name"])
        self.p_date.setText(f"{d['start']:%A} {d['start'].day} {d['start']:%B %Y}")
        self.p_time.setText(fmt_clock_time(d["start"]) + " (Pacific/Auckland)")
        self.p_len.setText(fmt_duration(d["duration"]))
        self.show_meet(d["meet_url"])
        self.inline_images = dict(inline_images or {})
        register_inline_image_resources(self.p_body, self.inline_images)
        if html_body:
            self.p_body.setHtml(html_body)
        else:
            self.p_body.setPlainText(body)
        register_inline_image_resources(self.p_body, self.inline_images)
        self.show_attachments()
        self.sent_label.setText("Already sent once \u2013 you can send again." if d["sent"] else "")
        self.btn_send.setText("Resend Invitation Email" if d["sent"] else "Send Invitation Email")
        self.empty.hide()
        self.preview.show()

    def load_defaults(self, schedule=True):
        """Fill the form from Settings. schedule=False leaves date/time/length alone."""
        self.subject.setText(self.store.setting("invite_subject", DEFAULT_SUBJECT))
        self.message_inline_images = inline_template_images(self.store)
        register_inline_image_resources(self.message, self.message_inline_images)
        saved_html = self.store.setting("invite_message_html", "")
        if saved_html:
            self.message.setHtml(saved_html)
        else:
            self.message.setPlainText(self.store.setting("invite_message", DEFAULT_MESSAGE))
        register_inline_image_resources(self.message, self.message_inline_images)
        self.attachments = default_attachments(self.store)
        if schedule:
            self.duration.setValue(setting_int(self.store, "default_duration", 60, 5, 600))
            self.time.setTime(setting_time(self.store))

    def add_inline_message_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image to embed in the invitation template",
                                              str(Path.home()), IMAGE_FILTER)
        if not path:
            return
        content_id = uuid.uuid4().hex + "@tutortracker"
        resource_url = QUrl("cid:" + content_id)
        self.message.document().addResource(QTextDocument.ImageResource, resource_url, QImage(path))
        image = QTextImageFormat()
        image.setName(resource_url.toString())
        image.setWidth(360)
        self.message.textCursor().insertImage(image)
        self.message_inline_images[content_id] = path

    def show_attachments(self):
        if not self.attachments:
            self.p_attach.setText("None")
        else:
            names = ", ".join(Path(a).name + ("  \u26a0 missing" if not Path(a).is_file() else "")
                              for a in self.attachments)
            self.p_attach.setText(names)
        self.p_attach.setToolTip("\n".join(self.attachments))
        self.btn_clear_img.setEnabled(bool(self.attachments))

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose images to attach", str(Path.home()), IMAGE_FILTER)
        for f in files:
            if f not in self.attachments:
                self.attachments.append(f)
        self.show_attachments()

    def add_inline_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image to embed in the email", str(Path.home()), IMAGE_FILTER)
        if not path:
            return
        content_id = uuid.uuid4().hex + "@tutortracker"
        resource_url = QUrl("cid:" + content_id)
        self.p_body.document().addResource(QTextDocument.ImageResource, resource_url, QImage(path))
        image = QTextImageFormat()
        image.setName(resource_url.toString())
        image.setWidth(360)
        self.p_body.textCursor().insertImage(image)
        self.inline_images[content_id] = path

    def clear_images(self):
        self.attachments = []
        self.show_attachments()

    def copy_meet(self):
        if self.draft and self.draft["meet_url"]:
            QApplication.clipboard().setText(self.draft["meet_url"])
            self.say("Meet link copied.", "ok")

    def send_email(self):
        d = self.draft
        if self.busy or not d:
            return
        subject = self.p_subject.text().strip()
        body = self.p_body.toPlainText().replace("\ufffc", "").strip()
        emails = parse_emails(self.p_to.text())
        if not d["meet_url"]:
            return QMessageBox.information(self, "No Meet link yet",
                                           "The Meet link isn't ready. Press 'Check for Meet Link' first.")
        if not emails:
            return QMessageBox.warning(self, "Email needed", "Enter the student's email address.")
        bad_emails = invalid_emails(emails)
        if bad_emails:
            return QMessageBox.warning(self, "Check the email address", f"This doesn't look like an email address:\n{', '.join(bad_emails)}")
        if not subject or not body:
            return QMessageBox.warning(self, "Nothing to send", "The subject and message can't be empty.")
        if MEET_PENDING in subject + body:
            return QMessageBox.warning(self, "Meet link missing",
                                       "The text still contains the '[Meet link pending]' marker. Check for the "
                                       "Meet link first, or remove the marker.")
        if d["meet_url"] not in body:
            if QMessageBox.question(self, "Meet link not in the message",
                                    "Your message doesn't contain the Meet link, so the student won't be able to "
                                    "join. Send it anyway?") != QMessageBox.Yes:
                return
        if not self.google_ready_to_run():
            return
        files = list(self.attachments)
        try:
            check_attachments(files + list(self.inline_images.values()))
        except GoogleError as e:
            return QMessageBox.warning(self, "Attachments", str(e))
        again = "\n\n(You already sent this invitation once.)" if d["sent"] else ""
        att = f"\nAttachments: {len(files)} image(s)" if files else ""
        embed_note = f"\nEmbedded images: {len(self.inline_images)}" if self.inline_images else ""
        if QMessageBox.question(self, "Send invitation email?",
                                f"Send this email now?\n\nTo: {', '.join(emails)}\nSubject: {subject}"
                                + att + embed_note + again) != QMessageBox.Yes:
            return
        d["emails"] = emails
        if self.save_email.isChecked():
            saved_email = ", ".join(emails)
            if saved_email != (self.store.student(d["sid"])["email"] or ""):
                self.store.set_student_email(d["sid"], saved_email)
        to = list(emails)
        html_body = self.p_body.toHtml()
        inline_images = [(path, cid) for cid, path in self.inline_images.items()]

        def work():
            return gmail_send(google_credentials(interactive=True), to, subject, body, files,
                              html_body, inline_images)
        self.run(work, self.email_sent, "Sending the email through Gmail\u2026")

    def email_sent(self, msg_id, err):
        if err:
            return self.fail(err, "Couldn't send the email",
                             "Your Calendar event is still saved. Press Send Invitation Email to retry \u2013 "
                             "no new event will be created.")
        d = self.draft
        d["sent"] = True
        self.store.mark_invite_sent(d["lesson_id"])
        self.btn_send.setText("Resend Invitation Email")
        self.sent_label.setText(f"Sent {datetime.now():%d %b %H:%M}")
        self.say(f"Gmail confirmed the email was sent to {', '.join(d['emails'])}.", "ok")
        QMessageBox.information(self, "Email sent", f"Gmail confirmed the invitation was sent to "
                                f"{', '.join(d['emails'])}.")
        self.notify()

    def notify(self):
        """Lets the main window refresh the Students/Calendar views."""
        cb = getattr(self, "changed", None)
        if cb:
            cb()
