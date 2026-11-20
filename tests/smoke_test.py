"""Headless smoke test: python tests/smoke_test.py   (no Google, no network, uses a throw-away data folder).

Exits non-zero if anything is wrong. The release workflow runs this before building the installer.
"""
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="tutoring-test-"))
os.environ["APPDATA"] = str(TMP / "appdata")          # config.py reads this at import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt                                                   # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu                               # noqa: E402

from tutoring_organiser.config import DT_FMT                                    # noqa: E402
from tutoring_organiser.database import Store                                   # noqa: E402
from tutoring_organiser.helpers import auckland_now                             # noqa: E402
from tutoring_organiser.invites import render_invite, render_invite_html        # noqa: E402
from tutoring_organiser.ui import calendar_page                                 # noqa: E402
from tutoring_organiser.ui.calendar_widgets import make_event                   # noqa: E402
from tutoring_organiser.ui.main_window import MainWindow                        # noqa: E402
from tutoring_organiser.ui.theme import STYLE                                   # noqa: E402
from tutoring_organiser import updater                                          # noqa: E402

failures = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + ((" -> " + str(detail)) if detail and not condition else ""))
    if not condition:
        failures.append(name)


def pump(app, n=6, pause=0.0):
    for _ in range(n):
        app.processEvents()
        if pause:
            time.sleep(pause)


# ---------------------------------------------------------------- email wording
today = auckland_now()
subj, body = render_invite("S {date}", "Hi {date} {meet_link}", "Alice Smith", today.replace(hour=16, minute=0), 60, "https://meet.google.com/abc")
check("email says 'today' for a lesson today", subj == "S today" and body.startswith("Hi today "), (subj, body))
# A fixed date that can never be "today" (the old 20 Nov 2026 date failed on that day).
check("email date has no year or weekday", render_invite("{date}", "x", "A", datetime(2031, 3, 7, 9, 30), 60, "")[0] == "7 March")
page = render_invite_html("<p>{meet_link}</p>", "A", datetime(2026, 11, 20, 9, 30), 60, "https://meet.google.com/a?b=1&c=2")
check("Meet link is a hyperlink in the HTML email", '<a href="https://meet.google.com/a?b=1&amp;c=2">' in page, page)

# ---------------------------------------------------------------- PDFs
folder = TMP / "files" / "Alice"
folder.mkdir(parents=True)
inbox = TMP / "inbox"
inbox.mkdir()
store = Store()
store.set_setting("auto_update_check", "0")
alice = store.save_student(None, "Alice Smith", 50.0, str(folder), "")
bob = store.save_student(None, "Bob Jones", 40.0, "", "")
store.save_student(None, "Alice Brown", 40.0, "", "")
store.save_lesson(None, alice, "2026-11-03 16:00", 60, 50, "completed", "", "")
for name in ("Alice Smith 2026-11-03.pdf", "random.pdf", "Alice 2026-11-10.pdf", "bob 3 Nov.pdf", "Alice Smith 2026-11-03 extra.pdf"):
    (inbox / name).write_bytes(b"%PDF-1.4 " + name.encode())
check("PDF matched by full name", store.match_student_for_pdf(inbox / "Alice Smith 2026-11-03.pdf") == alice)
check("PDF with no student name is not matched", store.match_student_for_pdf(inbox / "random.pdf") is None)
check("ambiguous first name is not guessed", store.match_student_for_pdf(inbox / "Alice 2026-11-10.pdf") is None)
check("first name matched when unique", store.match_student_for_pdf(inbox / "bob 3 Nov.pdf") == bob)
res = store.assign_pdf(alice, inbox / "Alice Smith 2026-11-03.pdf")
check("dropped PDF lands on that day's lesson and is copied into the folder",
      res["where"] == "lesson" and res["copied"] and (folder / "Alice Smith 2026-11-03.pdf").is_file(), res)
