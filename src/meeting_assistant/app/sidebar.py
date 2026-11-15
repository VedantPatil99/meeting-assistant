"""The navigation rail: app name, one row per page with an icon and a count badge, and the
light/dark switch at the bottom."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import theme

BADGE = Qt.UserRole + 1
GLYPH = Qt.UserRole + 2
MODE_GLYPHS = {"system": "", "light": "", "dark": ""}  # monitor, sun, moon
MODE_NAMES = {"system": "Match Windows", "light": "Light", "dark": "Dark"}


class NavDelegate(QStyledItemDelegate):
    """Draws a row as: icon, name, and a pill with the count when there is one."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, 40)

    def paint(self, painter: QPainter, option, index) -> None:
        t = theme.tokens()
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        r = option.rect.adjusted(8, 2, -8, -2)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if selected or hover:
            painter.setBrush(QColor(t.accent_soft if selected else t.surface_alt))
            painter.drawRoundedRect(QRectF(r), 7, 7)
        if selected:
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(QRectF(r.left(), r.center().y() - 8, 3, 16), 1.5, 1.5)

        icon_font = QFont(theme.ICON_FONT)
        icon_font.setPixelSize(16)
        painter.setFont(icon_font)
        painter.setPen(QColor(t.accent if selected else t.muted))
        painter.drawText(QRect(r.left() + 12, r.top(), 20, r.height()), Qt.AlignCenter, index.data(GLYPH) or "")

        font = QFont(option.font)
        font.setWeight(QFont.DemiBold if selected else QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor(t.text))
        text_rect = QRect(r.left() + 44, r.top(), r.width() - 44 - 36, r.height())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, index.data(Qt.DisplayRole))

        count = index.data(BADGE)
        if count:
            small = QFont(option.font)
            small.setPointSizeF(8.5)
            small.setWeight(QFont.DemiBold)
            painter.setFont(small)
            text = str(count) if count < 100 else "99+"
            width = max(20, painter.fontMetrics().horizontalAdvance(text) + 12)
            pill = QRectF(r.right() - width - 8, r.center().y() - 9, width, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t.accent if selected else t.border))
            painter.drawRoundedRect(pill, 9, 9)
            painter.setPen(QColor(t.accent_text if selected else t.text))
            painter.drawText(pill, Qt.AlignCenter, text)
        painter.restore()


class Sidebar(QWidget):
    mode_changed = Signal(str)

    def __init__(self, pages: list[tuple[str, str]], mode: str):
        """`pages` is (name, icon glyph) per page, in order."""
        super().__init__()
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(220)

        dot = QLabel("")  # microphone
        dot.setStyleSheet(f"font-family: '{theme.ICON_FONT}'; font-size: 16px;")
        brand = QHBoxLayout()
        brand.setContentsMargins(20, 20, 18, 16)
        brand.setSpacing(10)
        brand.addWidget(dot)
        brand.addWidget(theme.label("Meeting Assistant", "brand"), 1)

        self.list = QListWidget()
        self.list.setObjectName("nav")
        self.list.setItemDelegate(NavDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for name, glyph in pages:
            item = QListWidgetItem(name)
            item.setData(GLYPH, glyph)
            self.list.addItem(item)

        self.mode = mode
        self.mode_button = QPushButton()
        theme.set_role(self.mode_button, "ghost")
        self.mode_button.setToolTip("Switch between light, dark and your Windows setting")
        self.mode_button.clicked.connect(self._next_mode)
        self._show_mode()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)
        layout.addLayout(brand)
        layout.addWidget(self.list, 1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 0, 10, 0)
        bottom.addWidget(self.mode_button)
        layout.addLayout(bottom)

    def set_badge(self, row: int, count: int) -> None:
        self.list.item(row).setData(BADGE, count)

    def _show_mode(self) -> None:
        self.mode_button.setText(f"{MODE_GLYPHS[self.mode]}   {MODE_NAMES[self.mode]}")
        # the glyph needs the icon font; Qt falls back to it for the private-use code point
        font = QFont()
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", theme.ICON_FONT])
        self.mode_button.setFont(font)

    def _next_mode(self) -> None:
        self.mode = theme.MODES[(theme.MODES.index(self.mode) + 1) % len(theme.MODES)]
        self._show_mode()
        self.mode_changed.emit(self.mode)
