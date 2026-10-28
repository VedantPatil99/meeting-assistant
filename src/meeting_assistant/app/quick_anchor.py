"""Small always-on-top box for typing an anchor without leaving the call window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QVBoxLayout, QWidget


class QuickAnchor(QWidget):
    submitted = Signal(str)

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        frame = QFrame(self)
        frame.setObjectName("quickFrame")
        frame.setStyleSheet(
            "#quickFrame { background: palette(window); border: 1px solid palette(mid); border-radius: 8px; }"
        )
        self.hint = QLabel("Note  ·  ! must include   ctx: background   internal:   fr: feature request")
        self.hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.input = QLineEdit()
        self.input.setMinimumWidth(460)
        self.input.setPlaceholderText("Type and press Enter, Esc to cancel")
        self.input.returnPressed.connect(self._submit)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(12, 10, 12, 12)
        inner.addWidget(self.hint)
        inner.addWidget(self.input)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

    def popup(self, clock: str) -> None:
        self.hint.setText(f"[{clock}]  ! must include   ctx: background   internal:   fr: feature request")
        self.input.clear()
        self.adjustSize()
        pos = QCursor.pos()
        self.move(pos.x() - self.width() // 2, pos.y() + 16)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _submit(self) -> None:
        text = self.input.text().strip()
        if text:
            self.submitted.emit(text)
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