check("same PDF again is reported as already filed", store.assign_pdf(alice, inbox / "Alice Smith 2026-11-03.pdf")["where"] == "duplicate")
check("second PDF the same day goes to Additional PDFs", store.assign_pdf(alice, inbox / "Alice Smith 2026-11-03 extra.pdf")["where"] == "additional")
store.save_lesson(None, alice, "2026-11-10 16:00", 60, 50, "scheduled", "", "")
late = folder / "Alice 2026-11-10.pdf"
late.write_bytes(b"%PDF x")
store.add_additional_pdfs(alice, [str(late)])
store.sync_pdfs(alice)
lesson10 = [x for x in store.lessons(alice) if x["start"].startswith("2026-11-10")][0]
check("Sync moves a dated PDF from Additional onto its lesson", lesson10["pdf"].endswith("Alice 2026-11-10.pdf"), lesson10["pdf"])

# ---------------------------------------------------------------- calendar data
base = datetime.now().replace(minute=0, second=0, microsecond=0, hour=10)


def lesson(sid, start, event, meet, sent):
    store.save_lesson(None, sid, start.strftime(DT_FMT), 60, 50, "scheduled", "", "")
    lid = store.con.execute("SELECT MAX(id) FROM lessons").fetchone()[0]
    if event:
        store.con.execute("UPDATE lessons SET calendar_event_id=? WHERE id=?", (f"ev{lid}", lid))
    if meet:
        store.con.execute("UPDATE lessons SET meet_url=? WHERE id=?", ("https://meet.google.com/abc", lid))
    if sent:
        store.con.execute("UPDATE lessons SET invite_sent_at=? WHERE id=?", (datetime.now().strftime(DT_FMT), lid))
    store.con.commit()
    return lid


l_full = lesson(alice, base.replace(hour=9), True, True, True)
l_nomeet = lesson(bob, base.replace(hour=11), True, False, False)
l_noevent = lesson(bob, base.replace(hour=13), False, False, False)
events = {r["id"]: make_event(r) for r in store.calendar_lessons()}
check("calendar event carries Meet + email state",
      events[l_full]["meet_url"] and events[l_full]["email_sent"] and not events[l_nomeet]["meet_url"] and not events[l_nomeet]["email_sent"])

# ---------------------------------------------------------------- UI
app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
win = MainWindow()
win.resize(1280, 800)
win.show()
pump(app)
opened_on = win.pages.currentWidget()
win.show_page(0)                                      # the layout checks below are on the Students page
pump(app)

GEOMETRY = not os.environ.get("SMOKE_SKIP_GEOMETRY")      # window-size checks vary with fonts; CI skips them
split = win.split
last = None
smooth = True
if GEOMETRY:
    for pos in (800, 700, 600, 500, 450, 400, 350, 330, 100, 0):
        split.moveSplitter(pos, 1)
        pump(app, 2)
        a = split.sizes()[0]
        if a == 0 or (last is not None and a > last):
            smooth = False
        last = a
    check("dragging the divider never snaps a pane shut", smooth and all(n > 0 for n in split.sizes()), split.sizes())
    split.moveSplitter(1000, 1)
    pump(app, 2)
    check("divider stops at the other pane's minimum", all(n > 0 for n in split.sizes()), split.sizes())
    split.reset_sizes()
    pump(app, 2)

    for width, expect_hidden in ((1400, False), (1100, True)):
        win.resize(width, 780)
        split.setOrientation(Qt.Horizontal)
        pump(app, 8)
        hidden = win.table.isColumnHidden(1)
        check(f"student list at {width}px {'hides' if expect_hidden else 'shows'} the Notes column", hidden == expect_hidden)
    win.resize(1280, 800)
    pump(app)

check("app opens on the Overview page", opened_on is win.overview_page)
win.show_page(win.pages.indexOf(win.overview_page))
check("Overview graph opens on Lesson activity, All time",
      win.graph_metric.currentText() == "Lesson activity" and win.graph_period.currentText() == "All time")
check("window has the app icon", not win.windowIcon().isNull())

