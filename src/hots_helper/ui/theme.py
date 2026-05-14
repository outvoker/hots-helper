"""Black-and-gold fantasy theme — single source of truth.

Applied once via ``QApplication.setStyleSheet``; every window, dialog,
context menu and tooltip in the app inherits it automatically. Inline
``setStyleSheet`` calls on individual widgets layer on top of this only
where a per-widget accent is needed (status colors, semantic borders,
etc.); they intentionally don't try to re-skin everything.

Palette:
    --bg-deep      #0d0d12   page background
    --bg-elevated  #14141c   group boxes, cards
    --bg-input     #1a1a24   text fields, lists
    --line         #3a3445   subtle separators
    --gold         #d8a64a   accents, primary actions, focused frames
    --gold-bright  #f4c453   hover/checked highlight
    --gold-dim     #7d5618   inactive accent
    --blue         #6f8fb6   secondary actions, info
    --text         #ece6d4   primary copy
    --text-dim     #aaa494   secondary copy
    --warn         #d99    bad-state hints
    --good         #9d9    good-state hints

The look is intentionally muted — the black base is closer to charcoal
than pitch-black so gold strokes don't blow out, and gold itself sits
just shy of saturation so it reads as metal rather than yellow.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# --- palette tokens (also exported for inline use) -------------------------

BG_DEEP = "#0d0d12"
BG_ELEVATED = "#14141c"
BG_INPUT = "#1a1a24"
BG_HOVER = "#1f1f2c"
BG_PRESSED = "#2a2436"
LINE = "#3a3445"

GOLD = "#d8a64a"
GOLD_BRIGHT = "#f4c453"
GOLD_DIM = "#7d5618"

BLUE = "#6f8fb6"
BLUE_DIM = "#3f5c80"

TEXT = "#ece6d4"
TEXT_DIM = "#aaa494"
TEXT_DISABLED = "#6a6458"

WARN = "#e08585"
GOOD = "#8fcc8f"


_QSS = f"""
/* ---------- base ----------------------------------------------------- */
* {{
    color: {TEXT};
    selection-background-color: {GOLD_DIM};
    selection-color: {TEXT};
}}

QWidget {{
    background-color: {BG_DEEP};
    font-size: 10pt;
}}

QToolTip {{
    background-color: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {GOLD_DIM};
    padding: 4px 6px;
}}

/* ---------- top-level surfaces -------------------------------------- */
QMainWindow, QDialog {{
    background-color: {BG_DEEP};
}}

QGroupBox {{
    background-color: {BG_ELEVATED};
    border: 1px solid {LINE};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    margin-left: 8px;
    color: {GOLD};
    background-color: {BG_DEEP};
    border: 1px solid {GOLD_DIM};
    border-radius: 4px;
    letter-spacing: 0.5px;
}}

QFrame[role="card"] {{
    background-color: {BG_ELEVATED};
    border: 1px solid {LINE};
    border-radius: 6px;
}}

/* ---------- text input ---------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QKeySequenceEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {GOLD_DIM};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus, QKeySequenceEdit:focus {{
    border: 1px solid {GOLD};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{
    color: {TEXT_DISABLED};
    background-color: {BG_DEEP};
}}

/* ---------- buttons -------------------------------------------------- */
QPushButton {{
    background-color: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {GOLD_DIM};
}}
QPushButton:pressed {{
    background-color: {BG_PRESSED};
    border-color: {GOLD};
}}
QPushButton:checked {{
    background-color: {BG_PRESSED};
    border-color: {GOLD};
    color: {GOLD_BRIGHT};
}}
QPushButton:disabled {{
    color: {TEXT_DISABLED};
    background-color: {BG_DEEP};
    border-color: {LINE};
}}
QPushButton:focus {{
    outline: none;
    border-color: {GOLD};
}}

/* Primary call-to-action — apply property-based selector so we don't
   have to re-style every individual button. Use
   ``btn.setProperty("variant", "primary")`` to opt in. */
QPushButton[variant="primary"] {{
    background-color: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {GOLD_BRIGHT}, stop:1 {GOLD_DIM}
    );
    color: #1a1410;
    border: 1px solid {GOLD};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background-color: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffe28b, stop:1 {GOLD}
    );
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {GOLD_DIM};
    color: {TEXT};
}}

QPushButton[variant="ghost"] {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {TEXT_DIM};
}}
QPushButton[variant="ghost"]:hover {{
    color: {GOLD_BRIGHT};
    border-color: {GOLD_DIM};
    background-color: {BG_HOVER};
}}

