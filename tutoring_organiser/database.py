"""The SQLite database: schema, migrations and the Store class every screen talks to."""
import filecmp
import os
import re
import shutil
import sqlite3
import uuid
from collections import Counter
from datetime import date as Date, datetime, timezone
from pathlib import Path

from .config import DB_PATH, DT_FMT
from .helpers import effective_status, lesson_value, norm
from .parsing import date_from_filename, pdfs_in


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


# Additive migrations: (table, column, definition). Never drops or rewrites data.
MIGRATIONS = [
    ("students", "email", "TEXT DEFAULT ''"),
    ("lessons", "calendar_event_id", "TEXT DEFAULT ''"),
    ("lessons", "meet_url", "TEXT DEFAULT ''"),
    ("lessons", "invite_sent_at", "TEXT DEFAULT ''"),
    ("lessons", "local_updated_at", "TEXT DEFAULT ''"),
    ("lessons", "google_updated_at", "TEXT DEFAULT ''"),
]


DB_VERSION = 11


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
    def promote_additional_pdfs(self, sid):
        """Move PDFs sitting in Additional PDFs onto the same-day lesson when that lesson has no PDF yet.
        Only filenames that contain a date are trusted; a file's modified time is not enough to move it."""
        moved = 0
        for row in self.additional_pdfs(sid):
            try:
                when, kind = date_from_filename(row["pdf"])
            except OSError:                    # file missing on disk
                continue
            if kind != "filename":
                continue
            lesson = self.lesson_on_day(sid, when.strftime("%Y-%m-%d"))
            if lesson and not lesson["pdf"]:
                self.con.execute("UPDATE lessons SET pdf=? WHERE id=?", (row["pdf"], lesson["id"]))
                self.con.execute("DELETE FROM additional_pdfs WHERE id=?", (row["id"],))
                moved += 1
        if moved:
            self.con.commit()
        return moved

    def sync_pdfs(self, sid, as_additional=False):
        """Link each new PDF to its same-day lesson, preferring a lesson with a real start time."""
        s = self.student(sid)
        if not s or not s["folder"] or not Path(s["folder"]).is_dir():
            return 0
        self.merge_untimed_pdf_duplicates(sid)
        promoted = 0 if as_additional else self.promote_additional_pdfs(sid)
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
        return added + promoted

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

    # ---- dropping a PDF onto the app: work out the student, copy into their folder, file it under a lesson
    @staticmethod
    def _within(path, folder):
        try:
            return os.path.commonpath([norm(str(path)), norm(str(folder))]) == norm(str(folder))
        except ValueError:                     # different drives
            return False

    @staticmethod
    def _words(text):
        return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()) + " "

    def match_student_for_pdf(self, path):
        """Student id for a PDF, or None. Order: file already inside a student's folder, then the student's
        full name in the file name, then a first name that only one student has. Ambiguous = None."""
        students = list(self.students())
        inside = [s for s in students if s["folder"] and self._within(path, s["folder"])]
        if inside:
            return max(inside, key=lambda s: len(norm(s["folder"])))["id"]
        stem = self._words(Path(path).stem)
        named = [(s, self._words(s["name"])) for s in students if (s["name"] or "").strip()]
        full = [s for s, w in named if w in stem]
        if full:
            top = max(len(self._words(s["name"])) for s in full)
            best = [s for s in full if len(self._words(s["name"])) == top]
            return best[0]["id"] if len(best) == 1 else None
        first = [s for s, w in named if len(w.split()[0]) >= 3 and f" {w.split()[0]} " in stem]
        return first[0]["id"] if len(first) == 1 else None

    def assign_pdf(self, sid, source):
        """File one dropped PDF for a student. Returns a dict describing what happened:
        where = 'lesson' | 'new_lesson' | 'additional' | 'duplicate'."""
        s = self.student(sid)
        src = Path(source).resolve()
        out = {"sid": sid, "name": s["name"], "where": "", "lesson_id": None, "path": "", "when": None,
               "copied": False, "folder_missing": False}
        dest = src
        folder = Path(s["folder"]) if s["folder"] else None
        if folder and folder.is_dir():
            if not self._within(src, folder):
                target = folder / src.name
                if target.exists() and filecmp.cmp(src, target, shallow=False):
                    pass                                         # the same file is already in the folder
                else:
                    if target.exists():
                        target = folder / f"{src.stem}_{uuid.uuid4().hex[:6]}{src.suffix}"
                    shutil.copy2(src, target)
                    out["copied"] = True
                dest = target.resolve()
        else:
            out["folder_missing"] = True
        out["path"] = str(dest)
        self.merge_untimed_pdf_duplicates(sid)
        linked = {norm(r["pdf"]) for r in self.con.execute(
            "SELECT pdf FROM lessons WHERE student_id=? AND pdf != ''", (sid,))}
        linked |= {norm(r["pdf"]) for r in self.con.execute(
            "SELECT pdf FROM additional_pdfs WHERE student_id=?", (sid,))}
        when, kind = date_from_filename(dest)
        out["when"] = when
        if norm(str(dest)) in linked:
            out["where"] = "duplicate"
            return out
        target = self.lesson_on_day(sid, when.strftime("%Y-%m-%d"))
        if target and (kind == "filename" or not target["pdf"]):
            out["where"] = "additional" if target["pdf"] else "lesson"
            out["lesson_id"] = target["id"]
            self._link_pdf_to_lesson(target, str(dest))
        elif kind == "filename":
            status = "scheduled" if when > datetime.now() else "completed"
            cur = self.con.execute(
                "INSERT INTO lessons(student_id,start,duration,rate,status,pdf) VALUES (?,?,?,?,?,?)",
                (sid, when.strftime(DT_FMT), 60, s["rate"], status, str(dest)))
            out["where"], out["lesson_id"] = "new_lesson", cur.lastrowid
        else:                                  # no date in the name and no free lesson that day
            self.con.execute("INSERT OR IGNORE INTO additional_pdfs(student_id,pdf) VALUES (?,?)",
                             (sid, str(dest)))
            out["where"] = "additional"
        self.con.commit()
        return out


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
