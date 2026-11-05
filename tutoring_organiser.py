"""
Tutoring Organiser - a small desktop app for organising online tutoring.

Run:   pip install PySide6 google-api-python-client google-auth-httplib2 google-auth-oauthlib
       python tutor_trackerv10.py
Build: pip install pyinstaller   then   pyinstaller --noconsole --onefile tutor_trackerv10.py

Data is stored in %APPDATA%\\TutoringOrganiser\\tutoring.db on Windows.
Back that file up and you have backed up everything. PDFs are never copied;
the app only stores their paths.

v9 adds an Invite page (Google Calendar + Meet + Gmail). Google sign-in files
live OUTSIDE the project folder (see GOOGLE_DIR) so they can't be committed.
"""
import base64
import html
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import tempfile
import uuid
from email.message import EmailMessage
from collections import Counter
from datetime import datetime, date as Date, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QDateTime, QUrl, QTimer, QEvent, QDate, QTime, QMimeData, QObject, QThread, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QDesktopServices, QFont, QDrag, QBrush, QPen, QPainter, QPainterPath, QFontMetrics, QTextImageFormat, QImage, QTextDocument
try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QDateTimeAxis, QValueAxis
    HAVE_CHARTS = True
except ImportError:          # pip install PySide6-Addons
    HAVE_CHARTS = False
from PySide6.QtWidgets import (
    QLayout, QAbstractItemView, QApplication, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QInputDialog,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget, QStackedWidget, QCalendarWidget,
    QPlainTextEdit, QAbstractSpinBox, QCheckBox, QDateEdit, QTimeEdit, QGridLayout,
    QScrollArea, QToolTip, QButtonGroup, QSizePolicy, QBoxLayout,
)

try:  # Included with PySide6-Addons; the resource page still offers direct links without it.
    from PySide6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEngineProfile,
                                         QWebEngineUrlRequestInterceptor)
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEBENGINE = True
except ImportError:
    HAVE_WEBENGINE = False

try:   # pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials as GoogleCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build as google_build
    from googleapiclient.errors import HttpError
    HAVE_GOOGLE = True
except ImportError:
    HAVE_GOOGLE = False
    HttpError = RefreshError = Exception

# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
USER_DATA_ROOT = Path(os.environ.get("APPDATA") or (Path.home() / ".config"))
DATA_DIR = USER_DATA_ROOT / "TutoringOrganiser"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "tutoring.db"
# Older releases kept the database beside the script/executable. Copy it once
# into the writable per-user location; never move or overwrite the original.
if not DB_PATH.exists():
    for _legacy_db in (APP_DIR / "tutoring.db", USER_DATA_ROOT / "TutorTracker" / "tutoring.db"):
        if _legacy_db.is_file() and _legacy_db.resolve() != DB_PATH.resolve():
            shutil.copy2(_legacy_db, DB_PATH)
            break

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rate REAL NOT NULL DEFAULT 0,          -- hourly rate
    folder TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    email TEXT DEFAULT ''                  -- v9: comma-separated if more than one
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    start TEXT NOT NULL,                   -- 'YYYY-MM-DD HH:MM'
    duration INTEGER NOT NULL DEFAULT 60,  -- minutes
    rate REAL NOT NULL DEFAULT 0,          -- hourly rate copied at creation
    status TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled / completed / cancelled
    pdf TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    calendar_event_id TEXT DEFAULT '',     -- v9: Google Calendar event id
    meet_url TEXT DEFAULT '',              -- v9: Google Meet link
    invite_sent_at TEXT DEFAULT '',       -- v9: when the invitation email was sent
    local_updated_at TEXT DEFAULT '',
    google_updated_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    date TEXT NOT NULL,                    -- 'YYYY-MM-DD'
    amount REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'payment',  -- payment / bonus / refund
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS additional_pdfs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    pdf TEXT NOT NULL,
    UNIQUE(student_id, pdf)
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

DT_FMT = "%Y-%m-%d %H:%M"


# Additive migrations: (table, column, definition). Never drops or rewrites data.
MIGRATIONS = [
    ("students", "email", "TEXT DEFAULT ''"),
    ("lessons", "calendar_event_id", "TEXT DEFAULT ''"),
    ("lessons", "meet_url", "TEXT DEFAULT ''"),
    ("lessons", "invite_sent_at", "TEXT DEFAULT ''"),
    ("lessons", "local_updated_at", "TEXT DEFAULT ''"),
    ("lessons", "google_updated_at", "TEXT DEFAULT ''"),
]
DB_VERSION = 10


def _missing_columns(con):
    out = []
    for table, col, ddl in MIGRATIONS:
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if have and col not in have:
            out.append((table, col, ddl))
    return out


def migrate(con, db_path=None):
    """Apply additive schema updates while preserving existing tracker data."""
    todo = _missing_columns(con)
    if todo and db_path and Path(db_path).exists():
        backup = Path(db_path).with_name(Path(db_path).stem + "_pre_v10_backup.db")
        if not backup.exists():                       # one-off copy of the untouched database
            dst = sqlite3.connect(backup)
            try:
                con.backup(dst)
            finally:
                dst.close()
    for table, col, ddl in todo:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    if todo or con.execute("PRAGMA user_version").fetchone()[0] < DB_VERSION:
        con.execute(f"PRAGMA user_version = {DB_VERSION}")
    con.commit()


def connect(path=None):
    path = path or DB_PATH
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)          # creates missing tables only; existing ones are untouched
    migrate(con, path)
    return con


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def effective_status(row):
    """A lesson still marked 'scheduled' whose time has passed counts as completed."""
    if row["status"] == "scheduled" and datetime.strptime(row["start"], DT_FMT) < datetime.now():
        return "completed"
    return row["status"]


def needs_calendar_delete(row):
    """True only when a lesson still has a live Google Calendar event to remove.
    No event id -> nothing exists in Google. Cancelled -> the event is already cancelled there (sync pushes
    cancellations), so deleting the tracker row needs no API request either."""
    return bool(row["calendar_event_id"]) and row["status"] != "cancelled"


def invite_status(row):
    """What has happened for this lesson's invitation: '–' / 'Calendar made' / 'Email sent'."""
    if row["invite_sent_at"]:
        return "Email sent"
    if row["calendar_event_id"]:
        return "Calendar made"
    return "\u2013"


def invite_colour(status):
    return {"Email sent": GOOD, "Calendar made": ACCENT}.get(status, MUTED)


def note_line(text):
    """A student's notes squashed onto one line for the header and list."""
    return "  \u00b7  ".join(l.strip() for l in (text or "").splitlines() if l.strip())


def setting_int(store, key, default, lo, hi):
    try:
        return max(lo, min(hi, int(store.setting(key, str(default)))))
    except ValueError:
        return default


def setting_time(store):
    t = QTime.fromString(store.setting("default_time", "16:00"), "HH:mm")
    return t if t.isValid() else QTime(16, 0)


def default_attachments(store):
    try:
        v = json.loads(store.setting("invite_attachments", "[]"))
        return [x for x in v if isinstance(x, str)]
    except ValueError:
        return []


def lesson_value(row):
    return row["rate"] * row["duration"] / 60.0


def money(x):
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def nice_dt(s):
    d = datetime.strptime(s, DT_FMT)
    day = f"{d:%a} {d.day} {d:%b %Y}"
    if d.hour or d.minute:
        t = d.strftime("%I:%M %p").lstrip("0").lower()
        return f"{day}, {t}"
    return day


def short_dt(s):
    """Compact date for the student list (drops the year if it is this year)."""
    d = datetime.strptime(s, DT_FMT)
    return f"{d:%a} {d.day} {d:%b}" + ("" if d.year == datetime.now().year else f" {d.year}")


DATE_PATTERNS = [
    (re.compile(r"(\d{4})[-_./ ](\d{1,2})[-_./ ](\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})[-_./ ](\d{1,2})[-_./ ](\d{2}|\d{4})"), ("d", "m", "y")),
]


def date_from_filename(path):
    """Try to read a date out of a file name; fall back to the file's modified time."""
    name = Path(path).stem
    for rx, order in DATE_PATTERNS:
        m = rx.search(name)
        if m:
            parts = dict(zip(order, map(int, m.groups())))
            try:
                if parts["y"] < 100:
                    parts["y"] += 2000 if parts["y"] < 70 else 1900
                return datetime(parts["y"], parts["m"], parts["d"]), "filename"
            except ValueError:
                pass
    return datetime.fromtimestamp(os.path.getmtime(path)).replace(hour=0, minute=0, second=0, microsecond=0), "modified"


def pdfs_in(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


# --------------------------------------------------------------------------
# Paste parsers (Google Calendar sessions and payment lists)
# --------------------------------------------------------------------------
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def month_num(word):
    w = word.lower().rstrip(".")
    for i, name in enumerate(_MONTHS, 1):
        if w in (name, name[:3]) or (i == 9 and w == "sept"):
            return i
    return None


def weekday_ok(d, word):
    w = (word or "")[:3].lower()
    return w not in _WEEKDAYS or _WEEKDAYS.index(w) == d.weekday()


def clean_line(s):
    s = s.replace("\u202f", " ").replace("\u00a0", " ")
    for d in "\u2010\u2011\u2012\u2013\u2014\u2212":
        s = s.replace(d, "-")
    return s.strip()


_T = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
RANGE_RE = re.compile(rf"^{_T}\s*(?:-|to)\s*{_T}$", re.I)


def parse_time_range(text):
    """'3 - 4pm', '4:30 - 6pm', '11am - 12pm', '16:00 - 17:00' -> (start_minutes, duration_minutes)."""
    m = RANGE_RE.match(text)
    if not m:
        return None
    h1, m1, a1, h2, m2, a2 = m.groups()
    h1, h2, m1, m2 = int(h1), int(h2), int(m1 or 0), int(m2 or 0)
    a1 = a1.replace(".", "").lower() if a1 else None
    a2 = a2.replace(".", "").lower() if a2 else None
    if m1 > 59 or m2 > 59 or h1 > 24 or h2 > 24:
        return None

    def to_min(h, mi, ap):
        if ap:
            if not 1 <= h <= 12:
                return None
            h = h % 12 + (12 if ap == "pm" else 0)
        return h * 60 + mi

    if a1 is None and a2 is not None:          # '3 - 4pm': the start borrows the end's am/pm
        s, e = to_min(h1, m1, a2), to_min(h2, m2, a2)
        if s is not None and e is not None and s >= e:
            alt = to_min(h1, m1, "am")         # '11 - 1pm' means 11am
            if alt is not None and alt < e:
                s = alt
    elif a1 is not None and a2 is None:        # '3pm - 4': the end borrows the start's am/pm
        s, e = to_min(h1, m1, a1), to_min(h2, m2, a1)
        if s is not None and e is not None and e <= s:
            alt = to_min(h2, m2, "pm" if a1 == "am" else "am")
            if alt is not None and alt > s:
                e = alt
    else:                                      # both given, or plain 24-hour times
        s, e = to_min(h1, m1, a1), to_min(h2, m2, a2)
    if s is None or e is None:
        return None
    dur = (e - s) % 1440
    return (s, dur) if dur else None


def fmt_clock(minutes):
    h, m = divmod(minutes % 1440, 60)
    return f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


MY_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})(?:\s*,\s*([A-Za-z]{3,9}))?$")
TITLE_RE = re.compile(r"^\s*tutoring\b\s*[-:]?\s*(.*)$", re.I)


def student_from_title(title):
    m = TITLE_RE.match(title)
    if not m:
        return None
    name = re.sub(r"\s*\(.*?\)\s*$", "", m.group(1)).strip()
    return name or None


