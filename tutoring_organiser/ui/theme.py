"""Colours and the application stylesheet."""


BG = "#F5F4EE"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#FAF9F5"
BORDER = "#E6E3D8"
TEXT = "#3D3929"
MUTED = "#8A8672"
ACCENT = "#C96442"
ACCENT_HOVER = "#B5573A"
SELECT = "#F3E6DE"
GOOD = "#5E7F4F"
BAD = "#B4452F"


STYLE = f"""
* {{ font-family: 'Segoe UI', sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-family: Georgia, 'Times New Roman', serif; font-size: 22px; }}
QLabel#studentName {{ font-family: Georgia, 'Times New Roman', serif; font-size: 24px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#statValue {{ font-size: 17px; font-weight: 600; }}
QLabel#statLabel {{ color: {MUTED}; font-size: 11px; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {SELECT}; selection-color: {TEXT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateTimeEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; selection-background-color: {SELECT}; selection-color: {TEXT}; }}
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: {SURFACE_ALT}; border-color: #D5D1C2; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#danger {{ color: {BAD}; }}
QPushButton:checked {{ background: {SELECT}; border-color: {ACCENT}; font-weight: 600; }}
QPushButton:disabled {{ color: #B9B5A3; background: {SURFACE_ALT}; border-color: {BORDER}; }}
QTableWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; gridline-color: transparent;
    outline: 0; selection-background-color: {SELECT}; selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid #F0EEE5; }}
QTableWidget::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QHeaderView::section {{
    background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 10px; color: {MUTED}; font-weight: 600;
}}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; margin-right: 4px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ margin: 0 4px; }}
QSplitter::handle:vertical {{ margin: 4px 0; }}
QSplitter::handle:hover, QSplitter::handle:pressed {{ background: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D9D5C6; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}
"""


def invite_colour(status):
    return {"Email sent": GOOD, "Calendar made": ACCENT}.get(status, MUTED)
