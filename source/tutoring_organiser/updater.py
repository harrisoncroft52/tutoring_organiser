"""Update checker for Tutoring Organiser.

Looks at the latest *published* GitHub Release of GITHUB_REPO. If its tag is
newer than app_version.__version__, offers to download the installer asset
(TutoringOrganiser-Setup-<version>.exe), verifies it against the SHA-256 file
published next to it, backs up the database, then runs the installer silently
and lets it restart the app.

Nothing here needs a GitHub login: the repository is public.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from .version import __version__

GITHUB_REPO = "harrisoncroft52/tutoring_organiser"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
INSTALLER_RE = re.compile(r"^TutoringOrganiser-Setup-.+\.exe$", re.I)
CHECK_INTERVAL_S = 12 * 3600          # automatic checks at most twice a day
USER_AGENT = f"TutoringOrganiser/{__version__}"


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3); None if it doesn't look like a version."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _open(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest_release():
    """Return a dict describing the latest release, or None if none published yet."""
    try:
        with _open(API_LATEST) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code in (403, 429):
            raise RuntimeError("GitHub is limiting requests from this network right now. "
                               "Try again in a little while.") from e
        raise RuntimeError(f"GitHub replied with an error ({e.code}).") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError("Couldn't reach GitHub. Check your internet connection.") from e
    installer = sha = None
    for asset in data.get("assets", []):
        name, url = asset.get("name", ""), asset.get("browser_download_url", "")
        if not url.startswith(DOWNLOAD_PREFIX):
            continue
        if INSTALLER_RE.match(name):
            installer = {"name": name, "url": url, "size": asset.get("size", 0)}
        elif name.lower().endswith(".exe.sha256"):
            sha = {"name": name, "url": url}
    return {"version": data.get("tag_name", ""), "notes": (data.get("body") or "").strip(),
            "page": data.get("html_url") or RELEASES_PAGE, "installer": installer, "sha": sha}


def _download(url, dest, progress=None):
    h = hashlib.sha256()
    with _open(url, timeout=30) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return h.hexdigest()


class UpdateManager(QObject):
    """Owns the whole check -> prompt -> download -> install flow."""
    _checked = Signal(object, str, bool)      # release, error text, manual
    _progress = Signal(int, int)
    _downloaded = Signal(str, str)            # installer path, error text

    def __init__(self, window, store, before_install=None):
        super().__init__(window)
        self.window = window
        self.store = store
        self.before_install = before_install   # callable; raise to signal failure
        self.busy = False
        self.release = None
        self.dialog = None
        self._checked.connect(self._on_checked)
        self._progress.connect(self._on_progress)
        self._downloaded.connect(self._on_downloaded)

    # ---- checking
    def check_on_startup(self):
        if self.store.setting("auto_update_check", "1") != "1":
            return
        QTimer.singleShot(8000, lambda: self.check(manual=False))

    def check(self, manual=False):
        if self.busy:
            return
        if not manual:
            try:
                last = float(self.store.setting("last_update_check", "0") or 0)
            except ValueError:
                last = 0
            if time.time() - last < CHECK_INTERVAL_S:
                return
        self.busy = True
        threading.Thread(target=self._check_worker, args=(manual,), daemon=True).start()

    def _check_worker(self, manual):
        try:
            self._checked.emit(fetch_latest_release(), "", manual)
        except Exception as e:             # network, JSON, anything: never crash the app
            self._checked.emit(None, str(e) or e.__class__.__name__, manual)

    def _on_checked(self, release, error, manual):
        self.busy = False
        if error:
            if manual:
                QMessageBox.warning(self.window, "Check for updates", error)
            return
        self.store.set_setting("last_update_check", str(time.time()))
        if release is None:
            if manual:
                QMessageBox.information(self.window, "Check for updates",
                                        f"You're on version {__version__}. No releases have been published yet.")
            return
        if not is_newer(release["version"], __version__):
            if manual:
                QMessageBox.information(self.window, "Check for updates",
                                        f"You're up to date (version {__version__}).")
            return
        if not manual and self.store.setting("skipped_update") == release["version"]:
            return
        self.release = release
        self._prompt(release)

    # ---- prompting
    def _prompt(self, release):
        can_install = bool(getattr(sys, "frozen", False) and release["installer"] and release["sha"])
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Update available")
        box.setText(f"Tutoring Organiser {release['version'].lstrip('v')} is available "
                    f"(you have {__version__}).")
        notes = release["notes"]
        if len(notes) > 1500:
            notes = notes[:1500].rstrip() + "…"
        extra = "Your students, lessons and payments are not touched by an update; a backup of the database is made first."
        box.setInformativeText(extra if can_install else
                               "Automatic install isn't available for this copy, so the release page will open instead.")
        if notes:
            box.setDetailedText(notes)
        go = box.addButton("Install and restart" if can_install else "Open release page", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        skip = box.addButton("Skip this version", QMessageBox.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip:
            self.store.set_setting("skipped_update", release["version"])
        elif clicked is go:
            if can_install:
                self._start_download(release)
            else:
                QDesktopServices.openUrl(QUrl(release["page"]))

    # ---- downloading
    def _start_download(self, release):
        self.busy = True
        self.dialog = QProgressDialog("Downloading update…", "Cancel", 0, 100, self.window)
        self.dialog.setWindowTitle("Updating")
        self.dialog.setWindowModality(Qt.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.setValue(0)
        self._cancelled = False
        self.dialog.canceled.connect(lambda: setattr(self, "_cancelled", True))
        threading.Thread(target=self._download_worker, args=(release,), daemon=True).start()

    def _download_worker(self, release):
        try:
            folder = Path(tempfile.gettempdir()) / "TutoringOrganiserUpdate"
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            with _open(release["sha"]["url"]) as resp:
                text = resp.read(4096).decode("utf-8", "replace")
            m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
            if not m:
                raise RuntimeError("The release's checksum file is not valid.")
            expected = m.group(1).lower()
            dest = folder / release["installer"]["name"]

            def progress(done, total):
                if self._cancelled:
                    raise RuntimeError("cancelled")
                self._progress.emit(done, total or release["installer"].get("size", 0))

            actual = _download(release["installer"]["url"], dest, progress)
            if actual != expected:
                dest.unlink(missing_ok=True)
                raise RuntimeError("The downloaded file failed its safety check (checksum mismatch), "
                                   "so it was deleted and nothing was installed.")
            self._downloaded.emit(str(dest), "")
        except Exception as e:
            self._downloaded.emit("", "cancelled" if str(e) == "cancelled" else (str(e) or e.__class__.__name__))

    def _on_progress(self, done, total):
        if self.dialog and total:
            self.dialog.setValue(min(99, int(done * 100 / total)))

    def _on_downloaded(self, path, error):
        self.busy = False
        if self.dialog:
            self.dialog.close()
            self.dialog = None
        if error == "cancelled":
            return
        if error:
            QMessageBox.warning(self.window, "Update failed", error)
            return
        if self.before_install:
            try:
                self.before_install()
            except Exception as e:
                answer = QMessageBox.question(
                    self.window, "Backup failed",
                    f"Couldn't back up your database first:\n{e}\n\nInstall the update anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                             creationflags=flags, close_fds=True)
        except OSError as e:
            QMessageBox.warning(self.window, "Update failed", f"Couldn't start the installer:\n{e}")
            return
        QTimer.singleShot(300, QApplication.quit)
