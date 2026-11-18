"""First-run setup: get Ollama running and the two models downloaded, without a terminal.

Shown on start until everything is ready (or the user skips), and from Settings later.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import setup_check
from ..automations.store import Store
from ..config import Settings
from . import theme
from .background import run_in_background

MODEL_PREF = "ollama_model"
DONE, TODO = "", ""  # check mark, warning


def with_saved_model(settings: Settings, store: Store) -> Settings:
    """The model picked in setup wins over the default and the .env file."""
    chosen = store.get_pref(MODEL_PREF)
    return replace(settings, ollama_model=chosen) if chosen else settings


def needs_setup(settings: Settings) -> bool:
    state = setup_check.ollama_state(settings.ollama_host)
    return not (state.running and state.has(settings.ollama_model)
                and setup_check.whisper_ready(settings.whisper_model))


def gigabytes(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


class Step(QWidget):
    """One row: a status icon, a title and explanation, and the step's controls on the right."""

    def __init__(self, number: int, title: str):
        super().__init__()
        self.icon = QLabel(TODO)
        self.icon.setStyleSheet(f"font-family: '{theme.ICON_FONT}'; font-size: 18px;")
        self.title = theme.section(f"{number}. {title}")
        self.detail = theme.muted("")
        self.controls = QHBoxLayout()
        self.controls.setContentsMargins(0, 4, 0, 0)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        self.progress.hide()
        self.icon.setFixedWidth(26)
        header = QHBoxLayout()
        header.addWidget(self.icon)
        header.addWidget(self.title, 1)
        body = QVBoxLayout()
        body.setContentsMargins(32, 0, 0, 0)  # lines up under the title, past the icon
        body.addWidget(self.detail)
        body.addLayout(self.controls)
        body.addWidget(self.progress)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addLayout(header)
        column.addLayout(body)

    def set_done(self, done: bool, detail: str) -> None:
        self.icon.setText(DONE if done else TODO)
        self.icon.setStyleSheet(f"font-family: '{theme.ICON_FONT}'; font-size: 18px; "
                                f"color: {theme.tokens().success if done else theme.tokens().muted};")
        self.detail.setText(detail)


