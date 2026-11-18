"""Ask a question across all your meetings. Each answer lists the moments it comes from; clicking
one opens that meeting and plays the recording there."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from ..ask import AskResult, MeetingIndex, ask
from ..config import Settings
from ..llm import OllamaEmbedder, OllamaProvider
from ..models import format_ts
from ..setup_check import pull_model
from . import theme
from .background import run_in_background
from .library_page import meeting_folders

INDEX_FILE = "search-index.sqlite"
EXAMPLES = ["What did Rahul say about pricing last month?", "Who owns the licence breakdown?",
            "What was decided about the go-live date?"]


class MissingSearchModel(RuntimeError):
    pass


def answer_html(result: AskResult) -> str:
    body = f"<p style='font-size:14px'>{html.escape(result.answer)}</p>"
    if result.sources:
        body += "<p><b>From</b></p>"
        for i, s in enumerate(result.sources):
            when = f" · {format_ts(s.start)}" if s.start is not None else ""
            body += (f"<p><a href='open:{i}'>▶ {html.escape(s.title)} · {s.date}{when}</a><br>"
                     f"<i>“{html.escape(s.text)}”</i></p>")
    return body


class AskPage(QWidget):
    open_source = Signal(str, object)  # meeting folder, seconds (or None)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.sources = []
        self.question = QLineEdit()
        self.question.setPlaceholderText("Ask about any of your meetings, e.g. " + EXAMPLES[0])
        self.question.returnPressed.connect(self.ask)
        self.ask_button = QPushButton("Ask")
        theme.set_role(self.ask_button, "primary")
        self.ask_button.clicked.connect(self.ask)
        self.download_button = QPushButton(f"Download the search model ({settings.embed_model}, about 270 MB)")
        self.download_button.clicked.connect(self.download)
        self.download_button.hide()
        row = QHBoxLayout()
        row.addWidget(self.question, 1)
        row.addWidget(self.ask_button)
        self.status = theme.muted("Answers come only from your meetings, with the moment each one is from.")
        self.status.setWordWrap(True)
        self.result = QTextBrowser()
        self.result.setOpenLinks(False)
        self.result.anchorClicked.connect(self._open)
        self.result.setHtml("<p>Try:</p>" + "".join(
            f"<p><a href='ask:{i}'>{html.escape(q)}</a></p>" for i, q in enumerate(EXAMPLES)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(theme.title("Ask your meetings"))
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addWidget(self.download_button)
        layout.addWidget(self.result, 1)

    def ask(self) -> None:
        question = self.question.text().strip()
        if not question:
            return
        self.ask_button.setEnabled(False)
        self.status.setText("Searching your meetings…")
        settings = self.settings

        def work(report):
            embedder = OllamaEmbedder(settings)
            if not embedder.is_available():
                raise MissingSearchModel(f"Searching needs the {settings.embed_model} model, which isn't "
                                         "downloaded yet (or Ollama isn't running).")
            index = MeetingIndex(settings.meetings_dir / INDEX_FILE, embedder)
            index.update(meeting_folders(settings.meetings_dir),
                         report=lambda done, total: report(f"Reading meetings into the search index ({done} of {total})…"))
            report("Writing the answer…")
            return ask(OllamaProvider(settings), index, question)

        run_in_background(self, work, self._show, self._failed, on_progress=self.status.setText)

    def _show(self, result: AskResult) -> None:
        self.ask_button.setEnabled(True)
        self.download_button.hide()
        self.sources = result.sources
        self.status.setText("Click a source to open that meeting at that moment." if result.sources else "")
        self.result.setHtml(answer_html(result))

    def _failed(self, message: str) -> None:
        self.ask_button.setEnabled(True)
        self.status.setText(message)
        self.download_button.setVisible("isn't downloaded" in message)

    def download(self) -> None:
        self.download_button.setEnabled(False)
        settings = self.settings

        def work(report):
            pull_model(settings.ollama_host, settings.embed_model, report)

        def progress(value) -> None:
            _, done_bytes, total = value
            pct = f" {done_bytes / total:.0%}" if total else ""
            self.status.setText(f"Downloading the search model…{pct}")

        def done(_) -> None:
            self.download_button.setEnabled(True)
            self.download_button.hide()
            self.status.setText("Search model ready. Ask your question.")

        def failed(message: str) -> None:
            self.download_button.setEnabled(True)
            self.status.setText(f"Download failed: {message}")

        run_in_background(self, work, done, failed, on_progress=progress)

    def _open(self, url: QUrl) -> None:
        kind, _, value = url.toString().partition(":")
        if kind == "ask":
            self.question.setText(EXAMPLES[int(value)])
            self.ask()
        elif kind == "open":
            source = self.sources[int(value)]
            self.open_source.emit(source.folder, source.start)