def parse_calendar_paste(text):
    """
    Parses Google Calendar search results:
        30 / Oct 2026, Fri / 3 - 4pm / Tutoring Lorenzo / 4:30 - 6pm / Tutoring Noah / (blank) / 2 / Nov 2026, Mon ...
    Returns (items, warnings). Each item: start (datetime), duration (min), title, name (None if not 'Tutoring X').
    """
    lines = [l for l in (clean_line(x) for x in text.splitlines()) if l]
    items, warnings = [], []
    cur = pending = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        my = MY_RE.match(lines[i + 1]) if i + 1 < len(lines) else None
        if re.fullmatch(r"\d{1,2}", ln) and my:
            mon = month_num(my.group(1))
            try:
                cur = Date(int(my.group(2)), mon, int(ln))
            except (TypeError, ValueError):
                warnings.append(f"Couldn't read the date '{ln} {lines[i + 1]}'.")
                cur = None
            else:
                if not weekday_ok(cur, my.group(3)):
                    warnings.append(f"{ln} {lines[i + 1]}: that day of the week doesn't match the date. Please check it.")
            pending = None
            i += 2
            continue
        tr = parse_time_range(ln)
        if tr:
            if cur is None:
                warnings.append(f"The time '{ln}' has no date above it.")
            else:
                pending = tr
            i += 1
            continue
        if pending and cur:
            start = datetime(cur.year, cur.month, cur.day) + timedelta(minutes=pending[0])
            items.append({"start": start, "duration": pending[1], "title": ln, "name": student_from_title(ln)})
            pending = None
        else:
            warnings.append(f"Couldn't read the line: {ln}")
        i += 1
    return items, warnings


PAY_MONTH_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{4})$")
PAY_DAY_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{1,2})$")
SECTION_RE = re.compile(r"^(upcoming|past)\s+(?:lessons?|payments?|sessions?)$", re.I)
DOLLAR_RE = re.compile(r"^(?:[A-Za-z]{1,3})?\s*([-+]?)\s*\$\s*([\d,]+(?:\.\d{1,2})?)$")
PLAIN_RE = re.compile(r"^([-+]?)\s*([\d,]+(?:\.\d{1,2})?)$")          # bare '116.00' (bank-style lines only)
BANK_DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})$")  # '12 Oct 2026'


def parse_payments_paste(text):
    """
    Two layouts are understood, and they can be mixed:

    1. Lesson list:   October2026 / Tue27 / 16:00 - 17:00 / $45, or month, year,
       weekday, and day each on their own line before the time and amount.
       (with optional 'Upcoming lessons' / 'Past lessons' dividers)
    2. Bank style:    12 Oct 2026 / D T & K E Carpenter Monday Carpenter Ben / 116.00 / 17 Sep 2026 / ...
       (date line, then the payer/description, then the amount)

    Returns (items, warnings). Each item: date, amount, note, upcoming (bool).
    """
    lines = [l for l in (clean_line(x) for x in text.splitlines()) if l]
    items, warnings = [], []
    month = year = cur = pending = mode = None
    pending_weekday = None
    bank, desc = False, ""

    def money_match(ln):
        am = DOLLAR_RE.match(ln) or (PLAIN_RE.match(ln) if bank else None)
        if not am:
            return None
        return float(am.group(2).replace(",", "")) * (-1 if am.group(1) == "-" else 1)

    for ln in lines:
        mk = SECTION_RE.match(ln)
        if mk:
            if mk.group(1).lower() == "past":
                if mode is None:                      # everything above a 'Past lessons' divider is upcoming
                    for it in items:
                        if not it.get("bank"):        # bank-style lines are always real, past payments
                            it["upcoming"] = True
                mode = "past"
            else:
                mode = "upcoming"
            month = year = cur = pending = None       # a new month header follows the divider
            bank, desc = False, ""
            continue
        bm = BANK_DATE_RE.match(ln)
        if bm and month_num(bm.group(2)):
            try:
                cur = Date(int(bm.group(3)), month_num(bm.group(2)), int(bm.group(1)))
            except ValueError:
                warnings.append(f"Couldn't read the date '{ln}'.")
                cur = None
                bank = False
                continue
            bank, desc, pending = True, "", None
            continue
        m = PAY_MONTH_RE.match(ln)
        if m and month_num(m.group(1)):
            month, year, cur, pending = month_num(m.group(1)), int(m.group(2)), None, None
            pending_weekday = None
            bank, desc = False, ""
            continue
        # Some copied lesson lists put the month and year on separate lines.
        standalone_month = re.fullmatch(r"([A-Za-z]{3,9})\.?", ln)
        if standalone_month and month_num(standalone_month.group(1)):
            month, year, cur, pending = month_num(standalone_month.group(1)), None, None, None
            pending_weekday = None
            bank, desc = False, ""
            continue
        if month is not None and year is None and re.fullmatch(r"\d{4}", ln):
            year = int(ln)
            continue
        m = PAY_DAY_RE.match(ln)
        if m and m.group(1)[:3].lower() in _WEEKDAYS:
            bank, desc = False, ""
            if month is None:
                warnings.append(f"'{ln}' has no month above it.")
                continue
            try:
                cur = Date(year, month, int(m.group(2)))
            except ValueError:
                warnings.append(f"Couldn't read the date '{ln}'.")
                cur = None
                continue
            if not weekday_ok(cur, m.group(1)):
                warnings.append(f"{ln}: that day of the week doesn't match the date. Please check it.")
            pending = None
            pending_weekday = None
            continue
        # Also accept a weekday and day number on separate lines (e.g. Fri / 21).
        if re.fullmatch(r"[A-Za-z]{3,9}\.?", ln) and ln[:3].lower() in _WEEKDAYS:
            pending_weekday = ln
            continue
        if month is not None and year is not None and re.fullmatch(r"\d{1,2}", ln):
            try:
                cur = Date(year, month, int(ln))
            except ValueError:
                warnings.append(f"Couldn't read the date '{ln}'.")
                cur = None
                pending_weekday = None
                continue
            if pending_weekday and not weekday_ok(cur, pending_weekday):
                warnings.append(f"{pending_weekday} {ln}: that day of the week doesn't match the date. Please check it.")
            pending_weekday = None
            pending = None
            bank, desc = False, ""
            continue
        tr = parse_time_range(ln)
        if tr and not bank:
            pending = tr
            continue
        amount = money_match(ln)
        if amount is not None and cur:
            if bank:
                note = desc
            else:
                note = (f"Lesson {fmt_clock(pending[0])} - {fmt_clock(pending[0] + pending[1])}" if pending else "")
            items.append({"date": cur, "amount": amount, "note": note, "upcoming": mode == "upcoming", "bank": bank})
            pending, desc = None, ""
            continue
        if bank and cur:
            desc = (desc + " " + ln).strip()          # payer / description line(s)
            continue
        warnings.append(f"Couldn't read the line: {ln}")
    return items, warnings


