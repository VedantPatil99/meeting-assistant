"""Minimal desktop window: take timestamped anchors during a meeting, then process a transcript.

Run with:  python -m meeting_assistant.app.main
Recording, tray icon and global hotkeys come in later milestones.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..anchors import parse_anchors
from ..config import Settings
from ..llm import LLMError, OllamaProvider
from ..models import format_ts
from ..pipeline import process_meeting
from ..report import render_markdown
from ..transcript import parse_transcript

MEETING_TYPES = ["client feedback", "general", "standup", "sprint planning", "1:1"]


class Worker(QObject):
    progress = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, transcript_path: str, anchors_text: str, title: str, meeting_type: str):
        super().__init__()
        self.transcript_path = transcript_path
        self.anchors_text = anchors_text
        self.title = title
        self.meeting_type = meeting_type

    def run(self) -> None:
        try:
            transcript = parse_transcript(Path(self.transcript_path).read_text(encoding="utf-8"))
            report = process_meeting(
                OllamaProvider(Settings.from_env()),
                transcript,
                parse_anchors(self.anchors_text),
                title=self.title,
                meeting_type=self.meeting_type,
                on_progress=self.progress.emit,
            )
            self.finished.emit(render_markdown(report))
        except (LLMError, OSError) as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Meeting Assistant")
        self.resize(1100, 700)
        self.meeting_started: float | None = None
        self.thread: QThread | None = None

        # left: meeting setup and anchors
        self.title_edit = QLineEdit("Meeting")
        self.type_combo = QComboBox()
        self.type_combo.addItems(MEETING_TYPES)
        self.transcript_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.pick_transcript)
        transcript_row = QHBoxLayout()
        transcript_row.addWidget(self.transcript_edit)
        transcript_row.addWidget(browse)

        form = QFormLayout()
        form.addRow("Title", self.title_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("Transcript", transcript_row)

        self.timer_button = QPushButton("Start meeting clock")
        self.timer_button.clicked.connect(self.start_clock)
        self.anchor_input = QLineEdit()
        self.anchor_input.setPlaceholderText("Type a note and press Enter  (! must, ctx:, internal:, fr:)")
        self.anchor_input.returnPressed.connect(self.add_anchor)
        self.anchors_edit = QPlainTextEdit()
        self.process_button = QPushButton("Process meeting")
        self.process_button.clicked.connect(self.process)
        self.status = QLabel("Ready")

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self.timer_button)
        left.addWidget(self.anchor_input)
        left.addWidget(QLabel("Your anchors"))
        left.addWidget(self.anchors_edit)
        left.addWidget(self.process_button)
        left.addWidget(self.status)
        left_widget = QWidget()
        left_widget.setLayout(left)

        # right: report preview
        self.report_view = QTextBrowser()
        self.report_view.setPlaceholderText("The report will appear here.")

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(self.report_view)
        splitter.setSizes([420, 680])
        self.setCentralWidget(splitter)

    def pick_transcript(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose transcript", "", "Text files (*.txt)")
        if path:
            self.transcript_edit.setText(path)

    def start_clock(self) -> None:
        self.meeting_started = time.monotonic()
        self.timer_button.setText("Clock running: new anchors get timestamps")
        self.timer_button.setEnabled(False)

    def add_anchor(self) -> None:
        text = self.anchor_input.text().strip()
        if not text:
            return
        if self.meeting_started is not None:
            text = f"[{format_ts(time.monotonic() - self.meeting_started)}] {text}"
        self.anchors_edit.appendPlainText(text)
        self.anchor_input.clear()

    def process(self) -> None:
        if not self.transcript_edit.text():
            self.status.setText("Choose a transcript first.")
            return
        self.process_button.setEnabled(False)
        self.thread = QThread()
        self.worker = Worker(
            self.transcript_edit.text(),
            self.anchors_edit.toPlainText(),
            self.title_edit.text(),
            self.type_combo.currentText(),
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self.show_report)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def show_report(self, markdown: str) -> None:
        self.report_view.setMarkdown(markdown)
        self.status.setText("Done")
        self.process_button.setEnabled(True)

    def show_error(self, message: str) -> None:
        self.status.setText(f"Error: {message}")
        self.process_button.setEnabled(True)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
