"""Colours and the application stylesheet (Croft Tutoring brand: plum, paper, Roboto Slab headings)."""


BG = "#F7F3F0"            # Paper
SURFACE = "#FFFFFF"
SURFACE_ALT = "#FBF8F6"
BORDER = "#E8DFE2"
TEXT = "#2B1E25"          # Ink
MUTED = "#6F6067"
ACCENT = "#7A2E4F"        # Croft Plum
ACCENT_HOVER = "#63233F"
HEADING = "#4A1A30"       # Deep Plum
SELECT = "#F3E4EA"        # Blush
GOOD = "#4F7A5A"
BAD = "#B03A2E"

# Quieter neutrals used for row rules, hover borders, disabled text and scrollbars.
RULE = "#F1EBED"
HOVER_BORDER = "#D9CDD2"
DISABLED = "#B5A9AF"
SCROLL = "#DCD2D6"

HEADING_FONT = "'Roboto Slab', Georgia, 'Times New Roman', serif"


STYLE = f"""
* {{ font-family: 'Segoe UI', sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-family: {HEADING_FONT}; font-weight: 700; font-size: 22px; color: {HEADING}; }}
QLabel#studentName {{ font-family: {HEADING_FONT}; font-weight: 700; font-size: 22px; color: {HEADING}; }}
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
QPushButton:hover {{ background: {SURFACE_ALT}; border-color: {HOVER_BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#danger {{ color: {BAD}; }}
QPushButton:checked {{ background: {SELECT}; border-color: {ACCENT}; color: {ACCENT}; font-weight: 600; }}
QPushButton:disabled {{ color: {DISABLED}; background: {SURFACE_ALT}; border-color: {BORDER}; }}
QTableWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; gridline-color: transparent;
    outline: 0; selection-background-color: {SELECT}; selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid {RULE}; }}
QTableWidget::item:selected {{ background: {SELECT}; color: {TEXT}; }}
QHeaderView::section {{
    background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 10px; color: {MUTED}; font-weight: 600;
}}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; margin-right: 4px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ margin: 0 4px; }}
QSplitter::handle:vertical {{ margin: 4px 0; }}
QSplitter::handle:hover, QSplitter::handle:pressed {{ background: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}
"""


def invite_colour(status):
    return {"Email sent": GOOD, "Calendar made": ACCENT}.get(status, MUTED)
