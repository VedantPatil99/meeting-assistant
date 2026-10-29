"""Past meetings: summary, full transcript and your notes, read from each meeting's folder."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..anchors import parse_anchors
from ..models import AnchorKind, format_ts
from ..session import MeetingSession

KIND_LABEL = {AnchorKind.MUST: "Must include", AnchorKind.CONTEXT: "Background",
              AnchorKind.INTERNAL: "🔒 Internal", AnchorKind.FEATURE: "Feature request"}


def meeting_folders(base: Path) -> list[MeetingSession]:
    if not base.exists():
        return []
    sessions = [MeetingSession(p) for p in base.iterdir() if (p / "meeting.json").exists()]
    return sorted(sessions, key=lambda s: s.folder.name, reverse=True)


def notes_html(text: str) -> str:
    anchors = parse_anchors(text)
    if not anchors:
        return "<p>No notes for this meeting.</p>"
    rows = []
    for a in anchors:
        style = " style='background:#fff3e0'" if a.kind == AnchorKind.INTERNAL else ""
        when = format_ts(a.timestamp) if a.timestamp is not None else ""
        rows.append(f"<tr{style}><td>{when}</td><td><b>{KIND_LABEL[a.kind]}</b></td>"
                    f"<td>{html.escape(a.text)}</td></tr>")
    return ("<p>Internal notes are used in your summary and internal emails, "
            "never in emails to people outside your company.</p>"
            "<table cellpadding='6'>" + "".join(rows) + "</table>")


class LibraryPage(QWidget):
    reopen = Signal(object)  # MeetingSession to edit notes and process again on the Meeting page

    def __init__(self, meetings_dir: Path):
        super().__init__()
        self.meetings_dir = meetings_dir
        self.sessions: list[MeetingSession] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles and transcripts")
        self.search.textChanged.connect(self.refresh)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda item, _: self.show_meeting(item))
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(QLabel("<b>Meetings</b>"))
        left.addWidget(self.search)
        left.addWidget(self.list)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self.header = QLabel()
        self.header.setWordWrap(True)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self.open_folder)
        self.process_button = QPushButton("Edit notes and process")
        self.process_button.clicked.connect(self.process_again)
        header_row = QHBoxLayout()
        header_row.addWidget(self.header, 1)
        header_row.addWidget(self.process_button)
        header_row.addWidget(open_folder)
        self.summary = QTextBrowser()
        self.transcript = QTextBrowser()
        self.notes = QTextBrowser()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.summary, "Summary")
        self.tabs.addTab(self.transcript, "Transcript")
        self.tabs.addTab(self.notes, "Your notes")
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(header_row)
        right.addWidget(self.tabs)
        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([300, 720])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.refresh()

    def refresh(self, select: Path | None = None) -> None:
        query = self.search.text().strip().lower()
        current = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        select = str(select) if select else current
        self.list.blockSignals(True)
        self.list.clear()
        for session in meeting_folders(self.meetings_dir):
            info = session.load_info()
            if query and query not in info.title.lower() and not (
                session.transcript_file.exists()
                and query in session.transcript_file.read_text(encoding="utf-8").lower()
            ):
                continue
            state = "processed" if session.report_file.exists() else (
                "transcribed" if session.transcript_file.exists() else "recorded")
            item = QListWidgetItem(f"{info.title}\n{info.started_at[:16].replace('T', ' ')}  ·  "
                                   f"{format_ts(info.duration_seconds)}  ·  {state}")
            item.setData(Qt.UserRole, str(session.folder))
            self.list.addItem(item)
            if str(session.folder) == select:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.show_meeting(self.list.currentItem())

    def show_meeting(self, item: QListWidgetItem | None) -> None:
        self.process_button.setEnabled(item is not None)
        if item is None:
            self.header.setText("Meetings you record or process appear here.")
            for view in (self.summary, self.transcript, self.notes):
                view.clear()
            return
        session = MeetingSession(Path(item.data(Qt.UserRole)))
        info = session.load_info()
        people = ", ".join(info.attendees) or "no attendees listed"
        self.header.setText(f"<b>{html.escape(info.title)}</b>  ·  {info.meeting_type}  ·  {html.escape(people)}")
        if session.report_file.exists():
            self.summary.setMarkdown(session.report_file.read_text(encoding="utf-8"))
        else:
            self.summary.setPlainText("Not processed yet. Press “Edit notes and process”.")
        self.transcript.setPlainText(session.transcript_file.read_text(encoding="utf-8")
                                     if session.transcript_file.exists() else "No transcript yet.")
        self.notes.setHtml(notes_html(session.load_anchors()))

    def open_folder(self) -> None:
        item = self.list.currentItem()
        if item:
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.UserRole)))

    def process_again(self) -> None:
        item = self.list.currentItem()
        if item:
            self.reopen.emit(MeetingSession(Path(item.data(Qt.UserRole))))