# graph hover: x and y of the nearest point
from PySide6.QtCore import QPointF                                               # noqa: E402
from PySide6.QtGui import QMouseEvent                                            # noqa: E402
from tutoring_organiser.ui.charts import GraphDialog                            # noqa: E402
graph = win.overview_graph
graph.money_axis = False
graph.set_values([("Oct 26", 2, "October 2026"), ("Nov 26", 1, "November 2026")])
graph.resize(500, 220)
graph.grab()
target = graph._points[1]
graph.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, target, graph.mapToGlobal(target.toPoint()), Qt.NoButton, Qt.NoButton, Qt.NoModifier))
check("hovering the graph highlights the nearest point", graph.hover == 1, graph.hover)
check("hover shows period and lesson count", graph.hover_text(1) == ("November 2026", "1 lesson"), graph.hover_text(1))
graph.money_axis = True
check("hover shows money on the revenue graph", graph.hover_text(0)[1] == "$2.00", graph.hover_text(0))
graph.grab()                                          # paints the hover box
ev = [(datetime(2026, 11, 3, 16), 50.0), (datetime(2026, 11, 10, 16), 50.0)]
from tutoring_organiser.ui.charts import _ms                                      # noqa: E402
check("payment graph hover: nearest point", GraphDialog.hover_value(ev, False, _ms(datetime(2026, 11, 9)))[1] == 50.0)
check("payment graph hover: running total at the cursor",
      GraphDialog.hover_value(ev, True, _ms(datetime(2026, 11, 11))) == (datetime(2026, 11, 10, 16), 100.0))
win.refresh_overview()

win.graph_period.setCurrentIndex(0)
pump(app, 3)
win.graph_period.setCurrentIndex(1)
pump(app, 3)
check("overview says 'Earned all time' on the all-time graph", win.overview_stats[2].label.text() == "Earned all time")
win.graph_period.setCurrentIndex(0)
pump(app, 3)
check("overview says 'Earned this month' otherwise", win.overview_stats[2].label.text() == "Earned this month")

win.show_calendar()
pump(app)
win.grab()                                           # paints the week view with the status icons

# right-click Meet action, with Google mocked out
calls = []
calendar_page.google_status = lambda: "ready"
calendar_page.google_credentials = lambda interactive=False: object()
calendar_page.calendar_resume_event = lambda creds, event_id, **kw: (
    calls.append(("resume", event_id)) or {"event_id": event_id, "meet_url": "https://meet.google.com/resumed", "html_link": "", "warning": ""})
calendar_page.calendar_create_event = lambda creds, **kw: (
    calls.append(("create", kw["event_id"], kw["emails"])) or {"event_id": kw["event_id"], "meet_url": "https://meet.google.com/created", "html_link": "", "warning": ""})


def create_meet(lid):
    win.create_meet_for_lesson(lid)
    for _ in range(100):
        pump(app, 1, 0.02)
        if not win.calendar_sync_job:
            break


create_meet(l_nomeet)
create_meet(l_noevent)
row_a, row_b = store.lesson(l_nomeet), store.lesson(l_noevent)
check("Create Meet link on an existing event adds the link", row_a["meet_url"] == "https://meet.google.com/resumed" and calls[0][0] == "resume")
check("Create Meet link on a lesson not in Google creates the event (no attendees emailed)",
      calls[1][0] == "create" and calls[1][2] == [] and row_b["calendar_event_id"] and row_b["meet_url"].endswith("/created"))
menu = QMenu()
check("menu offers 'Copy Meet link' when a link exists", win.add_meet_action(menu, l_full).text() == "Copy Meet link")
menu.deleteLater()
store.con.execute("UPDATE lessons SET meet_url='' WHERE id=?", (l_full,))
store.con.commit()
menu2 = QMenu()
check("menu offers 'Create Meet link' when there isn't one", win.add_meet_action(menu2, l_full).text() == "Create Meet link")