class SetupDialog(QDialog):
    def __init__(self, settings: Settings, store: Store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set up Meeting Assistant")
        self.setMinimumWidth(560)
        self.settings, self.store = settings, store
        self.busy = False

        self.ollama = Step(1, "Ollama, the free program that runs the AI on this computer")
        self.get_ollama = QPushButton("Download Ollama")
        theme.set_role(self.get_ollama, "primary")
        self.get_ollama.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(setup_check.OLLAMA_DOWNLOAD)))
        self.start_button = QPushButton("Start Ollama")
        theme.set_role(self.start_button, "primary")
        self.start_button.clicked.connect(self.start_ollama)
        self.ollama.controls.addWidget(self.get_ollama)
        self.ollama.controls.addWidget(self.start_button)
        self.ollama.controls.addStretch()

        self.model = Step(2, "AI model")
        self.model_box = QComboBox()
        for name, about in setup_check.MODEL_CHOICES.items():
            self.model_box.addItem(f"{name}  ·  {about.split('.')[0]}", name)
            self.model_box.setItemData(self.model_box.count() - 1, about, Qt.ToolTipRole)
        index = self.model_box.findData(settings.ollama_model)
        self.model_box.setCurrentIndex(max(index, 0))
        self.model_box.currentIndexChanged.connect(self.refresh)
        self.pull_button = QPushButton("Download")
        theme.set_role(self.pull_button, "primary")
        self.pull_button.clicked.connect(self.pull_model)
        self.model.controls.addWidget(self.model_box, 1)
        self.model.controls.addWidget(self.pull_button)

        self.speech = Step(3, "Speech-to-text model")
        self.speech_button = QPushButton("Download (about 0.5 GB)")
        theme.set_role(self.speech_button, "primary")
        self.speech_button.clicked.connect(self.download_speech)
        self.speech.controls.addWidget(self.speech_button)
        self.speech.controls.addStretch()

        self.status = theme.muted("")
        self.skip = QPushButton("Skip for now")
        self.skip.clicked.connect(self.reject)
        self.finish = QPushButton("Start using the app")
        theme.set_role(self.finish, "primary")
        self.finish.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.skip)
        bottom.addWidget(self.finish)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetMinimumSize)  # rows never squash their buttons
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)
        layout.addWidget(theme.title("Set up Meeting Assistant"))
        layout.addWidget(theme.muted("Everything runs on this computer and is free. Recordings never leave it. "
                                     "This is a one-time setup; the downloads can take a while."))
        for step in (self.ollama, self.model, self.speech):
            layout.addWidget(step)
        layout.addLayout(bottom)

        # notices Ollama as soon as it's installed or started, without a "check again" button
        self.poll = QTimer(self)
        self.poll.setInterval(3000)
        self.poll.timeout.connect(self.refresh)
        self.poll.start()
        self.speech_ok = setup_check.whisper_ready(settings.whisper_model)
        self.refresh()

    @property
    def chosen_model(self) -> str:
        return self.model_box.currentData()

    def refresh(self) -> None:
        state = setup_check.ollama_state(self.settings.ollama_host)
        if state.running:
            self.ollama.set_done(True, "Running.")
        elif state.installed:
            self.ollama.set_done(False, "Installed but not running.")
        else:
            self.ollama.set_done(False, "Not installed yet. Download it, run the installer, then come back here.")
        self.get_ollama.setVisible(not state.installed)
        self.start_button.setVisible(state.installed and not state.running)

        has_model = state.has(self.chosen_model)
        if not self.busy:
            self.model.set_done(has_model, "Downloaded." if has_model else
                                "Downloads through Ollama, so finish step 1 first." if not state.running else
                                "Pick one and download it. You can switch later in Settings.")
            self.pull_button.setEnabled(state.running and not has_model)
            self.speech.set_done(self.speech_ok, "Downloaded." if self.speech_ok else
                                 "Turns your recordings into text. Downloaded once from Hugging Face.")
            self.speech_button.setVisible(not self.speech_ok)
        self.model_box.setEnabled(not self.busy)
        ready = state.running and has_model and self.speech_ok
        self.finish.setEnabled(ready and not self.busy)
        if ready:
            self.store.set_pref(MODEL_PREF, self.chosen_model)

    def start_ollama(self) -> None:
        exe = setup_check.find_ollama()
        if exe is None:
            self.status.setText("Couldn't find Ollama. Install it from the link in step 1.")
            return
        setup_check.start_ollama(exe)
        self.status.setText("Starting Ollama…")
        QTimer.singleShot(2500, self.refresh)

    def _busy(self, step: Step, on: bool) -> None:
        self.busy = on
        step.progress.setVisible(on)
        for b in (self.pull_button, self.speech_button, self.skip):
            b.setEnabled(not on)
        self.refresh()

    def _failed(self, step: Step, message: str) -> None:
        self._busy(step, False)
        self.status.setText(f"Download stopped: {message}. Press Download to resume.")

    def pull_model(self) -> None:
        model, host = self.chosen_model, self.settings.ollama_host
        self._busy(self.model, True)
        self.model.progress.setRange(0, 0)

        def progress(event) -> None:
            status, done, total = event
            if total:
                self.model.progress.setRange(0, 1000)
                self.model.progress.setValue(int(done * 1000 / total))
                self.model.detail.setText(f"Downloading {model}: {gigabytes(done)} of {gigabytes(total)}")
            else:
                self.model.detail.setText(status.capitalize())

        def done(_) -> None:
            self._busy(self.model, False)
            self.status.setText(f"{model} is ready.")

        run_in_background(self, lambda report: setup_check.pull_model(host, model, report),
                          done, lambda m: self._failed(self.model, m), progress)

    def download_speech(self) -> None:
        name = self.settings.whisper_model
        self._busy(self.speech, True)
        self.speech.progress.setRange(0, 0)  # Hugging Face's downloader doesn't report progress here
        self.speech.detail.setText(f"Downloading the {name} speech model…")

        def done(_) -> None:
            self.speech_ok = True
            self._busy(self.speech, False)

        run_in_background(self, lambda: setup_check.download_whisper(name), done,
                          lambda m: self._failed(self.speech, m))

    def done(self, result: int) -> None:
        self.poll.stop()
        super().done(result)
