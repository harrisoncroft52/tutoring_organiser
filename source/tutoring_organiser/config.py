"""Paths and constants shared by every part of the app."""
import os
import shutil
import sys
from pathlib import Path


# Folder holding the program: the install folder when frozen, the repo folder when run from source.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
# Bundled read-only files (the icon and fonts). PyInstaller unpacks them under sys._MEIPASS.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
ICON_PATH = BUNDLE_DIR / "assets" / "icon.ico"
FONT_DIR = BUNDLE_DIR / "assets" / "fonts"         # Roboto Slab for headings (brand font)
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


DT_FMT = "%Y-%m-%d %H:%M"
TIMEZONE = "Pacific/Auckland"