# ---------------------------------------------------------------- online resources list
from tutoring_organiser.ui import resources_page                                 # noqa: E402
from tutoring_organiser.ui.dialogs import ManageSitesDialog, normalise_url      # noqa: E402
check("three built-in websites the first time", [n for n, _ in win.resource_sites] == [n for n, _ in resources_page.DEFAULT_RESOURCE_SITES])
check("web addresses are tidied", normalise_url("nzqa.govt.nz") == "https://nzqa.govt.nz/" and normalise_url("not a site") == "")
dlg = ManageSitesDialog(win, win.resource_sites, resources_page.DEFAULT_RESOURCE_SITES)
dlg.new_name.setText("NZQA")
dlg.new_url.setText("nzqa.govt.nz")
dlg.add_site()
dlg.table.selectRow(0)
dlg.remove_selected()                                   # Man vs Maths
dlg.table.item(0, 0).setText("NBTS")                    # rename No Brain Too Small
dlg.save()
check("manage dialog adds, removes and renames", dlg.result_sites == [
    ("NBTS", "https://www.nobraintoosmall.co.nz/"), ("NZ Scholarship Network", "https://nsn.nz/"), ("NZQA", "https://nzqa.govt.nz/")],
    dlg.result_sites)
dup = ManageSitesDialog(win, [("A", "https://a.com/"), ("a", "https://b.com/")], [])
dup.save()
check("duplicate names are refused", dup.result_sites is None)
resources_page.save_resource_sites(store, dlg.result_sites)
check("website list is saved", resources_page.load_resource_sites(store) == dlg.result_sites)
win.resource_sites = dlg.result_sites
win.fill_site_choice("NZQA")
check("site picker shows the edited list", [win.site_choice.itemText(i) for i in range(win.site_choice.count())] == ["NBTS", "NZ Scholarship Network", "NZQA"]
      and win.site_choice.currentText() == "NZQA")

# ---------------------------------------------------------------- expired Google sign-in alert
from tutoring_organiser import google_auth                                      # noqa: E402
google_auth.CREDENTIALS_FILE.write_text('{"installed": {}}', encoding="utf-8")
google_auth.TOKEN_FILE.write_text('{"scopes": %s}' % str(google_auth.SCOPES).replace("'", '"'), encoding="utf-8")
win.check_google_alert()
check("no alert while signed in", google_auth.google_status() == "ready" and win.menuBar().alert_action is None
      and not win.google_pulse.running())
google_auth.sign_out(expired=True)                     # what happens when Google refuses to renew the token
check("refused renewal is reported as 'expired'", google_auth.google_status() == "expired")
win.check_google_alert()
pump(app, 3, 0.05)
check("Settings menu pulses when the sign-in expires", win.menuBar().alert_action is win.settings_action and win.google_pulse.running())
check("Settings Google card is highlighted", win.settings_page.g_card.alert and "expired" in win.settings_page.g_label.text())
check("Invite page says the sign-in expired", "expired" in win.invite_page.g_status.text())
win.grab()                                             # paints the pulsing menu bar
google_auth.sign_out()                                 # Disconnect: no longer 'expired'
win.check_google_alert()
check("alert clears once the user disconnects or reconnects", google_auth.google_status() == "needs_signin"
      and win.menuBar().alert_action is None and not win.settings_page.g_card.alert and not win.google_pulse.running())
google_auth.CREDENTIALS_FILE.unlink()

# ---------------------------------------------------------------- dropping PDFs on the window
extra = inbox / "Bob Jones 2026-11-09.pdf"
extra.write_bytes(b"%PDF y")
win.assign_dropped_pdfs([str(extra), str(inbox / "random.pdf")])
pump(app)
check("drop shows a message, flags the unmatched file", "random.pdf" in win.toast.text() and win.toast.isVisible(), win.toast.text())

# ---------------------------------------------------------------- updater + housekeeping
check("updater compares versions", updater.is_newer("v1.2.0", "1.1.9") and not updater.is_newer("1.0.0", "1.0.0"))
leftovers = [p for p in Path(__file__).resolve().parent.parent.rglob("*.py") if "miro" in p.read_text(encoding="utf-8").lower() and p.name != "smoke_test.py"]
check("no Miro code remains", not leftovers, leftovers)

win.close()
shutil.rmtree(TMP, ignore_errors=True)
print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