class Store:
    def __init__(self):
        self.con = connect()

    # ---- students
    def students(self):
        return self.con.execute("SELECT * FROM students").fetchall()

    def student(self, sid):
        return self.con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()

    def save_student(self, sid, name, rate, folder, notes, email=None):
        """email=None leaves the stored address alone (used by the import paths)."""
        if sid is None:
            cur = self.con.execute(
                "INSERT INTO students(name, rate, folder, notes, email) VALUES (?,?,?,?,?)",
                (name, rate, folder, notes, email or ""))
            sid = cur.lastrowid
        else:
            old = self.student(sid)
            if email is None:
                email = old["email"] or ""
            self.con.execute("UPDATE students SET name=?, rate=?, folder=?, notes=?, email=? WHERE id=?",
                             (name, rate, folder, notes, email, sid))
            if old["name"] != name:
                stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self.con.execute("UPDATE lessons SET local_updated_at=? WHERE student_id=?", (stamp, sid))
            if old["rate"] == 0 and rate > 0:   # fill in rates for imported lessons
                self.con.execute("UPDATE lessons SET rate=? WHERE student_id=? AND rate=0", (rate, sid))
        self.con.commit()
        return sid

    def delete_student(self, sid):
        self.con.execute("DELETE FROM students WHERE id=?", (sid,))
        self.con.commit()

    # ---- lessons
    def lessons(self, sid):
        return self.con.execute("SELECT * FROM lessons WHERE student_id=? ORDER BY start DESC", (sid,)).fetchall()

    def save_lesson(self, lid, sid, start, duration, rate, status, pdf, notes):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if lid is None:
            self.con.execute(
                "INSERT INTO lessons(student_id,start,duration,rate,status,pdf,notes,local_updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (sid, start, duration, rate, status, pdf, notes, stamp))
        else:
            self.con.execute(
                "UPDATE lessons SET start=?,duration=?,rate=?,status=?,pdf=?,notes=?,local_updated_at=? WHERE id=?",
                (start, duration, rate, status, pdf, notes, stamp, lid))
        self.con.commit()

    def calendar_lessons(self):
        """Every lesson with its student's name, for the Calendar page."""
        return self.con.execute(
            "SELECT lessons.*, students.name AS student_name FROM lessons "
            "JOIN students ON students.id=lessons.student_id").fetchall()

    def set_student_email(self, sid, email):
        self.con.execute("UPDATE students SET email=? WHERE id=?", (email, sid))
        self.con.commit()

    def lesson(self, lid):
        return self.con.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()

    def lesson_at(self, sid, start):
        return self.con.execute("SELECT * FROM lessons WHERE student_id=? AND start=? ORDER BY id LIMIT 1",
                                (sid, start)).fetchone()

    def lesson_on_day(self, sid, day, include_date_only=True):
        """Prefer the timed lesson for a day, then a Calendar-linked/date-only lesson."""
        rows = self.con.execute(
            "SELECT * FROM lessons WHERE student_id=? AND substr(start,1,10)=? "
            "ORDER BY CASE WHEN status='cancelled' THEN 1 ELSE 0 END, "
            "CASE WHEN substr(start,12,5)='00:00' THEN 1 ELSE 0 END, "
            "CASE WHEN calendar_event_id<>'' THEN 0 ELSE 1 END, start,id", (sid, day)).fetchall()
        if not include_date_only:
            rows = [r for r in rows if r["start"][11:16] != "00:00"]
        return rows[0] if rows else None

    def _merge_pdf_lesson_into(self, keep_id, duplicate_id):
        """Fold a date-only PDF record into a timed lesson while retaining its useful fields."""
        keep, duplicate = self.lesson(keep_id), self.lesson(duplicate_id)
        if not keep or not duplicate or keep_id == duplicate_id:
            return False
        if keep["student_id"] != duplicate["student_id"] or keep["start"][:10] != duplicate["start"][:10]:
            return False
        if duplicate["calendar_event_id"] and keep["calendar_event_id"] and duplicate["calendar_event_id"] != keep["calendar_event_id"]:
            return False
        pdf = keep["pdf"] or duplicate["pdf"] or ""
        if keep["pdf"] and duplicate["pdf"] and norm(keep["pdf"]) != norm(duplicate["pdf"]):
            self.con.execute("INSERT OR IGNORE INTO additional_pdfs(student_id,pdf) VALUES (?,?)",
                              (keep["student_id"], str(Path(duplicate["pdf"]).resolve())))
        local_stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        notes = [x.strip() for x in (keep["notes"] or "", duplicate["notes"] or "") if x.strip()]
        merged_notes = "\n".join(dict.fromkeys(notes))
        self.con.execute(
            "UPDATE lessons SET pdf=?,notes=?,rate=CASE WHEN rate=0 THEN ? ELSE rate END,"
            "calendar_event_id=CASE WHEN calendar_event_id='' THEN ? ELSE calendar_event_id END,"
            "meet_url=CASE WHEN meet_url='' THEN ? ELSE meet_url END,"
            "status=CASE WHEN calendar_event_id='' AND ?<>'' THEN ? ELSE status END,"
            "invite_sent_at=CASE WHEN invite_sent_at='' THEN ? ELSE invite_sent_at END,"
            "google_updated_at=CASE WHEN google_updated_at='' THEN ? ELSE google_updated_at END,"
            "local_updated_at=CASE WHEN calendar_event_id='' AND ?<>'' THEN ? ELSE local_updated_at END WHERE id=?",
            (pdf, merged_notes, duplicate["rate"], duplicate["calendar_event_id"], duplicate["meet_url"],
             duplicate["calendar_event_id"], duplicate["status"], duplicate["invite_sent_at"],
             duplicate["google_updated_at"], duplicate["calendar_event_id"], local_stamp, keep_id))
        self.con.execute("DELETE FROM lessons WHERE id=?", (duplicate_id,))
        return True

    def merge_untimed_pdf_duplicates(self, sid):
        """Merge PDF-only midnight records into a timed lesson on the same day."""
        rows = self.con.execute(
            "SELECT * FROM lessons WHERE student_id=? AND substr(start,12,5)='00:00' AND pdf<>'' "
            "ORDER BY start,id", (sid,)).fetchall()
        merged = 0
        for row in rows:
            timed = self.con.execute(
                "SELECT * FROM lessons WHERE student_id=? AND substr(start,1,10)=? "
                "AND substr(start,12,5)<>'00:00' ORDER BY CASE WHEN calendar_event_id<>'' THEN 0 ELSE 1 END,start,id LIMIT 1",
                (sid, row["start"][:10])).fetchone()
            if timed and self._merge_pdf_lesson_into(timed["id"], row["id"]):
                merged += 1
        if merged:
            self.con.commit()
        return merged

    def _lesson_for_event(self, sid, start_s, event_id=""):
        row = self.con.execute("SELECT * FROM lessons WHERE calendar_event_id=?", (event_id,)).fetchone() if event_id else None
        if row:
            return row
        row = self.lesson_at(sid, start_s)
        if row and (not row["calendar_event_id"] or row["calendar_event_id"] == event_id):
            return row
        day = start_s[:10]
        return self.con.execute(
            "SELECT * FROM lessons WHERE student_id=? AND substr(start,1,10)=? "
            "AND substr(start,12,5)<>'00:00' AND calendar_event_id='' ORDER BY start,id LIMIT 1",
            (sid, day)).fetchone() or self.con.execute(
            "SELECT * FROM lessons WHERE student_id=? AND substr(start,1,10)=? "
            "AND substr(start,12,5)='00:00' AND pdf<>'' AND (calendar_event_id='' OR calendar_event_id=?) "
            "ORDER BY id LIMIT 1", (sid, day, event_id)).fetchone()

    def attach_event(self, sid, start, duration, event_id, meet_url):
        """Record a Calendar event on the lesson at this time (creating the lesson if needed). Returns lesson id."""
        row = self._lesson_for_event(sid, start, event_id)
        if row:
            self.con.execute("UPDATE lessons SET start=?,duration=?,calendar_event_id=?,meet_url=? WHERE id=?",
                             (start, duration, event_id, meet_url, row["id"]))
            lid = row["id"]
        else:
            rate = self.student(sid)["rate"]
            cur = self.con.execute(
                "INSERT INTO lessons(student_id,start,duration,rate,status,calendar_event_id,meet_url) "
                "VALUES (?,?,?,?,'scheduled',?,?)", (sid, start, duration, rate, event_id, meet_url))
            lid = cur.lastrowid
        self.con.commit()
        return lid

    def apply_calendar_event(self, sid, event):
        """Upsert one Google tutoring event without disturbing notes, PDFs, or lesson rates."""
        start_s = event["start"].strftime(DT_FMT)
        row = self.con.execute("SELECT * FROM lessons WHERE calendar_event_id=?", (event["event_id"],)).fetchone()
        if not row:
            row = self._lesson_for_event(sid, start_s, event["event_id"])
        google_updated = event.get("updated", "")
        if row:
            self.con.execute(
                "UPDATE lessons SET student_id=?,start=?,duration=?,status=?,calendar_event_id=?,meet_url=?,"
                "google_updated_at=?,local_updated_at=? WHERE id=?",
                (sid, start_s, event["duration"], event["status"], event["event_id"], event.get("meet_url", ""),
                 google_updated, google_updated or datetime.now(timezone.utc).isoformat(timespec="seconds"), row["id"]))
            lid = row["id"]
        else:
            rate = self.student(sid)["rate"]
            cur = self.con.execute(
                "INSERT INTO lessons(student_id,start,duration,rate,status,calendar_event_id,meet_url,"
                "google_updated_at,local_updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, start_s, event["duration"], rate, event["status"], event["event_id"], event.get("meet_url", ""),
                 google_updated, google_updated or datetime.now(timezone.utc).isoformat(timespec="seconds")))
            lid = cur.lastrowid
        self.con.commit()
        self.merge_untimed_pdf_duplicates(sid)
        return lid

    def apply_calendar_missing(self, event_id):
        self.con.execute("UPDATE lessons SET status='cancelled' WHERE calendar_event_id=?", (event_id,))
        self.con.commit()

    def set_meet_url(self, lid, meet_url):
        self.con.execute("UPDATE lessons SET meet_url=? WHERE id=?", (meet_url, lid))
        self.con.commit()

    def mark_invite_sent(self, lid):
        self.con.execute("UPDATE lessons SET invite_sent_at=? WHERE id=?",
                         (datetime.now().strftime(DT_FMT), lid))
        self.con.commit()

    def unsent_invites(self, sid):
        """Future lessons that have a Calendar event but no invitation email yet."""
        now = datetime.now().strftime(DT_FMT)
        return self.con.execute(
            "SELECT * FROM lessons WHERE student_id=? AND calendar_event_id<>'' AND invite_sent_at='' "
            "AND status='scheduled' AND start>=? ORDER BY start", (sid, now)).fetchall()

    def setting(self, key, default=""):
        r = self.con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES (?,?)", (key, value))
        self.con.commit()

    def delete_lesson(self, lid):
        self.con.execute("DELETE FROM lessons WHERE id=?", (lid,))
        self.con.commit()

    # ---- payments
    def payments(self, sid):
        return self.con.execute("SELECT * FROM payments WHERE student_id=? ORDER BY date DESC, id DESC", (sid,)).fetchall()

    def save_payment(self, pid, sid, date, amount, kind, note):
        if pid is None:
            self.con.execute("INSERT INTO payments(student_id,date,amount,kind,note) VALUES (?,?,?,?,?)",
                             (sid, date, amount, kind, note))
        else:
            self.con.execute("UPDATE payments SET date=?,amount=?,kind=?,note=? WHERE id=?",
                             (date, amount, kind, note, pid))
        self.con.commit()

    def delete_payment(self, pid):
        self.con.execute("DELETE FROM payments WHERE id=?", (pid,))
        self.con.commit()

    # ---- derived numbers
    def stats(self, sid):
        lessons = self.lessons(sid)
        payments = self.payments(sid)
        earned = sum(lesson_value(l) for l in lessons if effective_status(l) == "completed")
        paid = sum(p["amount"] for p in payments if p["kind"] == "payment")
        refunds = sum(p["amount"] for p in payments if p["kind"] == "refund")
        bonuses = sum(p["amount"] for p in payments if p["kind"] == "bonus")
        now = datetime.now()
        future = [l for l in lessons if l["status"] != "cancelled" and datetime.strptime(l["start"], DT_FMT) >= now]
        past = [l for l in lessons if effective_status(l) == "completed" and datetime.strptime(l["start"], DT_FMT) < now]
        return {
            "done": sum(1 for l in lessons if effective_status(l) == "completed"),
            "earned": earned, "paid": paid, "refunds": refunds, "bonuses": bonuses,
            # bonuses are tips: they never reduce what is owed for lessons
            "balance": paid - refunds - earned,
            "next": min((l["start"] for l in future), default=None),
            "last": max((l["start"] for l in past), default=None),
        }

    # ---- importing
    def sync_pdfs(self, sid, as_additional=False):
        """Link each new PDF to its same-day lesson, preferring a lesson with a real start time."""
        s = self.student(sid)
        if not s or not s["folder"] or not Path(s["folder"]).is_dir():
            return 0
        self.merge_untimed_pdf_duplicates(sid)
        linked = {norm(r["pdf"]) for r in self.con.execute(
            "SELECT pdf FROM lessons WHERE student_id=? AND pdf != ''", (sid,))}
        linked |= {norm(r["pdf"]) for r in self.con.execute(
            "SELECT pdf FROM additional_pdfs WHERE student_id=?", (sid,))}
        added = 0
        for pdf in pdfs_in(s["folder"]):
            pdf_path = str(pdf.resolve())
            if norm(pdf_path) in linked:
                continue
            if as_additional:
                cur = self.con.execute("INSERT OR IGNORE INTO additional_pdfs(student_id,pdf) VALUES (?,?)",
                                       (sid, pdf_path))
                added += cur.rowcount
                continue
            when, _ = date_from_filename(pdf)
            target = self.lesson_on_day(sid, when.strftime("%Y-%m-%d"))
            if target:
                added += self._link_pdf_to_lesson(target, pdf_path)
            else:
                status = "scheduled" if when > datetime.now() else "completed"
                self.con.execute(
                    "INSERT INTO lessons(student_id,start,duration,rate,status,pdf) VALUES (?,?,?,?,?,?)",
                    (sid, when.strftime(DT_FMT), 60, s["rate"], status, pdf_path))
                added += 1
            linked.add(norm(pdf_path))
        self.con.commit()
        return added

    def sync_all_pdfs(self, as_additional=False):
        return sum(self.sync_pdfs(s["id"], as_additional) for s in self.students())

    def add_additional_pdfs(self, sid, paths):
        added = 0
        for path in paths:
            cur = self.con.execute("INSERT OR IGNORE INTO additional_pdfs(student_id,pdf) VALUES (?,?)",
                                   (sid, str(Path(path).resolve())))
            added += cur.rowcount
        self.con.commit()
        return added

    def additional_pdfs(self, sid):
        return self.con.execute("SELECT * FROM additional_pdfs WHERE student_id=? ORDER BY pdf", (sid,)).fetchall()

    def _link_pdf_to_lesson(self, lesson, path):
        """Attach a PDF to a lesson; keep a second same-day PDF as an additional reference."""
        path = str(Path(path).resolve())
        if lesson["pdf"] and norm(lesson["pdf"]) == norm(path):
            return 0
        if not lesson["pdf"]:
            self.con.execute("UPDATE lessons SET pdf=? WHERE id=?", (path, lesson["id"]))
            return 1
        cur = self.con.execute("INSERT OR IGNORE INTO additional_pdfs(student_id,pdf) VALUES (?,?)",
                               (lesson["student_id"], path))
        return cur.rowcount

    def attach_pdfs(self, sid, paths):
        s = self.student(sid)
        self.merge_untimed_pdf_duplicates(sid)
        linked = {norm(r["pdf"]) for r in self.con.execute(
            "SELECT pdf FROM lessons WHERE student_id=? AND pdf != ''", (sid,))}
        linked |= {norm(r["pdf"]) for r in self.con.execute(
            "SELECT pdf FROM additional_pdfs WHERE student_id=?", (sid,))}
        added = 0
        for p in paths:
            if norm(p) in linked:
                continue
            when, _ = date_from_filename(p)
            pdf_path = str(Path(p).resolve())
            target = self.lesson_on_day(sid, when.strftime("%Y-%m-%d"))
            if target:
                added += self._link_pdf_to_lesson(target, pdf_path)
            else:
                status = "scheduled" if when > datetime.now() else "completed"
                self.con.execute(
                    "INSERT INTO lessons(student_id,start,duration,rate,status,pdf) VALUES (?,?,?,?,?,?)",
                    (sid, when.strftime(DT_FMT), 60, s["rate"], status, pdf_path))
                added += 1
            linked.add(norm(pdf_path))
        self.con.commit()
        return added

    def import_folder(self, folder, as_additional=False):
        """
        A folder with PDFs directly inside it is ONE student (name = folder name).
        A folder with no PDFs directly inside but with sub-folders is a parent:
        each sub-folder becomes a student.
        Returns (new_students, pdfs_added).
        """
        folder = Path(folder)
        direct_pdfs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
        subdirs = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
        targets = [folder] if (direct_pdfs or not subdirs) else subdirs
        new_students = pdfs_added = 0
        for t in targets:
            existing = self.con.execute(
                "SELECT id FROM students WHERE lower(name)=lower(?)", (t.name,)).fetchone()
            if existing:
                sid = existing["id"]
                self.con.execute("UPDATE students SET folder=? WHERE id=?", (str(t), sid))
                self.con.commit()
            else:
                sid = self.save_student(None, t.name, 0.0, str(t), "")
                new_students += 1
            pdfs_added += self.sync_pdfs(sid, as_additional=as_additional)
        return new_students, pdfs_added

    # ---- which PDFs are already tied to a student
    def linked_pdf_paths(self):
        paths = set()
        for q in ("SELECT pdf FROM lessons WHERE pdf != ''", "SELECT pdf FROM additional_pdfs"):
            for r in self.con.execute(q):
                paths.add(norm(r["pdf"]))
                try:
                    paths.add(norm(str(Path(r["pdf"]).resolve())))
                except OSError:
                    pass
        return paths

    # ---- pasted calendar sessions
    def find_student(self, name):
        n = name.strip().lower()
        rows = self.students()
        for s in rows:
            if s["name"].strip().lower() == n:
                return s["id"]
        close = [s["id"] for s in rows if s["name"].strip().lower().startswith(n + " ")
                 or n.startswith(s["name"].strip().lower() + " ")]
        return close[0] if len(close) == 1 else None

    def plan_lessons(self, items):
        """Decide what each pasted session would do: new / dup / merge (fills the time into a PDF lesson) / skip."""
        rows, seen, existing, pool = [], set(), {}, {}
        for it in items:
            start = it["start"].strftime(DT_FMT)
            row = {"kind": "new", "sid": None, "name": it["name"] or it["title"], "start": start,
                   "duration": it["duration"], "merge_id": None, "note": "", "new_student": False}
            rows.append(row)
            if not it["name"]:
                row["kind"], row["note"] = "skip", "Not a 'Tutoring <name>' event"
                continue
            sid = self.find_student(it["name"])
            row["sid"] = sid
            if sid:
                row["name"] = self.student(sid)["name"]
                if sid not in existing:
                    existing[sid], pool[sid] = Counter(), {}
                    for l in self.con.execute("SELECT id, start FROM lessons WHERE student_id=?", (sid,)):
                        existing[sid][l["start"]] += 1
                        if l["start"].endswith(" 00:00"):          # date-only lesson created from a PDF
                            pool[sid].setdefault(l["start"][:10], []).append(l["id"])
            else:
                row["new_student"] = True
            key = (sid or it["name"].lower(), start)
            if key in seen:
                row["kind"], row["note"] = "dup", "Repeated in the pasted text"
            elif sid and existing[sid][start] > 0:
                existing[sid][start] -= 1
                row["kind"], row["note"] = "dup", "Already in the app"
            elif sid and pool[sid].get(start[:10]):
                row["kind"], row["merge_id"] = "merge", pool[sid][start[:10]].pop(0)
                row["note"] = "Adds the time to the PDF lesson on this date"
            elif sid is None:
                row["note"] = "New student, rate $0"
            seen.add(key)
        return rows

    def apply_lesson_plan(self, rows, add_duplicates, create_missing):
        added = updated = created = skipped = 0
        made = {}
        now = datetime.now()
        for r in rows:
            if r["kind"] == "skip" or (r["kind"] == "dup" and not add_duplicates):
                continue
            if r["kind"] == "merge":
                self.con.execute("UPDATE lessons SET start=?, duration=? WHERE id=?",
                                 (r["start"], r["duration"], r["merge_id"]))
                updated += 1
                continue
            sid = r["sid"]
            if sid is None:
                if not create_missing:
                    skipped += 1
                    continue
                key = r["name"].lower()
                if key not in made:
                    made[key] = self.save_student(None, r["name"], 0.0, "", "")
                    created += 1
                sid = made[key]
            rate = self.student(sid)["rate"]
            status = "scheduled" if datetime.strptime(r["start"], DT_FMT) >= now else "completed"
            self.con.execute(
                "INSERT INTO lessons(student_id,start,duration,rate,status,pdf,notes) VALUES (?,?,?,?,?,?,?)",
                (sid, r["start"], r["duration"], rate, status, "", ""))
            added += 1
        self.con.commit()
        return added, updated, created, skipped

    # ---- graph data
    def graph_data(self, sid):
        """(lesson_events, payment_events), each a list of (datetime, amount).
        Lessons: completed lessons only (cancelled and upcoming are not charged yet).
        Payments: money received minus refunds. Bonuses are tips, so they are left out."""
        lessons = [(datetime.strptime(l["start"], DT_FMT), lesson_value(l))
                   for l in self.lessons(sid) if effective_status(l) == "completed"]
        payments = [(datetime.strptime(p["date"], "%Y-%m-%d"), p["amount"] if p["kind"] == "payment" else -p["amount"])
                    for p in self.payments(sid) if p["kind"] in ("payment", "refund")]
        return sorted(lessons), sorted(payments)

    # ---- pasted payments (always for one student)
    def plan_payments(self, sid, items):
        existing = Counter((p["date"], round(p["amount"], 2)) for p in self.payments(sid) if p["kind"] == "payment")
        rows, seen = [], set()
        for it in items:
            d, amt = it["date"].strftime("%Y-%m-%d"), round(it["amount"], 2)
            row = {"kind": "new", "date": d, "amount": amt, "lesson": it["note"], "note": ""}
            rows.append(row)
            if it.get("upcoming") or it["date"] > Date.today():
                row["kind"], row["note"] = "skip", "Upcoming lesson, not paid yet"
                continue
            if amt <= 0:
                row["kind"] = "skip"
                row["note"] = "$0, nothing was paid" if amt == 0 else "Negative amount, add refunds by hand"
                continue
            key = (d, amt, it["note"])
            if key in seen:
                row["kind"], row["note"] = "dup", "Repeated in the pasted text"
            elif existing[(d, amt)] > 0:
                existing[(d, amt)] -= 1
                row["kind"], row["note"] = "dup", "Already recorded on this date"
            seen.add(key)
        return rows

    def apply_payment_plan(self, sid, rows, add_duplicates):
        added = 0
        for r in rows:
            if r["kind"] == "skip" or (r["kind"] == "dup" and not add_duplicates):
                continue
            self.con.execute("INSERT INTO payments(student_id,date,amount,kind,note) VALUES (?,?,?,?,?)",
                             (sid, r["date"], r["amount"], "payment", r["lesson"]))
            added += 1
        self.con.commit()
        return added


