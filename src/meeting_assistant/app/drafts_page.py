"""Follow-up emails the app drafted after each meeting, one per suggested person.

Edit a draft here, then copy it, put it in your Gmail Drafts, or send it with Gmail.
Nothing is sent without pressing Send and confirming.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import after_meeting
from ..automations.store import Store
from ..gmail import GmailConnection
from .background import run_in_background

STATUS_LABEL = {"draft": "", "in gmail": "  ·  in Gmail Drafts"}


def split_addresses(text: str) -> list[str]:
    return [a for a in re.split(r"[,;\s]+", text) if a]


class DraftsPage(QWidget):
    count_changed = Signal(int)

    def __init__(self, store: Store, gmail: GmailConnection):
        super().__init__()
        self.store = store
        self.gmail = gmail
        self.current: int | None = None

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(lambda item, previous: self.select(item, previous))
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(QLabel("<b>Follow-up drafts</b>"))
        left.addWidget(self.list)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self.reason = QLabel()
        self.reason.setWordWrap(True)
        self.reason.setStyleSheet("color: palette(placeholder-text);")
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("background: #fff3e0; color: #8a4b00; padding: 6px; border-radius: 4px;")
        self.to = QLineEdit()
        self.to.setPlaceholderText("Recipient email(s), comma separated")
        self.subject = QLineEdit()
        self.body = QPlainTextEdit()
        form = QFormLayout()
        form.addRow("To", self.to)
        form.addRow("Subject", self.subject)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(lambda: self.save(quiet=False))
        copy = QPushButton("Copy")
        copy.clicked.connect(self.copy)
        self.gmail_button = QPushButton("Put in Gmail Drafts")
        self.gmail_button.clicked.connect(self.to_gmail)
        self.send_button = QPushButton("Send with Gmail")
        self.send_button.setStyleSheet("font-weight: 600;")
        self.send_button.clicked.connect(self.send)
        discard = QPushButton("Discard")
        discard.clicked.connect(self.discard)
        buttons = QHBoxLayout()
        for b in (self.save_button, copy, self.gmail_button, self.send_button):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(discard)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setOpenExternalLinks(True)

        right = QVBoxLayout()
        right.addWidget(self.reason)
        right.addWidget(self.warning)
        right.addLayout(form)
        right.addWidget(self.body, 1)
        right.addLayout(buttons)
        right.addWidget(self.status)
        self.editor = QWidget()
        self.editor.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(self.editor)
        splitter.setSizes([330, 690])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.refresh()

    def refresh(self, keep: int | None = None) -> None:
        keep = keep if keep is not None else self.current
        rows = self.store.drafts()
        self.list.blockSignals(True)
        self.list.clear()
        for r in rows:
            to = ", ".join(after_meeting.recipients(r)) or "no address yet"
            badge = "external" if r["audience"] == "external" else "internal"
            item = QListWidgetItem(f"{r['recipient']}  ({badge}){STATUS_LABEL.get(r['status'], '')}\n"
                                   f"{r['subject']}\n{r['meeting']}  ·  {to}")
            item.setData(Qt.UserRole, r["id"])
            if r["warning"]:
                item.setForeground(QColor("#8a4b00"))
            self.list.addItem(item)
            if r["id"] == keep:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.select(self.list.currentItem(), None)
        self.count_changed.emit(len(rows))

    def select(self, item: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if previous is not None and self.current is not None:
            self.save(quiet=True)  # don't lose edits when switching drafts
        draft_id = item.data(Qt.UserRole) if item else None
        row = self.store.draft(draft_id) if draft_id else None
        self.current = draft_id if row else None
        self.editor.setEnabled(row is not None)
        if row is None:
            self.reason.setText("Drafts appear here after a meeting is processed.")
            self.warning.hide()
            for w in (self.to, self.subject):
                w.clear()
            self.body.clear()
            return
        audience = ("outside your company: internal notes were kept out of this email"
                    if row["audience"] == "external" else "your team")
        self.reason.setText(f"For {row['recipient']} ({audience}). Why: {row['reason']}")
        self.warning.setText("⚠ " + row["warning"])
        self.warning.setVisible(bool(row["warning"]))
        self.to.setText(", ".join(after_meeting.recipients(row)))
        if not self.to.text():
            self.to.setPlaceholderText(f"Couldn't find an address for {row['recipient']}. Type it here; "
                                       "the app remembers it next time.")
        self.subject.setText(row["subject"])
        self.body.setPlainText(row["body"])
        connected = self.gmail.is_connected
        self.gmail_button.setEnabled(connected)
        self.send_button.setEnabled(connected)
        self.gmail_button.setText("Update Gmail draft" if row["gmail_id"] else "Put in Gmail Drafts")
        self.status.setText("" if connected else "Connect Gmail in Settings to send from here, or use Copy.")

    def save(self, quiet: bool = False) -> None:
        if self.current is None:
            return
        self.store.update_draft(self.current, to_addrs=split_addresses(self.to.text()),
                                subject=self.subject.text().strip(), body=self.body.toPlainText().strip())
        if not quiet:
            self.refresh(keep=self.current)
            self.status.setText("Saved.")

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(f"Subject: {self.subject.text()}\n\n{self.body.toPlainText()}")
        self.status.setText("Copied subject and body.")

    def _busy(self, message: str) -> None:
        self.status.setText(message)
        self.gmail_button.setEnabled(False)
        self.send_button.setEnabled(False)

    def _failed(self, message: str) -> None:
        self.status.setText(f"Gmail: {message}")
        self.gmail_button.setEnabled(True)
        self.send_button.setEnabled(True)

    def _gmail_call(self, step, busy: str, done_message: str) -> None:
        """Runs `step(store, gmail_client, draft_id)` off the UI thread; the window stays usable."""
        self.save(quiet=True)
        draft_id, client = self.current, self.gmail.client()
        self._busy(busy)

        def done(_):
            self.refresh(keep=draft_id)
            self.status.setText(done_message)

        run_in_background(self, lambda: step(self.store, client, draft_id), done, self._failed)

    def to_gmail(self) -> None:
        self._gmail_call(after_meeting.push_to_gmail, "Saving to Gmail…",
                         'Saved in your Gmail Drafts. <a href="https://mail.google.com/mail/u/0/#drafts">Open Gmail</a>')

    def send(self) -> None:
        to = split_addresses(self.to.text())
        if not to:
            self.status.setText("Add a recipient first.")
            return
        answer = QMessageBox.question(self, "Send email", f"Send “{self.subject.text()}” to {', '.join(to)} now?")
        if answer == QMessageBox.Yes:
            self._gmail_call(after_meeting.send, "Sending…", f"Sent to {', '.join(to)}.")

    def discard(self) -> None:
        if self.current is not None:
            self.store.update_draft(self.current, status="discarded")
            self.current = None
            self.refresh()
