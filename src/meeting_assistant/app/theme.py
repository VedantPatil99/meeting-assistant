"""The app's look: one set of colour tokens per theme, a Qt palette and a stylesheet built from them.

Widgets don't hard-code colours. They get a role (`set_role(button, "primary")`) and the
stylesheet decides what a primary button looks like in light and dark mode. Code that has to
pick a colour itself, like an overdue date in a list, asks `tokens()`.

Fonts are the ones Windows 11 ships with (Segoe UI Variable, Segoe Fluent Icons), so there is
nothing to install.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from string import Template
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QTextBrowser, QWidget

MODES = ["system", "light", "dark"]
ICON_FONT = "Segoe Fluent Icons"  # Windows 11; Windows 10 has the older "Segoe MDL2 Assets"


@dataclass(frozen=True)
class Tokens:
    bg: str            # page background
    sidebar: str
    surface: str       # cards, inputs, lists
    surface_alt: str   # hover
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_soft: str   # selected rows
    accent_text: str   # text on an accent background
    danger: str
    warning_bg: str
    warning_text: str
    success: str
    scroll: str


LIGHT = Tokens(
    bg="#f6f7f9", sidebar="#eceef3", surface="#ffffff", surface_alt="#f1f2f6", border="#e1e4ea",
    text="#1c1e24", muted="#6b7180", accent="#4f46e5", accent_hover="#4338ca", accent_soft="#eceafd",
    accent_text="#ffffff", danger="#dc2626", warning_bg="#fff4e0", warning_text="#8a4b00",
    success="#15803d", scroll="#c9cdd6",
)
DARK = Tokens(
    bg="#16171b", sidebar="#101114", surface="#1e2025", surface_alt="#272a31", border="#2e3139",
    text="#e8e9ed", muted="#9ba1ad", accent="#8b8ff9", accent_hover="#a5a8fb", accent_soft="#2a2c4d",
    accent_text="#0d0e12", danger="#f87171", warning_bg="#3a2e12", warning_text="#fcd34d",
    success="#4ade80", scroll="#3d414b",
)

STYLESHEET = Template("""
* { outline: none; }
QMainWindow, QDialog, QWidget#content { background: $bg; }
QWidget#sidebar { background: $sidebar; border-right: 1px solid $border; }
QToolTip { background: $surface; color: $text; border: 1px solid $border; padding: 6px; border-radius: 6px; }

QLabel[role="brand"] { font-size: 15px; font-weight: 700; }
QLabel[role="h1"] { font-size: 21px; font-weight: 600; padding-bottom: 2px; }
QLabel[role="section"] { font-size: 13px; font-weight: 600; padding-top: 4px; }
QLabel[role="muted"] { color: $muted; }
QLabel[role="error"] { color: $danger; }
QLabel[role="warning"] { background: $warning_bg; color: $warning_text; padding: 8px 10px; border-radius: 8px; }
QLabel[role="clock"] { font-family: "Cascadia Mono", Consolas, monospace; font-size: 24px; font-weight: 600; }
QFrame[role="card"] { background: $surface; border: 1px solid $border; border-radius: 12px; }

