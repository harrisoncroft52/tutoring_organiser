"""Application entry point."""
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .config import ICON_PATH
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


def main():
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Tutoring Organiser")
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))     # title bar, taskbar and every dialog
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    menu_filter = NoTextMenu(app)
    app.installEventFilter(menu_filter)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
