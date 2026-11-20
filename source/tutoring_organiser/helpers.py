"""Small formatting and lookup helpers with no UI of their own."""
import json
import os
from datetime import datetime

from PySide6.QtCore import QTime

from .config import DT_FMT, TIMEZONE


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


def fmt_duration(minutes):
    h, m = divmod(int(minutes), 60)
    bits = ([f"{h} hour" + ("s" if h != 1 else "")] if h else []) + ([f"{m} minutes"] if m else [])
    return " ".join(bits) or "0 minutes"


def fmt_clock_time(d):
    return d.strftime("%I:%M %p").lstrip("0").lower()


def auckland_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    except Exception:            # no tzdata on this machine: fall back to the PC clock
        return datetime.now()