/* ---------- checkboxes / radios ------------------------------------- */
QCheckBox, QRadioButton {{
    spacing: 6px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {LINE};
    border-radius: 3px;
    background-color: {BG_INPUT};
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {GOLD_DIM};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {GOLD};
    border-color: {GOLD_BRIGHT};
}}

/* ---------- combo box dropdown -------------------------------------- */
QComboBox::drop-down {{
    width: 18px;
    border-left: 1px solid {LINE};
    background: {BG_HOVER};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    border: 1px solid {GOLD_DIM};
    selection-background-color: {GOLD_DIM};
    color: {TEXT};
}}

/* ---------- list / tree / table ------------------------------------- */
QListView, QTreeView, QTableView {{
    background-color: {BG_INPUT};
    alternate-background-color: {BG_ELEVATED};
    border: 1px solid {LINE};
    selection-background-color: {GOLD_DIM};
    selection-color: {TEXT};
    gridline-color: {LINE};
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    color: {GOLD};
    border: 0;
    border-right: 1px solid {LINE};
    border-bottom: 1px solid {GOLD_DIM};
    padding: 6px 10px;
    font-weight: 600;
}}

/* ---------- tabs ----------------------------------------------------- */
QTabWidget::pane {{
    border: 1px solid {LINE};
    border-top-color: {GOLD_DIM};
    background: {BG_ELEVATED};
}}
QTabBar::tab {{
    background: {BG_DEEP};
    color: {TEXT_DIM};
    padding: 6px 14px;
    border: 1px solid {LINE};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{
    background: {BG_ELEVATED};
    color: {GOLD};
    border-color: {GOLD_DIM};
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
}}

/* ---------- scroll bars --------------------------------------------- */
QScrollBar:vertical {{
    background: {BG_DEEP};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {GOLD_DIM};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {GOLD};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG_DEEP};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {GOLD_DIM};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {GOLD};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ---------- progress bar -------------------------------------------- */
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {LINE};
    border-radius: 4px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background-color: {GOLD};
    border-radius: 3px;
}}

/* ---------- menus / status bar -------------------------------------- */
QMenuBar {{
    background-color: {BG_DEEP};
    color: {TEXT};
    border-bottom: 1px solid {LINE};
}}
QMenuBar::item:selected {{
    background-color: {BG_HOVER};
    color: {GOLD_BRIGHT};
}}
QMenu {{
    background-color: {BG_INPUT};
    border: 1px solid {GOLD_DIM};
    color: {TEXT};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background-color: {GOLD_DIM};
    color: {TEXT};
}}
QStatusBar {{
    background: {BG_ELEVATED};
    border-top: 1px solid {LINE};
    color: {TEXT_DIM};
}}

/* ---------- splitter handles ---------------------------------------- */
QSplitter::handle {{
    background: {LINE};
}}
QSplitter::handle:hover {{
    background: {GOLD_DIM};
}}

/* ---------- semantic accent classes --------------------------------- */
QLabel[role="title"] {{
    color: {GOLD};
    font-weight: 600;
    letter-spacing: 0.5px;
}}
QLabel[role="subtitle"] {{
    color: {TEXT_DIM};
    font-style: italic;
}}
QLabel[role="warn"] {{ color: {WARN}; }}
QLabel[role="good"] {{ color: {GOOD}; }}
"""


def app_qss() -> str:
    """Return the app-wide stylesheet (cheap; no formatting at runtime)."""
    return _QSS


def apply_app_theme(app: QApplication) -> None:
    """Install the dark/black-gold theme on a QApplication.

    Sets:
    * an opinionated QPalette so non-CSS-aware widgets (file dialogs,
      message boxes) still pick up the dark base;
    * the global QSS above;
    * a slightly tighter default font.
    """
    app.setStyle("Fusion")  # neutral base — easier to override than native styles

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG_DEEP))
    pal.setColor(QPalette.Base, QColor(BG_INPUT))
    pal.setColor(QPalette.AlternateBase, QColor(BG_ELEVATED))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(BG_INPUT))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(GOLD_DIM))
    pal.setColor(QPalette.HighlightedText, QColor(TEXT))
    pal.setColor(QPalette.ToolTipBase, QColor(BG_INPUT))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.Link, QColor(GOLD_BRIGHT))
    pal.setColor(QPalette.LinkVisited, QColor(GOLD))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_DISABLED))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(TEXT_DISABLED))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_DISABLED))
    app.setPalette(pal)

    f = app.font()
    if f.pointSize() < 10:
        f.setPointSize(10)
    app.setFont(f)

    app.setStyleSheet(_QSS)
