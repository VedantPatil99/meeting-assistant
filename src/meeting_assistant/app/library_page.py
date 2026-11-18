"""Past meetings: summary, full transcript and your notes, read from each meeting's folder.

Every timestamp in the summary and transcript is a link: clicking it plays the recording from
that moment and scrolls the transcript to the line, so a summary point can be checked against
what was said."""

from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QTextCursor, QTextFormat
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..anchors import parse_anchors
from ..models import AnchorKind, format_ts
from ..playback import Player
from ..session import MeetingSession
from . import theme

KIND_LABEL = {AnchorKind.MUST: "Must include", AnchorKind.CONTEXT: "Background",
              AnchorKind.INTERNAL: "🔒 Internal", AnchorKind.FEATURE: "Feature request"}


LEAD_IN = 1.5  # seconds of audio before the clicked moment, so the sentence isn't cut off
TS_CODE = re.compile(r"`\[(\d{2}):(\d{2}):(\d{2})\]`")
TS_LINE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*(?:([^:]{1,40}):\s)?(.*)$")


def _seconds(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def link_timestamps(markdown: str) -> str:
    """Turn the report's `[HH:MM:SS]` code spans into links that play the recording there."""
    return TS_CODE.sub(lambda m: f"[▶ {m.group(1)}:{m.group(2)}:{m.group(3)}](seek:{_seconds(*m.groups())})",
                       markdown)


def transcript_html(text: str) -> tuple[str, list[int], list[int]]:
    """The transcript with a play link and an anchor on every line, plus each timed line's start
    second and its paragraph number (to highlight the line being played)."""
    rows, starts, blocks = [], [], []
    for line in text.splitlines():
        m = TS_LINE.match(line.strip())
        if not m:
            if line.strip():
                rows.append(f"<p>{html.escape(line)}</p>")
            continue
        h, mi, s, speaker, said = m.groups()
        sec = _seconds(h, mi, s)
        starts.append(sec)
        blocks.append(len(rows))
        who = f"<b>{html.escape(speaker)}:</b> " if speaker else ""
        rows.append(f"<p><a name='t{sec}' href='seek:{sec}'>{h}:{mi}:{s}</a>&nbsp;&nbsp;{who}{html.escape(said)}</p>")
    return "".join(rows), starts, blocks


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
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(lambda item, _: self.show_meeting(item))
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(theme.title("Meetings"))
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
        for view in (self.summary, self.transcript):
            view.setOpenLinks(False)
            view.anchorClicked.connect(self.seek)
        self.player = Player()
        self.line_starts: list[int] = []
        self.line_blocks: list[int] = []
        self.playing_line = -1
        self.session: MeetingSession | None = None
        self.play_button = QPushButton("Play recording")
        self.play_button.clicked.connect(self.toggle_play)
        self.play_label = QLabel("Click any timestamp to hear that moment.")
        theme.set_role(self.play_label, "muted")
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(250)
        self.play_timer.timeout.connect(self._show_position)
        player_row = QHBoxLayout()
        player_row.addWidget(self.play_button)
        player_row.addWidget(self.play_label, 1)
        self.notes = QTextBrowser()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.summary, "Summary")
        self.tabs.addTab(self.transcript, "Transcript")
        self.tabs.addTab(self.notes, "Your notes")
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(header_row)
        right.addWidget(self.tabs)
        right.addLayout(player_row)
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
        self.stop_playback()
        self.session = MeetingSession(Path(item.data(Qt.UserRole))) if item else None
        has_audio = bool(self.session and any(p.exists() for p, _ in self.session.audio_tracks()))
        self.play_button.setEnabled(has_audio)
        self.play_label.setText("Click any timestamp to hear that moment." if has_audio
                                else "No recording for this meeting; timestamps jump to the transcript line.")
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
            theme.set_markdown(self.summary, link_timestamps(session.report_file.read_text(encoding="utf-8")))
        else:
            self.summary.setPlainText("Not processed yet. Press “Edit notes and process”.")
        if session.transcript_file.exists():
            body, self.line_starts, self.line_blocks = transcript_html(
                session.transcript_file.read_text(encoding="utf-8"))
            self.transcript.setHtml(body)
        else:
            self.line_starts, self.line_blocks = [], []
            self.transcript.setPlainText("No transcript yet.")
        self.playing_line = -1
        self.transcript.setExtraSelections([])
        self.notes.setHtml(notes_html(session.load_anchors()))

    def seek(self, url: QUrl) -> None:
        if url.scheme() != "seek":
            QDesktopServices.openUrl(url)
            return
        seconds = float(url.path())
        line = max((s for s in self.line_starts if s <= seconds), default=None)
        self.tabs.setCurrentWidget(self.transcript)
        if line is not None:
            self.transcript.scrollToAnchor(f"t{line}")
        if self.play_button.isEnabled():
            self.start_playback(max(seconds - LEAD_IN, 0))

    def start_playback(self, seconds: float) -> None:
        try:
            self.player.play([p for p, _ in self.session.audio_tracks()], seconds)
        except (OSError, ValueError) as e:
            self.play_label.setText(f"Can't play this recording: {e}")
            return
        self.play_button.setText("Stop")
        self.play_timer.start()

    def toggle_play(self) -> None:
        if self.player.playing:
            self.stop_playback()
        elif self.session:
            self.start_playback(self.player.position if self.player.position else 0)

    def stop_playback(self) -> None:
        self.player.stop()
        self.play_timer.stop()
        self.play_button.setText("Play recording")

    def _show_position(self) -> None:
        self.play_label.setText(f"▶ {format_ts(self.player.position)}")
        self._highlight(self.player.position)
        if not self.player.playing:
            self.stop_playback()
            self.play_label.setText("Finished. Click any timestamp to hear that moment.")

    def _highlight(self, seconds: float) -> None:
        """Shade the transcript line being played, and keep it in view."""
        line = max((i for i, s in enumerate(self.line_starts) if s <= seconds), default=-1)
        if line == self.playing_line or line < 0:
            return
        self.playing_line = line
        block = self.transcript.document().findBlockByNumber(self.line_blocks[line])
        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(block)
        selection.format.setBackground(QColor(theme.tokens().accent_soft))
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        self.transcript.setExtraSelections([selection])
        self.transcript.setTextCursor(QTextCursor(block))
        self.transcript.ensureCursorVisible()

    def hideEvent(self, event) -> None:  # leaving the page stops the audio
        self.stop_playback()
        super().hideEvent(event)

    def open_folder(self) -> None:
        item = self.list.currentItem()
        if item:
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.UserRole)))

    def process_again(self) -> None:
        item = self.list.currentItem()
        if item:
            self.reopen.emit(MeetingSession(Path(item.data(Qt.UserRole))))
