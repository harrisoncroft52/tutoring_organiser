"""Ensure Windows resolves the Qt DLLs bundled beside PySide6 extensions."""
import os
import sys

if sys.platform == "win32" and getattr(sys, "frozen", False):
    # Keep handles alive for the lifetime of the process; dropping one closes
    # its DLL search directory before QtCore/shiboken are imported.
    sys._tutoring_organiser_dll_dirs = [
        os.add_dll_directory(os.path.join(sys._MEIPASS, directory))
        for directory in ("PySide6", "shiboken6")
    ]
