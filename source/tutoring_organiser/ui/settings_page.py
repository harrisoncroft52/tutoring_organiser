"""The Settings page."""
import json
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QImage, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
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

from ..google_auth import GOOGLE_DIR, google_status, GoogleError
from ..helpers import default_attachments, setting_int, setting_time
from ..invites import (
    check_attachments,
    DEFAULT_MESSAGE,
    DEFAULT_SUBJECT,
    IMAGE_FILTER,
    inline_template_images,
    MAX_ATTACH_BYTES,
    register_inline_image_resources,
)
from ..version import __version__ as APP_VERSION
from .theme import BAD, GOOD, MUTED
from .widgets import AlertCard, cell, make_table


class SettingsPage(QWidget):
    """Everything is stored in the app_settings table, so it lives in tutoring.db with your other data."""
    saved = None   # set by main window: called after Save
    updater = None  # set by main window: UpdateManager

    def __init__(self, store, invite_page):
        super().__init__()
        self.store = store
        self.invite_page = invite_page
        self.setObjectName("root")
        self.attach_paths = []
        self.inline_template_images = {}

        def card(title, note="", frame_class=QFrame):
            frame = frame_class()
            frame.setObjectName("card")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(18, 14, 18, 16)
            lay.setSpacing(8)
            head = QLabel(title)
            head.setStyleSheet("font-weight: 600; font-size: 14px;")
            lay.addWidget(head)
            if note:
                n = QLabel(note)
                n.setObjectName("muted")
                n.setWordWrap(True)
                lay.addWidget(n)
            return frame, lay

        # ---- invitation email
        email_card, el = card("Default invitation email",
                              "Used to fill the Invite page each time you start a new invitation. "
                              "You can still edit both for a single email on the Invite page.")
        self.s_subject = QLineEdit()
        self.s_message = QTextEdit()
        self.s_message.setMinimumHeight(130)
        hint = QLabel("Placeholders: {first_name} {name} {date} {time} {duration} {meet_link}")
        hint.setObjectName("muted")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.addRow("Subject", self.s_subject)
        el.addLayout(form)
        el.addWidget(QLabel("Message"))
        el.addWidget(self.s_message, 1)
        self.btn_embed_template = QPushButton("Embed image in default email…")
        self.btn_embed_template.clicked.connect(self.add_inline_template_image)
        el.addWidget(self.btn_embed_template, 0, Qt.AlignLeft)
        el.addWidget(hint)
        self.btn_reset = QPushButton("Reset email text to built-in default")
        self.btn_reset.clicked.connect(self.reset_email)
        el.addWidget(self.btn_reset, 0, Qt.AlignLeft)

        # ---- default attachments
        att_card, al = card("Default image attachments",
                            f"These images are attached to every invitation (you can add or remove images per email "
                            f"on the Invite page). PNG, JPG, GIF or WebP; keep the total under "
                            f"{MAX_ATTACH_BYTES // (1024 * 1024)} MB. The app stores each image's location, not a copy.")
        self.s_attach = make_table(["Image"], stretch_col=0)
        self.s_attach.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.s_attach.setFixedHeight(120)
        b_add = QPushButton("Add images\u2026")
        b_add.clicked.connect(self.add_images)
        b_rem = QPushButton("Remove selected")
        b_rem.clicked.connect(self.remove_image)
        arow = QHBoxLayout()
        arow.addWidget(b_add)
        arow.addWidget(b_rem)
        arow.addStretch(1)
        al.addWidget(self.s_attach)
        al.addLayout(arow)

        # ---- lesson + calendar defaults
        les_card, ll = card("Lesson defaults", "Pre-filled on the Invite page.")
        self.s_duration = QSpinBox()
        self.s_duration.setRange(5, 600)
        self.s_duration.setSingleStep(15)
        self.s_duration.setSuffix(" min")
        self.s_time = QTimeEdit()
        self.s_time.setDisplayFormat("h:mm ap")
        f2 = QFormLayout()
        f2.setLabelAlignment(Qt.AlignLeft)
        f2.addRow("Length", self.s_duration)
        f2.addRow("Start time", self.s_time)
        ll.addLayout(f2)

        cal_card, cl = card("Calendar")
        self.s_view = QComboBox()
        for label, key in (("Week", "week"), ("Month", "month"), ("List", "list")):
            self.s_view.addItem(label, key)
        self.s_week = QComboBox()
        for label, key in (("Monday", "mon"), ("Sunday", "sun")):
            self.s_week.addItem(label, key)
        f3 = QFormLayout()
        f3.setLabelAlignment(Qt.AlignLeft)
        f3.addRow("Opens in", self.s_view)
        f3.addRow("Week starts on", self.s_week)
        cl.addLayout(f3)

        # ---- google
        g_card, gl = card("Google account", frame_class=AlertCard)
        self.g_card = g_card
        self.g_label = QLabel()
        self.g_label.setWordWrap(True)
        self.g_conn = b_conn = QPushButton("Connect Google")
        b_conn.clicked.connect(lambda: (self.invite_page.connect_google(), self.refresh_google()))
        b_disc = QPushButton("Disconnect")
        b_disc.clicked.connect(lambda: (self.invite_page.disconnect_google(), self.refresh_google()))
        grow = QHBoxLayout()
        grow.addWidget(self.g_label, 1)
        grow.addWidget(b_conn)
        grow.addWidget(b_disc)
        where = QLabel(f"Sign-in files are kept in {GOOGLE_DIR}")
        where.setObjectName("muted")
        where.setWordWrap(True)
        gl.addLayout(grow)
        gl.addWidget(where)

        # ---- about / updates
        u_card, ul = card("About and updates")
        ul.addWidget(QLabel(f"Tutoring Organiser version {APP_VERSION}"))
        self.s_autoupdate = QCheckBox("Check for updates when the app starts")
        self.s_autoupdate.setChecked(self.store.setting("auto_update_check", "1") == "1")
        self.s_autoupdate.toggled.connect(
            lambda on: self.store.set_setting("auto_update_check", "1" if on else "0"))
        b_upd = QPushButton("Check for updates now")
        b_upd.clicked.connect(lambda: self.updater and self.updater.check(manual=True))
        urow = QHBoxLayout()
        urow.addWidget(self.s_autoupdate, 1)
        urow.addWidget(b_upd)
        ul.addLayout(urow)

        # ---- layout
        heading = QLabel("Settings")
        heading.setObjectName("title")
        self.status = QLabel()
        self.status.setObjectName("muted")
        btn_save = QPushButton("Save settings")
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self.save)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(btn_save)
        small = QVBoxLayout()
        small.addWidget(les_card)
        small.addWidget(cal_card)
        small.addWidget(g_card)
        small.addWidget(u_card)
        small.addStretch(1)
        left = QVBoxLayout()
        left.addWidget(email_card, 1)
        left.addWidget(att_card)
        cols = QHBoxLayout()
        cols.setSpacing(14)
        cols.addLayout(left, 3)
        cols.addLayout(small, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        lay.addWidget(heading)
        lay.addLayout(cols, 1)
        lay.addLayout(bottom)
        self.load()

    # ---- values
    def load(self):
        s = self.store
        self.s_subject.setText(s.setting("invite_subject", DEFAULT_SUBJECT))
        self.inline_template_images = inline_template_images(s)
        register_inline_image_resources(self.s_message, self.inline_template_images)
        saved_html = s.setting("invite_message_html", "")
        if saved_html:
            self.s_message.setHtml(saved_html)
        else:
            self.s_message.setPlainText(s.setting("invite_message", DEFAULT_MESSAGE))
        register_inline_image_resources(self.s_message, self.inline_template_images)
        self.attach_paths = default_attachments(s)
        self.show_attach()
        self.s_duration.setValue(setting_int(s, "default_duration", 60, 5, 600))
        self.s_time.setTime(setting_time(s))
        self.s_view.setCurrentIndex(max(0, self.s_view.findData(s.setting("default_cal_view", "week"))))
        self.s_week.setCurrentIndex(max(0, self.s_week.findData(s.setting("week_start", "mon"))))
        self.refresh_google()
        self.status.setText("")




    def on_show(self):
        self.load()

    def follow_pulse(self, pulse):
        self.g_card.follow(pulse)

    def refresh_google(self):
        st = google_status()
        text, colour = {
            "missing_libs": ("Google libraries not installed", BAD),
            "no_credentials": ("Not connected", MUTED),
            "needs_signin": ("Credentials loaded \u2013 sign-in needed", MUTED),
            "expired": ("\u26a0 Sign-in expired \u2013 press Connect Google", BAD),
            "ready": ("Connected to Google", GOOD),
        }[st]
        expired = st == "expired"
        self.g_label.setText(text)
        self.g_label.setStyleSheet(f"color: {colour};" + (" font-weight: 600;" if expired else ""))
        self.g_card.set_alert(expired)
        self.g_label.setToolTip("Google stopped accepting the saved sign-in. Calendar sync and invitation emails "
                                "are paused until you sign in again." if expired else "")
        self.g_conn.setObjectName("primary" if expired else "")
        self.g_conn.style().unpolish(self.g_conn)
        self.g_conn.style().polish(self.g_conn)

    def show_attach(self):
        self.s_attach.setRowCount(len(self.attach_paths))
        for r, path in enumerate(self.attach_paths):
            missing = not Path(path).is_file()
            it = cell(("\u26a0 missing: " if missing else "") + Path(path).name, BAD if missing else None)
            it.setToolTip(path)
            self.s_attach.setItem(r, 0, it)

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose images to attach", str(Path.home()), IMAGE_FILTER)
        for f in files:
            if f not in self.attach_paths:
                self.attach_paths.append(f)
        self.show_attach()

    def remove_image(self):
        r = self.s_attach.currentRow()
        if 0 <= r < len(self.attach_paths):
            del self.attach_paths[r]
            self.show_attach()

    def reset_email(self):
        self.s_subject.setText(DEFAULT_SUBJECT)
        self.s_message.setPlainText(DEFAULT_MESSAGE)
        self.inline_template_images = {}
        self.status.setText("Email text reset \u2013 press Save settings to keep it.")

    def add_inline_template_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image to embed in the default email",
                                              str(Path.home()), IMAGE_FILTER)
        if not path:
            return
        content_id = uuid.uuid4().hex + "@tutortracker"
        resource_url = QUrl("cid:" + content_id)
        self.s_message.document().addResource(QTextDocument.ImageResource, resource_url, QImage(path))
        image = QTextImageFormat()
        image.setName(resource_url.toString())
        image.setWidth(360)
        self.s_message.textCursor().insertImage(image)
        self.inline_template_images[content_id] = path

    def save(self):
        plain_message = self.s_message.toPlainText().replace("\ufffc", "").strip()
        if not self.s_subject.text().strip() or not plain_message:
            QMessageBox.warning(self, "Settings", "The default subject and message can't be empty.")
            return
        try:
            check_attachments(self.attach_paths + list(self.inline_template_images.values()))
        except GoogleError as e:
            QMessageBox.warning(self, "Attachments", str(e))
            return
        s = self.store
        s.set_setting("invite_subject", self.s_subject.text().strip())
        s.set_setting("invite_message", plain_message)
        s.set_setting("invite_message_html", self.s_message.toHtml())
        s.set_setting("invite_inline_images", json.dumps(self.inline_template_images))
        s.set_setting("invite_attachments", json.dumps(self.attach_paths))
        s.set_setting("default_duration", str(self.s_duration.value()))
        s.set_setting("default_time", self.s_time.time().toString("HH:mm"))
        s.set_setting("default_cal_view", self.s_view.currentData())
        s.set_setting("week_start", self.s_week.currentData())
        self.status.setText("Saved.")
        if self.saved:
            self.saved()