# --------------------------------------------------------------------------
# Google Calendar + Gmail (official APIs, OAuth 2.0 "Desktop app" flow)
# --------------------------------------------------------------------------
# Credentials and the sign-in token are kept OUTSIDE the project folder so they
# can never be committed to GitHub by accident:
#   Windows: %APPDATA%\TutoringOrganiser
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]
TIMEZONE = "Pacific/Auckland"
GOOGLE_DIR = DATA_DIR
CREDENTIALS_FILE = GOOGLE_DIR / "google_credentials.json"
TOKEN_FILE = GOOGLE_DIR / "google_token.json"
# Preserve the existing OAuth setup from older installations.
_legacy_google_dir = USER_DATA_ROOT / "TutorTracker"
for _name in ("google_credentials.json", "google_token.json"):
    _old, _new = _legacy_google_dir / _name, GOOGLE_DIR / _name
    if not _new.exists() and _old.is_file():
        shutil.copy2(_old, _new)
PIP_GOOGLE = "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MEET_PENDING = "[Meet link pending]"
DEFAULT_SUBJECT = "Tutoring lesson \u2013 {date} at {time}"
DEFAULT_MESSAGE = (
    "Hi {first_name},\n\n"
    "Your tutoring lesson is booked for {date} at {time} (New Zealand time), "
    "and will run for {duration}.\n\n"
    "Join on Google Meet:\n{meet_link}\n\n"
    "See you then!"
)


class GoogleError(Exception):
    """An error whose message is already fit to show the user."""
    def __init__(self, message, needs_signin=False):
        super().__init__(message)
        self.needs_signin = needs_signin


def parse_emails(text):
    """'a@x.com, b@y.com' -> ['a@x.com', 'b@y.com'] (de-duplicated, order kept)."""
    out, seen = [], set()
    for p in re.split(r"[,;\s]+", text or ""):
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def invalid_emails(parts):
    return [p for p in parts if not EMAIL_RE.match(p)]


def fmt_duration(minutes):
    h, m = divmod(int(minutes), 60)
    bits = ([f"{h} hour" + ("s" if h != 1 else "")] if h else []) + ([f"{m} minutes"] if m else [])
    return " ".join(bits) or "0 minutes"


def fmt_clock_time(d):
    return d.strftime("%I:%M %p").lstrip("0").lower()


def render_invite(subject_tpl, message_tpl, name, start, duration, meet_url):
    """Fill {placeholders}. Unknown {words} are left alone (no KeyErrors from stray braces)."""
    ctx = {
        "name": name,
        "first_name": (name.split() or [name])[0] if name else "",
        "date": f"{start:%A} {start.day} {start:%B %Y}",
        "time": fmt_clock_time(start),
        "duration": fmt_duration(duration),
        "meet_link": meet_url or MEET_PENDING,
    }
    def fill(t):
        for k, v in ctx.items():
            t = t.replace("{" + k + "}", v)
        return t
    return fill(subject_tpl), fill(message_tpl)


def render_invite_html(html_tpl, name, start, duration, meet_url):
    values = {
        "name": name,
        "first_name": (name.split() or [name])[0] if name else "",
        "date": f"{start:%A} {start.day} {start:%B %Y}",
        "time": fmt_clock_time(start),
        "duration": fmt_duration(duration),
        "meet_link": meet_url or MEET_PENDING,
    }
    for key, value in values.items():
        html_tpl = html_tpl.replace("{" + key + "}", html.escape(str(value)))
    return html_tpl


def inline_template_images(store):
    try:
        value = json.loads(store.setting("invite_inline_images", "{}"))
        return {str(cid): str(path) for cid, path in value.items()
                if isinstance(cid, str) and isinstance(path, str)} if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def register_inline_image_resources(editor, images):
    for cid, path in images.items():
        if Path(path).is_file():
            editor.document().addResource(QTextDocument.ImageResource, QUrl("cid:" + cid), QImage(path))


def auckland_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    except Exception:            # no tzdata on this machine: fall back to the PC clock
        return datetime.now()


# ---- credentials / sign-in ------------------------------------------------
def _read_token_info():
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _token_has_scopes(info):
    return bool(info) and set(SCOPES) <= set(info.get("scopes") or [])


def google_status():
    """Cheap local check (no network): missing_libs / no_credentials / needs_signin / ready."""
    if not HAVE_GOOGLE:
        return "missing_libs"
    if not CREDENTIALS_FILE.exists():
        return "no_credentials"
    if not _token_has_scopes(_read_token_info()):
        return "needs_signin"
    return "ready"


