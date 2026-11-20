# Tutoring Organiser

Desktop app (Python + PySide6) for running an online tutoring business: students, lessons, payments, PDFs,
a calendar synced with Google Calendar, Google Meet links, and invitation emails through Gmail.

Run from source: `pip install -r requirements.txt` then `python main.py`.
Self-test: `python tests/smoke_test.py`. Releasing: see [RELEASING.md](RELEASING.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## Layout

```
main.py                      entry point (python main.py; also what PyInstaller builds)
tutoring_organiser/
  version.py                 the one place the version number lives
  config.py                  data folder, database path, timezone, date format
  database.py                SQLite schema, migrations, and Store (every read/write the screens use)
  helpers.py                 small formatting/lookup functions
  parsing.py                 dates from file names; calendar and bank-statement paste parsers
  google_auth.py             Google sign-in, token files, readable Google error messages
  calendar_api.py            Google Calendar: create events + Meet links, two-way sync, delete
  invites.py                 invitation email templates, building the message, Gmail send
  updater.py                 checks GitHub Releases and installs updates
  app.py                     starts Qt and opens the window
  ui/
    theme.py                 colours and the stylesheet
    widgets.py               tables, SmartSplitter, Toast, flash highlight, reflowing layouts, alert pulse
    jobs.py                  run work on a background thread
    dialogs.py               student / lesson / payment / paste / manage-websites dialogs
    charts.py                overview graph (with hover readout) and the payment graph dialog
    calendar_widgets.py      week + month drawing, lesson chips with Meet/email icons
    detail_panel.py          right-hand student panel
    main_window.py           main window: student list, page switching, PDF drag-and-drop
    calendar_page.py         Calendar page methods (mixed into MainWindow)
    resources_page.py        Online resources + My resources (mixed into MainWindow)
    overview_page.py         Overview page (mixed into MainWindow)
    invite_page.py           Invite page
    settings_page.py         Settings page
tests/smoke_test.py          headless self-test, also run by the release workflow
assets/icon.ico  assets/fonts/  tutoring_organiser.spec  installer.iss  build.ps1  rth_pyside6_dlls.py   (build)
.github/workflows/release.yml                                                           (release)
```

Rule of thumb for changes: anything about *data* goes in `database.py`; anything Google goes in
`google_auth.py` / `calendar_api.py` / `invites.py`; anything on screen goes in `ui/`.
Personal data (`*.db`, Google credentials) is git-ignored: the repo is public.
