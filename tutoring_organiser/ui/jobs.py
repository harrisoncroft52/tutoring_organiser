"""Run a function on a background thread and get the result back on the UI thread."""
import threading

from PySide6.QtCore import QObject, Signal


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