def install_credentials(src_path):
    """Validate the downloaded OAuth client JSON and copy it into GOOGLE_DIR."""
    try:
        data = json.loads(Path(src_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GoogleError("That file couldn't be read as JSON. Choose the file you downloaded from "
                          "Google Cloud (it's usually called client_secret_....json).")
    if "installed" not in data:
        if "web" in data:
            raise GoogleError("That is a 'Web application' client. In Google Cloud > Credentials, create an "
                              "OAuth client ID of type 'Desktop app' and download that one instead.")
        raise GoogleError("That doesn't look like a Google OAuth client file (no 'installed' section).")
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_path, CREDENTIALS_FILE)
    _restrict(CREDENTIALS_FILE)
    TOKEN_FILE.unlink(missing_ok=True)          # a new client invalidates any old token


def _restrict(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _save_token(creds):
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    _restrict(TOKEN_FILE)


def sign_out():
    TOKEN_FILE.unlink(missing_ok=True)


def google_credentials(interactive=True):
    """Valid credentials: reuse the saved token, refresh it, or (if interactive) open the browser to sign in.
    Missing scopes (e.g. after a new feature needs one) trigger a fresh sign-in automatically."""
    if not HAVE_GOOGLE:
        raise GoogleError(f"The Google libraries aren't installed. Run:\n{PIP_GOOGLE}")
    if not CREDENTIALS_FILE.exists():
        raise GoogleError("No Google credentials file has been chosen yet. Use 'Connect Google' first.",
                          needs_signin=True)
    creds = None
    info = _read_token_info()
    if _token_has_scopes(info):
        try:
            creds = GoogleCredentials.from_authorized_user_info(info, SCOPES)
        except (ValueError, KeyError):
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            _save_token(creds)
            return creds
        except RefreshError:                    # revoked, or expired (7-day limit while the app is in 'Testing')
            creds = None
            sign_out()
    if not interactive:
        raise GoogleError("Google sign-in has expired. Press 'Connect Google' to sign in again.", needs_signin=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    kwargs = dict(port=0, prompt="consent", authorization_prompt_message="")
    try:
        creds = flow.run_local_server(timeout_seconds=300, **kwargs)
    except TypeError:                           # older google-auth-oauthlib without timeout_seconds
        creds = flow.run_local_server(**kwargs)
    if not _token_has_scopes({"scopes": list(creds.scopes or [])}):
        raise GoogleError("Google sign-in finished, but not every permission was granted. Sign in again and "
                          "tick BOTH the Calendar and Gmail checkboxes.", needs_signin=True)
    _save_token(creds)
    return creds


def _service(name, version, creds):
    # Built fresh for each background job: httplib2 objects aren't thread-safe.
    return google_build(name, version, credentials=creds, cache_discovery=False)


# ---- Calendar -------------------------------------------------------------
def meet_from_event(ev):
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for ep in (ev.get("conferenceData") or {}).get("entryPoints") or []:
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    return ""


def _meet_request_status(ev):
    return (((ev.get("conferenceData") or {}).get("createRequest") or {}).get("status") or {}).get("statusCode", "")


def _meet_request_body():
    return {"conferenceData": {"createRequest": {
        "requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}


def wait_for_meet(cal, event_id, ev, attempts=15, delay=1.0, sleep=time.sleep):
    """Google creates Meet links asynchronously: the insert reply can say 'pending'. Poll until the link
    exists. If the request failed (or was never made) ask once more. Returns (event, url); url is '' if
    Google is still working after all attempts - the event is fine, just check again later."""
    asked_again = False
    for _ in range(attempts):
        url = meet_from_event(ev)
        if url:
            return ev, url
        status = _meet_request_status(ev)
        if status != "pending":
            if asked_again:
                raise GoogleError("Google couldn't generate a Meet link for this event "
                                  f"(status: {status or 'none'}). Your Calendar event exists; try 'Check for Meet "
                                  "Link' again in a minute.")
            ev = cal.events().patch(calendarId="primary", eventId=event_id, conferenceDataVersion=1,
                                    sendUpdates="none", body=_meet_request_body()).execute()
            asked_again = True
            continue
        sleep(delay)
        ev = cal.events().get(calendarId="primary", eventId=event_id).execute()
    return ev, meet_from_event(ev)


def calendar_create_event(creds, *, event_id, summary, description, start, duration, emails,
                          service=None, sleep=time.sleep):
    """Create (or, if event_id already exists, resume) one event with a Meet link.
    sendUpdates='none' stops Google emailing the attendee: the only email is the one you review and send.
    The caller-chosen event_id makes the insert idempotent - a retry after a lost reply can't duplicate it."""
    cal = service or _service("calendar", "v3", creds)
    end = start + timedelta(minutes=duration)
    body = {
        "id": event_id, "summary": summary, "description": description,
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "attendees": [{"email": e} for e in emails],
        **_meet_request_body(),
    }
    try:
        ev = cal.events().insert(calendarId="primary", body=body, conferenceDataVersion=1,
                                 sendUpdates="none").execute()
    except HttpError as e:
        if getattr(e.resp, "status", None) != 409:
            raise
        ev = cal.events().get(calendarId="primary", eventId=event_id).execute()   # created by an earlier attempt
        if ev.get("status") == "cancelled":
            raise GoogleError("That Calendar event was deleted in Google Calendar. Start a new invitation.")
    result = {"event_id": ev["id"], "html_link": ev.get("htmlLink", ""), "meet_url": "", "warning": ""}
    try:
        ev, result["meet_url"] = wait_for_meet(cal, ev["id"], ev, sleep=sleep)
    except Exception as e:      # the event exists; don't lose it just because the Meet lookup failed
        result["warning"] = friendly_error(e)
    return result


def calendar_resume_event(creds, event_id, service=None, sleep=time.sleep):
    """Look up an existing event and (re)fetch its Meet link. Never creates a new event."""
    cal = service or _service("calendar", "v3", creds)
    try:
        ev = cal.events().get(calendarId="primary", eventId=event_id).execute()
    except HttpError as e:
        if getattr(e.resp, "status", None) == 404:
            raise GoogleError("That event no longer exists in Google Calendar.")
        raise
    if ev.get("status") == "cancelled":
        raise GoogleError("That event was cancelled in Google Calendar.")
    ev, url = wait_for_meet(cal, event_id, ev, sleep=sleep)
    return {"event_id": event_id, "meet_url": url, "html_link": ev.get("htmlLink", ""), "warning": ""}


def calendar_find_student_events(creds, student_name, service=None):
    """Find upcoming primary-calendar bookings titled 'Tutoring <student name>'."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        now = datetime.now().astimezone()
    cal = service or _service("calendar", "v3", creds)
    token, found = None, []
    while True:
        response = cal.events().list(calendarId="primary", q=f"Tutoring {student_name}",
                                     timeMin=now.isoformat(),
                                     singleEvents=True, orderBy="startTime", maxResults=250, pageToken=token).execute()
        for ev in response.get("items", []):
            summary = (ev.get("summary") or "").strip()
            prefix = f"Tutoring {student_name}".casefold()
            remainder = summary.casefold()
            matches = remainder == prefix or any(remainder.startswith(prefix + d) for d in (" ", "-", "–", "—", ":", "|", "("))
            if matches and ev.get("status") != "cancelled":
                found.append(_remote_event_record(ev))
        token = response.get("nextPageToken")
        if not token:
            return found


def calendar_event_datetimes(ev):
    """Convert a Google event's timed or all-day bounds to naive Auckland datetimes."""
    try:
        from zoneinfo import ZoneInfo
        calendar_tz = ZoneInfo(TIMEZONE)
    except Exception:
        calendar_tz = datetime.now().astimezone().tzinfo
    def convert(value):
        if "T" not in value:
            return datetime.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(calendar_tz).replace(tzinfo=None)
        return parsed
    return (convert((ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")),
            convert((ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")))


def _utc_stamp(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return ""


def _calendar_tutoring_event(row, student_name, event_id=None):
    start = datetime.strptime(row["start"], DT_FMT)
    body = {
        "summary": f"Tutoring {student_name}",
        "description": f"Online tutoring lesson with {student_name}.",
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "end": {"dateTime": (start + timedelta(minutes=int(row["duration"]))).strftime("%Y-%m-%dT%H:%M:00"),
                "timeZone": TIMEZONE},
    }
    if row["status"] == "cancelled":
        body["status"] = "cancelled"
    if event_id:
        body["id"] = event_id
    return body


def _remote_event_record(ev):
    start, end = calendar_event_datetimes(ev)
    return {"event_id": ev["id"], "start": start,
            "duration": max(5, int(round((end - start).total_seconds() / 60))),
            "meet_url": meet_from_event(ev), "updated": _utc_stamp(ev.get("updated", "")),
            "status": "cancelled" if ev.get("status") == "cancelled" else "scheduled",
            "summary": ev.get("summary", "")}


def calendar_sync_tutoring(creds, lessons, students):
    """Two-way sync for primary-calendar items titled 'Tutoring <student>'.
    Local changes win if newer than the last Google update; otherwise Google changes win.
    Only upcoming, scheduled unlinked local lessons are created in Google Calendar.
    """
    cal = _service("calendar", "v3", creds)
    all_events, token = [], None
    while True:
        response = cal.events().list(calendarId="primary", q="Tutoring", singleEvents=True,
                                     showDeleted=True, maxResults=2500, pageToken=token).execute()
        all_events.extend(response.get("items", []))
        token = response.get("nextPageToken")
        if not token:
            break
    by_id = {e["id"]: e for e in all_events if e.get("id")}
    tutoring = [e for e in all_events if (e.get("summary") or "").casefold().startswith("tutoring ")]
    actions, consumed_local, consumed_remote, missing_ids = [], set(), set(), []

    def student_for_event(event):
        summary = (event.get("summary") or "").strip()
        if not summary.casefold().startswith("tutoring "):
            return None
        remainder = summary[len("Tutoring "):].strip().casefold()
        matches = [s for s in students if remainder == s["name"].strip().casefold()
                   or any(remainder.startswith(s["name"].strip().casefold() + delimiter)
                          for delimiter in (" ", "-", "–", "—", ":", "|", "("))]
        return max(matches, key=lambda s: len(s["name"])) if matches else None

    def push_local(row, student, event=None, deterministic_id=None):
        event_id = row.get("calendar_event_id") or (event or {}).get("id") or deterministic_id
        body = _calendar_tutoring_event(row, student["name"], event_id if not event else None)
        if event:
            if row["status"] == "cancelled":
                result = cal.events().patch(calendarId="primary", eventId=event["id"], body={"status": "cancelled"},
                                            sendUpdates="none").execute()
            else:
                result = cal.events().patch(calendarId="primary", eventId=event["id"], body=body,
                                            sendUpdates="none").execute()
        else:
            try:
                result = cal.events().insert(calendarId="primary", body=body, sendUpdates="none").execute()
            except HttpError as exc:
                if getattr(exc.resp, "status", None) != 409:
                    raise
                result = cal.events().get(calendarId="primary", eventId=event_id).execute()
        actions.append({"sid": student["id"], "event": _remote_event_record(result)})
        if event:
            consumed_remote.add(event["id"])
        consumed_local.add(row["id"])

    # Existing links are stable even if the event has moved.
    for row in lessons:
        event_id = row.get("calendar_event_id")
        if not event_id:
            continue
        event = by_id.get(event_id)
        student = next((s for s in students if s["id"] == row["student_id"]), None)
        if not student:
            continue
        if not event:
            missing_ids.append(event_id)
            consumed_local.add(row["id"])
            continue
        consumed_remote.add(event_id)
        if event.get("status") == "cancelled":
            actions.append({"sid": student["id"], "event": {
                "event_id": event_id, "start": datetime.strptime(row["start"], DT_FMT),
                "duration": row["duration"], "meet_url": row["meet_url"] or "",
                "updated": _utc_stamp(event.get("updated", "")), "status": "cancelled"}})
            consumed_local.add(row["id"])
            continue
        remote_updated = _utc_stamp(event.get("updated", ""))
        local_updated = _utc_stamp(row.get("local_updated_at", ""))
        event_student = student_for_event(event)
        if (not event_student or event_student["id"] != student["id"]) and local_updated and remote_updated and local_updated > remote_updated:
            push_local(row, student, event)
        elif not event_student:
            missing_ids.append(event_id)
            consumed_local.add(row["id"])
        elif row["status"] == "cancelled" and event.get("status") != "cancelled":
            push_local(row, student, event)
        elif local_updated and remote_updated and local_updated > remote_updated:
            push_local(row, student, event)
        else:
            actions.append({"sid": event_student["id"], "event": _remote_event_record(event)})
            consumed_local.add(row["id"])

    # Unlinked local bookings either pair by student+start with Google or get created if upcoming.
    unlinked = [r for r in lessons if not r.get("calendar_event_id") and r["id"] not in consumed_local]
    for event in tutoring:
        if event["id"] in consumed_remote:
            continue
        student = student_for_event(event)
        if not student or event.get("status") == "cancelled":
            continue
        remote_record = _remote_event_record(event)
        start_s = remote_record["start"].strftime(DT_FMT)
        local = next((r for r in unlinked if r["id"] not in consumed_local
                      and r["student_id"] == student["id"] and r["start"] == start_s), None)
        if local:
            local_updated = _utc_stamp(local.get("local_updated_at", ""))
            remote_updated = remote_record["updated"]
            if local_updated and remote_updated and local_updated > remote_updated:
                push_local(local, student, event)
            else:
                actions.append({"sid": student["id"], "event": remote_record})
                consumed_local.add(local["id"]); consumed_remote.add(event["id"])
        else:
            actions.append({"sid": student["id"], "event": remote_record})
            consumed_remote.add(event["id"])

    now = auckland_now()
    for row in unlinked:
        if row["id"] in consumed_local or row["status"] == "cancelled":
            continue
        start = datetime.strptime(row["start"], DT_FMT)
        if start < now:
            continue
        student = next((s for s in students if s["id"] == row["student_id"]), None)
        if student:
            deterministic_id = uuid.uuid5(uuid.NAMESPACE_URL, f"tutor-tracker-lesson-{row['id']}").hex
            push_local(row, student, deterministic_id=deterministic_id)
    return {"actions": actions, "missing_ids": missing_ids,
            "seen": len(tutoring), "matched": len(actions), "unmatched": 0}


def calendar_delete_event(creds, event_id, service=None):
    # A local-only lesson has nothing to delete remotely. Keep this guard at the
    # API boundary as well as in callers so an empty id can never reach Google.
    if not event_id:
        return False
    cal = service or _service("calendar", "v3", creds)
    try:
        cal.events().delete(calendarId="primary", eventId=event_id, sendUpdates="none").execute()
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        if status not in (404, 410):
            # A stale/malformed local event id can produce 400 instead of Google's usual
            # 404 for a deleted event. Confirm with get() before treating it as already gone.
            try:
                cal.events().get(calendarId="primary", eventId=event_id).execute()
            except HttpError as check_exc:
                if getattr(check_exc.resp, "status", None) not in (404, 410):
                    raise exc
            else:
                raise exc
    return True


# ---- Gmail ----------------------------------------------------------------
IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp"}
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.gif *.webp)"
MAX_ATTACH_BYTES = 15 * 1024 * 1024      # Gmail's limit is 25 MB for the whole message; base64 adds ~33%


def check_attachments(paths):
    """Raise GoogleError (with a readable message) if any attachment is missing, not an image, or too big."""
    total = 0
    for path in paths:
        pp = Path(path)
        if not pp.is_file():
            raise GoogleError(f"Can't find the attachment:\n{path}")
        if pp.suffix.lower() not in IMAGE_EXTS:
            raise GoogleError(f"Only PNG, JPG, GIF and WebP images can be attached:\n{pp.name}")
        total += pp.stat().st_size
    if total > MAX_ATTACH_BYTES:
        raise GoogleError(f"The attachments total {total / 1048576:.1f} MB. Keep them under "
                          f"{MAX_ATTACH_BYTES // 1048576} MB or Gmail may reject the email.")
    return total


def build_raw_email(to_list, subject, body, attachments=(), html_body="", inline_images=()):
    msg = EmailMessage()
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
        html_part = msg.get_payload()[-1]
        for path, content_id in inline_images:
            pp = Path(path)
            maintype, subtype = IMAGE_EXTS[pp.suffix.lower()].split("/")
            html_part.add_related(pp.read_bytes(), maintype=maintype, subtype=subtype, cid=f"<{content_id}>")
    for path in attachments:
        pp = Path(path)
        maintype, subtype = IMAGE_EXTS.get(pp.suffix.lower(), "image/png").split("/")
        msg.add_attachment(pp.read_bytes(), maintype=maintype, subtype=subtype, filename=pp.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def gmail_send(creds, to_list, subject, body, attachments=(), html_body="", inline_images=(), service=None):
    """Send an invitation with optional attachments and MIME-embedded images.
    Returns Gmail's message id."""
    check_attachments(list(attachments) + [p for p, _ in inline_images])
    svc = service or _service("gmail", "v1", creds)
    resp = svc.users().messages().send(
        userId="me", body={"raw": build_raw_email(to_list, subject, body, attachments,
                                                   html_body, inline_images)}).execute()
    if not resp.get("id"):
        raise GoogleError("Gmail didn't confirm that the email was sent. Check your Sent folder before retrying.")
    return resp["id"]


# ---- error wording --------------------------------------------------------
def needs_signin(exc):
    return isinstance(exc, GoogleError) and exc.needs_signin or (
        HAVE_GOOGLE and isinstance(exc, HttpError) and getattr(exc.resp, "status", None) == 401)


def friendly_error(exc):
    if isinstance(exc, GoogleError):
        return str(exc)
    name = type(exc).__name__
    if HAVE_GOOGLE and isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        reason, message = "", ""
        try:
            err = json.loads(exc.content.decode("utf-8"))["error"]
            message = err.get("message", "")
            reason = ((err.get("errors") or [{}])[0]).get("reason", "") or err.get("status", "")
        except Exception:
            pass
        low = (message + " " + reason).lower()
        if status == 401:
            return "Google rejected the saved sign-in. Press 'Connect Google' to sign in again."
        if status == 403 and ("has not been used" in low or "disabled" in low or reason in ("accessNotConfigured", "SERVICE_DISABLED")):
            return ("The Calendar or Gmail API isn't switched on for your Google Cloud project. In Google Cloud "
                    "Console > APIs & Services > Library, enable both 'Google Calendar API' and 'Gmail API', "
                    "wait a minute, then try again.")
        if status == 403 and ("insufficient" in low or reason == "insufficientPermissions"):
            return "This sign-in is missing a permission. Press 'Connect Google' and tick both permission boxes."
        if status in (403, 429) and "ratelimit" in low.replace(" ", ""):
            return "Google says you're sending requests too quickly. Wait a minute and try again."
        if status == 404:
            return "Google couldn't find that item (it may have been deleted)."
        if status >= 500:
            return "Google's servers had a problem. Wait a moment and try again."
        return f"Google returned an error ({status}): {message or reason or 'no details'}"
    if name == "RefreshError":
        return "Your Google sign-in has expired or was revoked. Press 'Connect Google' to sign in again."
    if isinstance(exc, (OSError, TimeoutError)) or name in ("ServerNotFoundError", "TransportError"):
        return "Couldn't reach Google. Check your internet connection and try again."
    if "scope has changed" in str(exc).lower():
        return "Google sign-in finished without every permission. Sign in again and tick both checkboxes."
    return f"Something went wrong: {exc}"


class Job(QObject):
    """Runs fn() on a background thread (so the window never freezes during sign-in or API calls)
    and delivers (result, error) back on the GUI thread."""
    done = Signal(object, object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            out = (self.fn(), None)
        except Exception as e:                  # reported to the user, never swallowed
            out = (None, e)
        self.done.emit(*out)


# --------------------------------------------------------------------------
# Theme (warm cream + terracotta)
# --------------------------------------------------------------------------
BG = "#F5F4EE"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#FAF9F5"
BORDER = "#E6E3D8"
TEXT = "#3D3929"
MUTED = "#8A8672"
ACCENT = "#C96442"
ACCENT_HOVER = "#B5573A"
SELECT = "#F3E6DE"
GOOD = "#5E7F4F"
BAD = "#B4452F"

STYLE = f"""
* {{ font-family: 'Segoe UI', sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-family: Georgia, 'Times New Roman', serif; font-size: 22px; }}
QLabel#studentName {{ font-family: Georgia, 'Times New Roman', serif; font-size: 24px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#statValue {{ font-size: 17px; font-weight: 600; }}
QLabel#statLabel {{ color: {MUTED}; font-size: 11px; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {SELECT}; selection-color: {TEXT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateTimeEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; selection-background-color: {SELECT}; selection-color: {TEXT}; }}
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: {SURFACE_ALT}; border-color: #D5D1C2; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#danger {{ color: {BAD}; }}
QPushButton:checked {{ background: {SELECT}; border-color: {ACCENT}; font-weight: 600; }}
QPushButton:disabled {{ color: #B9B5A3; background: {SURFACE_ALT}; border-color: {BORDER}; }}
QTableWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; gridline-color: transparent;
    outline: 0; selection-background-color: {SELECT}; selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid #F0EEE5; }}
QTableWidget::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QHeaderView::section {{
    background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 10px; color: {MUTED}; font-weight: 600;
}}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; margin-right: 4px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D9D5C6; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}
"""


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


class OverviewLineGraph(QWidget):
    """Small theme-matched line graph, drawn without optional chart packages."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.setMinimumHeight(170)

    def set_values(self, values):
        self.values = list(values)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(44, 10, -14, -34)
        if not self.values:
            p.setPen(QColor(MUTED))
            p.drawText(rect, Qt.AlignCenter, "No completed lesson data for this range yet")
            p.end()
            return
        max_value = max(1.0, max(v for _, v in self.values))
        p.setFont(QFont(p.font().family(), 9))
        for tick in range(4):
            y = rect.bottom() - rect.height() * tick / 3
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QColor(MUTED))
            value = max_value * tick / 3
            p.drawText(QRectF(0, y - 9, 38, 18), Qt.AlignRight | Qt.AlignVCenter,
                       f"${value:,.0f}" if getattr(self, "money_axis", False) else f"{value:.0f}")
        count = len(self.values)
        points = []
        for i, (label, value) in enumerate(self.values):
            x = rect.left() + (rect.width() * i / max(1, count - 1))
            y = rect.bottom() - rect.height() * value / max_value
            points.append(QPointF(x, y))
        if len(points) == 1:
            points = [QPointF(rect.left(), points[0].y()), points[0]]
        path = QPainterPath(points[0])
        for point in points[1:]:
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
        for i, (label, _) in enumerate(self.values):
            if i % stride == 0 or i == count - 1:
                x = points[min(i, len(points) - 1)].x()
                p.drawText(QRectF(x - 34, rect.bottom() + 6, 68, 20), Qt.AlignHCenter, label)
        p.end()


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


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------
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


class NoTextMenu(QObject):
    """App-wide filter: no Undo/Redo/Cut/Copy/Paste/Select All pop-up on text fields."""
    TEXT = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.ContextMenu and isinstance(obj, QWidget):
            if isinstance(obj, self.TEXT) or isinstance(obj.parent(), (QTextEdit, QPlainTextEdit)):
                return True
        return False


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
            sets = [("Lesson cost", ACCENT, step_points(self.lessons, xmin, xmax)),
                    ("Payments", GOOD, step_points(self.payments, xmin, xmax))]
        else:
            sets = [("Lesson cost", ACCENT, list(self.lessons)), ("Payments", GOOD, list(self.payments))]

        self.chart.removeAllSeries()
        ys = [0.0]
        for label, colour, pts in sets:
            series = QLineSeries()
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


# --------------------------------------------------------------------------
# Detail panel (right side)
# --------------------------------------------------------------------------
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
        lab = QLabel(label)
        lab.setObjectName("statLabel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lay.addWidget(lab)
        lay.addWidget(self.value)

    def set(self, text, color=None):
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color};" if color else "")


class DetailPanel(QWidget):
    invite_requested = Signal(int)
    delete_linked_requested = Signal(object)
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
        self.lessons.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.lessons.blockSignals(False)

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


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Calendar grids (week + month), drawn in the style of Google Calendar
# --------------------------------------------------------------------------
CAL_COLOURS = ["#C96442", "#5E7F4F", "#4F7A9A", "#9A6BA8", "#B8893B", "#3F8F8A", "#A8576B", "#7B7F3B"]


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
            "state": state, "invite": invite_status(row), "notes": row["notes"] or ""}


def event_tooltip(ev):
    when = nice_dt(ev["start"].strftime(DT_FMT))
    lines = [ev["name"], when + ("" if ev["untimed"] else f" \u2013 {short_clock(ev['end'])}"),
             f"{ev['duration']} min \u00b7 {ev['state'].capitalize()}", f"Invite: {ev['invite']}"]
    if ev["notes"]:
        lines.append(ev["notes"])
    lines.append("Double-click to edit")
    return "\n".join(lines)


def paint_chip(p, rect, ev, lines):
    """A rounded lesson 'pill'. Upcoming = solid student colour, past = pale tint, cancelled = grey + strikethrough."""
    col = student_colour(ev["sid"])
    state = ev["state"]
    if state == "upcoming":
        fill, fg = col, QColor("white")
    elif state == "done":
        fill, fg = blend(col, QColor("white"), 0.8), QColor(TEXT)
    else:
        fill, fg = QColor("#EEECE3"), QColor(MUTED)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, 5, 5)
    p.fillPath(path, fill)
    if state == "done":
        p.setClipPath(path)
        p.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()), col)
        p.setClipping(False)
    p.setPen(fg)
    f = QFont(p.font())
    y = rect.top() + 3
    for text, bold in lines:
        f.setBold(bold)
        f.setStrikeOut(state == "cancelled")
        f.setPixelSize(12 if bold else 11)
        p.setFont(f)
        fm = QFontMetrics(f)
        if y + fm.height() > rect.bottom() + 1:
            break
        r = QRectF(rect.left() + 7, y, rect.width() - 10, fm.height())
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
                p.fillRect(QRectF(self.GUTTER + i * cw, 0, cw, self.height()), QColor("#FCF6F2"))
        f = QFont(p.font()); f.setPixelSize(10); p.setFont(f)
        for h in range(24):
            y = h * self.HOUR_H
            p.setPen(QColor("#EFEDE4"))
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
            paint_chip(p, r, ev, lines)
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
                p.setPen(QColor(TEXT if d.month == self.month else "#B9B5A3"))
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


# --------------------------------------------------------------------------
# Settings page
# --------------------------------------------------------------------------
class SettingsPage(QWidget):
    """Everything is stored in the app_settings table, so it lives in tutoring.db with your other data."""
    saved = None   # set by main window: called after Save

    def __init__(self, store, invite_page):
        super().__init__()
        self.store = store
        self.invite_page = invite_page
        self.setObjectName("root")
        self.attach_paths = []
        self.inline_template_images = {}

        def card(title, note=""):
            frame = QFrame()
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
        g_card, gl = card("Google account")
        self.g_label = QLabel()
        b_conn = QPushButton("Connect Google")
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

    def refresh_google(self):
        st = google_status()
        self.g_label.setText({"missing_libs": "Google libraries not installed", "no_credentials": "Not connected",
                              "needs_signin": "Credentials loaded \u2013 sign-in needed",
                              "ready": "Connected to Google"}[st])

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


# --------------------------------------------------------------------------
# Invite page: Calendar event + Meet link, then a reviewed email
# --------------------------------------------------------------------------
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

        split = QSplitter()
        split.addWidget(self.left)
        split.addWidget(self.right)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 6)
        split.setSizes([520, 680])

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
            self.update_google_status()
        QMessageBox.warning(self, title, text)

    def update_google_status(self):
        st = google_status()
        text, color = {
            "missing_libs": ("Google libraries not installed", BAD),
            "no_credentials": ("Not connected \u2013 credentials file needed", MUTED),
            "needs_signin": ("Credentials loaded \u2013 sign-in needed", MUTED),
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


if HAVE_WEBENGINE:
    class StatCounterRequestFilter(QWebEngineUrlRequestInterceptor):
        """Block the specific third-party script responsible for the console warning."""
        def interceptRequest(self, info):
            url = info.requestUrl()
            if (url.host().lower() == "secure.statcounter.com" and
                    url.path() == "/counter/counter.js"):
                info.block(True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.store = Store()
        self.setWindowTitle("Tutoring Organiser")
        self.resize(1280, 780)
        self.setMinimumSize(640, 520)
        self.setAcceptDrops(True)

        root = ResizeWatcher()
        root.setObjectName("root")
        self.pages = QStackedWidget()
        self.pages.addWidget(root)
        self.setCentralWidget(self.pages)

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

        top = QBoxLayout(QBoxLayout.LeftToRight)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(btn_import)
        top.addWidget(btn_sync)
        top.addWidget(btn_new)

        self.table = make_table(["Student", "Notes", "Next lesson", "Last lesson", "Balance"], stretch_col=1)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setMinimumSectionSize(90)

        self.drop_note = QLabel("Drag student folders anywhere onto this window to import them.")
        self.drop_note.setObjectName("muted")

        left = QWidget()
        left.setMinimumWidth(0)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        ll.addLayout(top)
        ll.addWidget(self.search)
        ll.addWidget(self.table, 1)
        ll.addWidget(self.drop_note)

        self.detail = DetailPanel(self.store)
        self.detail.changed = self.refresh
        self.detail.invite_requested.connect(self.invite_for_lesson)
        self.detail.delete_linked_requested.connect(self.delete_linked_lesson)
        self.download_temp = tempfile.TemporaryDirectory(prefix="TutoringOrganiser-Online-")
        self.detail.download_temp_dir = self.download_temp.name

        split = QSplitter()
        self.detail.setMinimumWidth(0)
        split.setChildrenCollapsible(True)
        split.addWidget(left)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 6)
        split.setSizes([680, 600])
        # Keep the student list and detail view usable on compact windows by
        # stacking the panes instead of squeezing their tables and controls.
        root.resized.connect(lambda w: split.setOrientation(
            Qt.Vertical if w < 1040 else Qt.Horizontal))
        root.resized.connect(lambda w: top.setDirection(
            QBoxLayout.TopToBottom if w < 760 else QBoxLayout.LeftToRight))
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
        for page in (self.overview_page, self.calendar_page, self.invite_page, self.online_page, self.resources_page,
                     self.settings_page):
            self.pages.addWidget(page)

        bar = self.menuBar()
        bar.addAction("Overview", lambda: self.show_page(self.pages.indexOf(self.overview_page)))
        bar.addAction("Students", lambda: self.show_page(0))
        bar.addAction("Invite", self.show_invite)
        bar.addAction("Calendar", self.show_calendar)
        bar.addAction("Online resources", lambda: self.show_page(self.pages.indexOf(self.online_page)))
        bar.addAction("My resources", self.show_resources)
        bar.addAction("Settings", self.show_settings)

        self.calendar_sync_job = None
        self.calendar_sync_timer = QTimer(self)
        self.calendar_sync_timer.timeout.connect(lambda: self.sync_google_calendar(silent=True))
        self.calendar_sync_timer.start(5 * 60 * 1000)
        if google_status() == "ready":
            QTimer.singleShot(3000, lambda: self.sync_google_calendar(silent=True))

        self.refresh()

    def build_overview_page(self):
        page = ResizeWatcher()
        page.setObjectName("root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        welcome = QLabel("Welcome back")
        welcome.setObjectName("title")
        sub = QLabel("Here’s your tutoring at a glance.")
        sub.setObjectName("muted")
        titles.addWidget(welcome); titles.addWidget(sub)
        head.addLayout(titles, 1)
        for label, callback in (("Open calendar", self.show_calendar), ("New student", self.new_student)):
            button = QPushButton(label)
            if label == "New student": button.setObjectName("primary")
            button.clicked.connect(callback)
            head.addWidget(button)
        layout.addLayout(head)
        self.overview_stats = [Stat(x) for x in ("Students", "Lessons completed", "Earned this month", "Upcoming lessons")]
        layout.addWidget(ReflowContainer(self.overview_stats, min_item_width=150))
        lower = QBoxLayout(QBoxLayout.LeftToRight)
        activity_card = QFrame(); activity_card.setObjectName("card")
        al = QVBoxLayout(activity_card)
        self.overview_graph_title = QLabel("Lesson activity"); self.overview_graph_title.setObjectName("studentName")
        self.overview_graph = OverviewLineGraph()
        al.addWidget(self.overview_graph_title); al.addWidget(self.overview_graph, 1)
        graph_controls = QHBoxLayout()
        graph_controls.addStretch(1)
        self.graph_metric = QComboBox(); self.graph_metric.addItems(["Lesson activity", "Total revenue"])
        self.graph_period = QComboBox(); self.graph_period.addItems(["Last month", "All time"])
        self.graph_metric.setMaximumWidth(140); self.graph_period.setMaximumWidth(110)
        self.graph_metric.currentIndexChanged.connect(self.refresh_overview)
        self.graph_period.currentIndexChanged.connect(self.refresh_overview)
        graph_controls.addWidget(self.graph_metric); graph_controls.addWidget(self.graph_period)
        al.addLayout(graph_controls)
        upcoming_card = QFrame(); upcoming_card.setObjectName("card")
        ul = QVBoxLayout(upcoming_card)
        upcoming_title = QLabel("Coming up"); upcoming_title.setObjectName("studentName")
        self.upcoming_table = QTableWidget(0, 3)
        self.upcoming_table.setHorizontalHeaderLabels(["When", "Student", "Length"])
        self.upcoming_table.verticalHeader().setVisible(False)
        self.upcoming_table.setShowGrid(False)
        self.upcoming_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.upcoming_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.upcoming_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.upcoming_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.upcoming_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.upcoming_table.verticalHeader().setDefaultSectionSize(34)
        ul.addWidget(upcoming_title); ul.addWidget(self.upcoming_table, 1)
        activity_card.setMinimumHeight(250); upcoming_card.setMinimumHeight(220)
        lower.addWidget(activity_card, 3); lower.addWidget(upcoming_card, 2)
        layout.addLayout(lower, 1)
        # Narrow window: graph and "Coming up" stack vertically instead of squeezing side by side.
        page.resized.connect(lambda w: lower.setDirection(QBoxLayout.TopToBottom if w < 820 else QBoxLayout.LeftToRight))
        return page

    def refresh_overview(self):
        now = datetime.now()
        lessons = self.store.calendar_lessons()
        completed = [l for l in lessons if effective_status(l) == "completed"]
        month_earned = sum(lesson_value(l) for l in completed
                           if datetime.strptime(l["start"], DT_FMT).year == now.year
                           and datetime.strptime(l["start"], DT_FMT).month == now.month)
        upcoming = sorted((l for l in lessons if l["status"] != "cancelled"
                           and datetime.strptime(l["start"], DT_FMT) >= now), key=lambda l: l["start"])
        values = (str(len(self.store.students())), str(len(completed)), money(month_earned), str(len(upcoming)))
        for card, value in zip(self.overview_stats, values): card.set(value)
        revenue_mode = self.graph_metric.currentIndex() == 1
        self.overview_graph_title.setText(self.graph_metric.currentText())
        self.overview_graph.money_axis = revenue_mode
        lesson_points = []
        for lesson in completed:
            when = datetime.strptime(lesson["start"], DT_FMT)
            amount = lesson_value(lesson) if revenue_mode else 1
            lesson_points.append((when, amount))
        points = []
        if self.graph_period.currentIndex() == 0:
            first_day = now.date() - timedelta(days=29)
            buckets = [(first_day + timedelta(days=6 * i),
                        first_day + timedelta(days=6 * (i + 1)) if i < 4 else now.date() + timedelta(days=1))
                       for i in range(5)]
            for a, b in buckets:
                total = sum(v for d, v in lesson_points if a <= d.date() < b)
                points.append((a.strftime("%d %b"), total))
        elif lesson_points:
            first = min(d for d, _ in lesson_points)
            month = Date(first.year, first.month, 1)
            last_month = Date(now.year, now.month, 1)
            while month <= last_month:
                next_month = Date(month.year + (month.month == 12), month.month % 12 + 1, 1)
                total = sum(v for d, v in lesson_points if month <= d.date() < next_month)
                points.append((month.strftime("%b %y"), total))
                month = next_month
        self.overview_graph.set_values(points)
        self.upcoming_table.setRowCount(min(8, len(upcoming)))
        for i, lesson in enumerate(upcoming[:8]):
            self.upcoming_table.setItem(i, 0, cell(short_dt(lesson["start"])))
            self.upcoming_table.setItem(i, 1, cell(lesson["student_name"]))
            self.upcoming_table.setItem(i, 2, cell(f"{lesson['duration']} min"))

    def build_online_page(self):
        page = QWidget(); page.setObjectName("root")
        layout = QVBoxLayout(page); layout.setContentsMargins(28, 22, 28, 20); layout.setSpacing(10)
        heading = QLabel("Online resources"); heading.setObjectName("title")
        layout.addWidget(heading)
        self.resource_sites = {
            "Man vs Maths": "https://manvsmaths.com/",
            "No Brain Too Small": "https://www.nobraintoosmall.co.nz/",
            "NZ Scholarship Network": "https://nsn.nz/",
        }
        toolbar = QHBoxLayout()
        self.site_choice = QComboBox(); self.site_choice.addItems(self.resource_sites)
        self.site_choice.currentTextChanged.connect(lambda _: self.resource_home())
        self.resource_address = QLineEdit(); self.resource_address.setPlaceholderText("Search this site or enter a web address")
        self.resource_address.returnPressed.connect(self.resource_search)
        go = QPushButton("Search / go"); go.setObjectName("primary"); go.clicked.connect(self.resource_search)
        home = QPushButton("Site home"); home.clicked.connect(self.resource_home)
        toolbar.addWidget(self.site_choice); toolbar.addWidget(self.resource_address, 1); toolbar.addWidget(go); toolbar.addWidget(home)
        layout.addLayout(toolbar)
        if HAVE_WEBENGINE:
            self.web_view = QWebEngineView()
            self.web_view.setUrl(QUrl(self.resource_sites["Man vs Maths"]))
            back = QPushButton("Back"); back.clicked.connect(self.web_view.back)
            forward = QPushButton("Forward"); forward.clicked.connect(self.web_view.forward)
            toolbar.insertWidget(1, back); toolbar.insertWidget(2, forward)
            layout.addWidget(self.web_view, 1)
            self.downloads_label = QLabel("Downloaded PDFs appear here for dragging onto a lesson row.")
            self.downloads_label.setObjectName("muted")
            self.download_list = make_table(["Downloaded PDF — drag onto a lesson row"], stretch_col=0,
                                            pdf_drag_column=0, whole_row_drag=True)
            self.download_list.setMaximumHeight(140)
            self.download_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.download_list.doubleClicked.connect(self.open_downloaded_resource)
            layout.addWidget(self.downloads_label); layout.addWidget(self.download_list)
            profile = QWebEngineProfile.defaultProfile()
            # The embedded reference sites can include StatCounter's legacy
            # document.write counter script. Block only that known analytics
            # resource; other page and Chromium diagnostics remain visible.
            self._statcounter_filter = StatCounterRequestFilter()
            profile.setUrlRequestInterceptor(self._statcounter_filter)
            profile.downloadRequested.connect(self.handle_resource_download)
            self.resource_note = QLabel("Downloads are temporary until attached to a lesson. Drag a downloaded PDF onto the lesson row.")
        else:
            self.web_view = None
            self.resource_note = QLabel("The embedded browser needs PySide6-Addons (Qt WebEngine). These site links still open in your default browser.")
            self.resource_note.setObjectName("muted")
            layout.addWidget(self.resource_note)
            for name, url in self.resource_sites.items():
                row = QHBoxLayout()
                link = QPushButton(f"Open {name}"); link.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
                row.addWidget(link); row.addWidget(QLabel(url), 1); layout.addLayout(row)
            self.download_list = None
        if HAVE_WEBENGINE:
            self.resource_note.setObjectName("muted")
            layout.addWidget(self.resource_note)
        return page

    def resource_search(self):
        query = self.resource_address.text().strip()
        if not query: return self.resource_home()
        if query.startswith(("https://", "http://")):
            url = query
        else:
            from urllib.parse import quote_plus
            from urllib.parse import urlparse
            domain = urlparse(self.resource_sites[self.site_choice.currentText()]).netloc
            url = "https://www.google.com/search?q=" + quote_plus(f"site:{domain} {query}")
        if self.web_view:
            self.web_view.setUrl(QUrl(url))
        else:
            QDesktopServices.openUrl(QUrl(url))

    def resource_home(self):
        url = self.resource_sites[self.site_choice.currentText()]
        if self.web_view: self.web_view.setUrl(QUrl(url))
        else: QDesktopServices.openUrl(QUrl(url))

    def handle_resource_download(self, download):
        name = Path(download.downloadFileName()).name or f"resource_{uuid.uuid4().hex[:8]}.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        download.setDownloadDirectory(self.download_temp.name)
        download.setDownloadFileName(name)
        download.stateChanged.connect(lambda state, d=download: self.resource_download_state(d, state))
        download.accept()

    def resource_download_state(self, download, state):
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            path = Path(download.downloadDirectory()) / download.downloadFileName()
            if path.is_file() and path.suffix.lower() == ".pdf" and self.download_list:
                row = self.download_list.rowCount(); self.download_list.insertRow(row)
                item = cell(path.name, data=str(path)); item.setToolTip(str(path)); item.setData(Qt.UserRole + 1, str(path))
                self.download_list.setItem(row, 0, item)
                self.resource_note.setText(f"Ready to drag: {path.name}. Attach it to a lesson row to keep a copy with your lesson.")

    def open_downloaded_resource(self, index):
        path = self.download_list.item(index.row(), 0).data(Qt.UserRole)
        if path and Path(path).is_file(): QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def closeEvent(self, event):
        self.download_temp.cleanup()
        super().closeEvent(event)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)

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

    # ---- calendar helpers
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
        delete = menu.addAction("Delete selected lesson(s)")
        chosen = menu.exec(self.calendar_table.viewport().mapToGlobal(pos))
        if chosen == edit:
            self.edit_lesson_from_calendar(lid)
        elif chosen == invite:
            self.invite_for_lesson(lid)
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
        menu.addSeparator()
        delete = menu.addAction("Delete lesson")
        chosen = menu.exec(global_pos)
        if chosen == edit:
            self.edit_lesson_from_calendar(lesson_id)
        elif chosen == invite:
            self.invite_for_lesson(lesson_id)
        elif chosen == delete:
            self.confirm_delete_lessons([lesson_id])

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

    def build_resources_page(self):
        page = QWidget()
        page.setObjectName("root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel("My resources")
        heading.setObjectName("title")
        row = QHBoxLayout()
        self.resource_path = QLineEdit()
        self.resource_path.setReadOnly(True)
        self.resource_path.setPlaceholderText("Choose a folder to search for PDFs")
        choose = QPushButton("Link folder")
        choose.clicked.connect(self.choose_resource_folder)
        scan = QPushButton("Search for PDFs")
        scan.clicked.connect(self.refresh_resources)
        row.addWidget(self.resource_path, 1); row.addWidget(choose); row.addWidget(scan)
        self.resource_list = make_table(["PDF file", "Folder"], stretch_col=0, pdf_drag_column=0, whole_row_drag=True)
        self.resource_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.resource_list.doubleClicked.connect(self.open_resource_pdf)
        self.resource_note = QLabel("")
        self.resource_note.setObjectName("muted")
        drag_hint = QLabel("Drag one or more PDFs from this list into an email, a browser upload, a folder or "
                           "another app. Double-click a PDF to open it.")
        drag_hint.setObjectName("muted")
        layout.addWidget(heading); layout.addLayout(row); layout.addWidget(drag_hint); layout.addWidget(self.resource_note)
        layout.addWidget(self.resource_list, 1)
        saved = self.store.con.execute("SELECT value FROM app_settings WHERE key='resources_folder'").fetchone()
        if saved:
            self.resource_path.setText(saved["value"])
            self.refresh_resources()
        return page

    def choose_resource_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose your resources folder", self.resource_path.text())
        if folder:
            self.store.con.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('resources_folder',?)", (folder,))
            self.store.con.commit()
            self.resource_path.setText(folder)
            self.refresh_resources()

    def refresh_resources(self):
        folder = self.resource_path.text().strip()
        every = pdfs_in(folder) if folder and Path(folder).is_dir() else []
        linked = self.store.linked_pdf_paths()
        files = [f for f in every if norm(str(f)) not in linked and norm(str(f.resolve())) not in linked]
        hidden = len(every) - len(files)
        self.resource_note.setText(f"{hidden} PDF(s) hidden because they're already linked to a student." if hidden else "")
        self.resource_list.setRowCount(len(files))
        for i, path in enumerate(files):
            item = cell(path.name, data=str(path))
            item.setToolTip(str(path))
            item.setData(Qt.UserRole + 1, str(path))          # what gets dragged out
            self.resource_list.setItem(i, 0, item)
            self.resource_list.setItem(i, 1, cell(str(path.parent)))

    def show_resources(self):
        self.refresh_resources()
        self.show_page(self.pages.indexOf(self.resources_page))

    def open_resource_pdf(self, index):
        path = self.resource_list.item(index.row(), 0).data(Qt.UserRole)
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

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
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.refresh_overview()

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
        answer = QMessageBox.question(self, "Sync PDFs",
            "Treat unlinked PDFs in every student folder as additional/reference PDFs? Choose No to add them as lessons.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        n = self.store.sync_all_pdfs(as_additional=answer == QMessageBox.Yes)
        self.refresh()
        QMessageBox.information(self, "Sync PDFs", f"Added {n} PDF(s) across all student folders.")

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
            if self.detail.sid is None:
                QMessageBox.information(self, "Pick a student first",
                                        "Select a student in the list, then drop the PDFs again.")
            else:
                answer = QMessageBox.question(self, "Classify PDFs",
                    "Add these as additional/reference PDFs? Choose No to add them as lessons.",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No)
                if answer == QMessageBox.Cancel:
                    e.acceptProposedAction(); return
                n = (self.store.add_additional_pdfs(self.detail.sid, pdfs) if answer == QMessageBox.Yes
                     else self.store.attach_pdfs(self.detail.sid, pdfs))
                self.refresh()
                QMessageBox.information(self, "PDFs added", f"Added {n} PDF(s).")
        e.acceptProposedAction()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    menu_filter = NoTextMenu(app)
    app.installEventFilter(menu_filter)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
