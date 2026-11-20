"""Application entry point."""
import sys

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from .config import FONT_DIR, ICON_PATH
from .ui.main_window import MainWindow
from .ui.theme import STYLE
from .ui.widgets import NoTextMenu


def _set_windows_app_id():
    """Give the process its own taskbar identity so Windows shows our icon, not Python's (matters when run
    from source; harmless in the installed app)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HarrisonCroft.TutoringOrganiser")
    except (AttributeError, OSError):
        pass


def _load_brand_fonts():
    """Register the bundled Roboto Slab so headings match the Croft Tutoring brand on any PC.
    If a font file is missing the stylesheet falls back to Georgia."""
    if FONT_DIR.is_dir():
        for path in sorted(FONT_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(path))


def main():
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Tutoring Organiser")
    _load_brand_fonts()
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))     # title bar, taskbar and every dialog
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    menu_filter = NoTextMenu(app)
    app.installEventFilter(menu_filter)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
