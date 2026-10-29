"""Small always-on-top box for typing an anchor without leaving the call window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget


def mark_internal(text: str) -> str:
    """Turns a note into an internal one, replacing any other note type it had."""
    for prefix in ("internal:", "ctx:", "fr:", "!"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return f"internal: {text}"


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
        self.internal = QCheckBox("Internal (Ctrl+I)")
        self.internal.setToolTip("Only your team sees it: never used in emails to people outside your company")
        QShortcut(QKeySequence("Ctrl+I"), self, activated=self.internal.toggle)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(12, 10, 12, 12)
        inner.addWidget(self.hint)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.internal)
        inner.addLayout(row)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

    def popup(self, clock: str) -> None:
        self.hint.setText(f"[{clock}]  ! must include   ctx: background   internal:   fr: feature request")
        self.input.clear()
        self.internal.setChecked(False)
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
            self.submitted.emit(mark_internal(text) if self.internal.isChecked() else text)
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