QPushButton {
    background: $surface; color: $text; border: 1px solid $border; border-radius: 7px;
    padding: 6px 14px; min-height: 20px;
}
QPushButton:hover { background: $surface_alt; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $muted; background: $bg; }
QPushButton[role="primary"] { background: $accent; color: $accent_text; border-color: $accent; font-weight: 600; }
QPushButton[role="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[role="primary"]:disabled { background: $accent_soft; border-color: $accent_soft; color: $muted; }
QPushButton[role="danger"] { background: $danger; color: $surface; border-color: $danger; font-weight: 600; }
QPushButton[role="ghost"] { background: transparent; border: none; color: $muted; text-align: left; }
QPushButton[role="ghost"]:hover { background: $surface_alt; color: $text; }

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox {
    background: $surface; color: $text; border: 1px solid $border; border-radius: 7px; padding: 5px 8px;
    selection-background-color: $accent; selection-color: $accent_text;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {
    border: 1px solid $accent;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QDateEdit:disabled { color: $muted; background: $bg; }
QTextBrowser { background: $surface; border: 1px solid $border; border-radius: 12px; padding: 10px 14px; }
QComboBox, QDateEdit { padding-right: 26px; }
QComboBox::drop-down, QDateEdit::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; background: transparent;
}
QComboBox::down-arrow, QDateEdit::down-arrow { image: url("$chevron"); width: 10px; height: 10px; }
QComboBox QAbstractItemView {
    background: $surface; border: 1px solid $border; selection-background-color: $accent_soft;
    selection-color: $text; padding: 4px;
}

QListWidget, QTreeWidget, QTableWidget {
    background: $surface; border: 1px solid $border; border-radius: 12px; padding: 4px;
}
QListWidget::item { padding: 8px 10px; border-radius: 7px; margin: 1px 0; }
QListWidget::item:hover, QTreeView::item:hover, QTableView::item:hover { background: $surface_alt; }
QListWidget::item:selected, QTreeView::item:selected, QTableView::item:selected {
    background: $accent_soft; color: $text;
}
QTreeView::item { padding: 5px 2px; }
QTableView { gridline-color: transparent; }
QTableView::item { padding: 4px 6px; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: $surface; color: $muted; border: none; border-bottom: 1px solid $border;
    padding: 6px 8px; font-weight: 600;
}
QTableCornerButton::section { background: $surface; border: none; }
QListWidget#nav { background: transparent; border: none; padding: 0; }

QTabWidget::pane { border: none; top: 0; }
QTabBar::tab {
    background: transparent; color: $muted; padding: 7px 14px; margin-right: 4px;
    border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $accent; font-weight: 600; }
QTabBar::tab:hover { color: $text; }

QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 14px; }
QSplitter::handle:vertical { height: 14px; }
QProgressBar { background: $surface_alt; border: none; border-radius: 4px; }
QProgressBar::chunk { background: $accent; border-radius: 4px; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle { background: $scroll; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 30px; }
QScrollBar::handle:horizontal { min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QMenu { background: $surface; color: $text; border: 1px solid $border; padding: 4px; border-radius: 8px; }
QMenu::item { padding: 6px 18px; border-radius: 5px; }
QMenu::item:selected { background: $accent_soft; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid $muted; border-radius: 4px; background: $surface; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url("$check"); }
QCheckBox::indicator:disabled { border-color: $border; background: $bg; }
""")

_state: dict = {"mode": "system", "tokens": LIGHT, "listening": False}
_listeners: list[Callable[[], None]] = []


def tokens() -> Tokens:
    return _state["tokens"]


def mode() -> str:
    return _state["mode"]


def on_change(callback: Callable[[], None]) -> None:
    """Called after the theme changes, for widgets that pick colours in code."""
    _listeners.append(callback)


def palette(t: Tokens) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.Window: t.bg, QPalette.WindowText: t.text, QPalette.Base: t.surface,
        QPalette.AlternateBase: t.surface_alt, QPalette.Text: t.text, QPalette.Button: t.surface,
        QPalette.ButtonText: t.text, QPalette.Highlight: t.accent, QPalette.HighlightedText: t.accent_text,
        QPalette.PlaceholderText: t.muted, QPalette.ToolTipBase: t.surface, QPalette.ToolTipText: t.text,
        QPalette.Mid: t.border, QPalette.Link: t.accent, QPalette.BrightText: t.danger,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t.muted))
    return p


def is_dark(app: QApplication) -> bool:
    if _state["mode"] != "system":
        return _state["mode"] == "dark"
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply(app: QApplication, new_mode: str = "system") -> None:
    """Use `new_mode` ("system", "light" or "dark"). "system" follows Windows as it changes."""
    _state["mode"] = new_mode if new_mode in MODES else "system"
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+: also makes the title bar match
        hints.setColorScheme({"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}
                             .get(_state["mode"], Qt.ColorScheme.Unknown))
    if not _state["listening"]:
        hints.colorSchemeChanged.connect(lambda _: _restyle(app))
        _state["listening"] = True
    _restyle(app)


def _restyle(app: QApplication) -> None:
    t = DARK if is_dark(app) else LIGHT
    _state["tokens"] = t
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    font.setPointSizeF(10)
    app.setFont(font)
    app.setPalette(palette(t))
    app.setStyleSheet(STYLESHEET.substitute(asdict(t), chevron=chevron(t.muted),
                                        check=check_mark(t.accent_text)))
    for callback in _listeners:
        callback()


def _stroke_icon(name: str, path_data: str, color: str) -> str:
    """Stylesheets can only draw arrows and ticks from image files, so write a small SVG per colour."""
    folder = Path(tempfile.gettempdir()) / "meeting-assistant-theme"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}-{color.lstrip('#')}.svg"
    if not path.exists():
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                        f'<path d="{path_data}" fill="none" stroke="{color}" stroke-width="1.4" '
                        f'stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
    return path.as_posix()


def chevron(color: str) -> str:
    return _stroke_icon("chevron", "M1.5 3.5 5 7l3.5-3.5", color)


def check_mark(color: str) -> str:
    return _stroke_icon("check", "M2 5.2 4.2 7.4 8 3", color)


def set_markdown(view: QTextBrowser, text: str) -> None:
    """Show Markdown, with `code` spans (the timestamps) in a clean monospace font and muted
    colour instead of Qt's default fixed font."""
    view.setMarkdown(text)
    code = QTextCharFormat()
    code.setFontFamilies(["Cascadia Mono", "Consolas"])
    code.setFontPointSize(9)
    code.setForeground(QColor(tokens().muted))
    cursor = QTextCursor(view.document())
    block = view.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.charFormat().fontFixedPitch():
                cursor.setPosition(fragment.position())
                cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.KeepAnchor)
                cursor.mergeCharFormat(code)
            it += 1
        block = block.next()


def set_role(widget: QWidget, role: str) -> QWidget:
    """Give a widget a stylesheet role. Re-polishes so a change takes effect immediately."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def label(text: str, role: str, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    return lbl


def title(text: str) -> QLabel:
    return label(text, "h1")


def section(text: str) -> QLabel:
    return label(text, "section")


def muted(text: str) -> QLabel:
    return label(text, "muted", wrap=True)


def card() -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "card")
    return frame
