"""Draws the app icon (the accent colour with a microphone) and saves it as a multi-size .ico."""

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

from meeting_assistant.app import theme

app = QGuiApplication(sys.argv)
image = QImage(256, 256, QImage.Format_ARGB32)
image.fill(Qt.transparent)
painter = QPainter(image)
painter.setRenderHint(QPainter.Antialiasing)
painter.setPen(Qt.NoPen)
painter.setBrush(QColor(theme.LIGHT.accent))
painter.drawRoundedRect(QRectF(8, 8, 240, 240), 56, 56)
font = QFont(theme.ICON_FONT)
font.setPixelSize(140)
painter.setFont(font)
painter.setPen(QColor("#ffffff"))
painter.drawText(QRectF(0, 0, 256, 256), Qt.AlignCenter, "")  # microphone
painter.end()
if not image.save(sys.argv[1]):
    raise SystemExit(f"Couldn't write {sys.argv[1]}")
