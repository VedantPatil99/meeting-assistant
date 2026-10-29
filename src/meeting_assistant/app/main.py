"""Desktop app: record a meeting, add notes from anywhere with a hotkey, then process it into a
summary, scheduled tasks and follow-up email drafts. Gmail and Notion are optional connectors.

Run with:  python -m meeting_assistant.app.main   (or the meeting-assistant-app shortcut)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..after_meeting import save_suggestions
from ..anchors import parse_anchors
from ..automations import Engine, Prefs, Store, events_from_report
from ..config import Settings
from ..gmail import GmailConnection
from ..llm import LLMError, OllamaProvider
from ..models import format_ts
from ..pipeline import process_meeting
from ..pipeline.followups import suggest_followups
from ..recorder import Recorder
from ..session import MeetingInfo, MeetingSession
from ..transcript import parse_transcript
from ..notion.connection import NotionConnection
from .automations_page import AutomationsPage
from .drafts_page import DraftsPage
from .gmail_settings import GmailSettings
from .library_page import LibraryPage
from .notion_settings import NotionSettings
from .hotkeys import MOD_CONTROL, MOD_SHIFT, VK_SPACE, HotkeyThread
from .quick_anchor import QuickAnchor, mark_internal
from .tasks_page import TasksPage

MEETING_TYPES = ["client feedback", "general", "standup", "sprint planning", "1:1"]
NAV = ["Meeting", "Meetings", "Tasks", "Drafts", "Automations", "Settings"]
NAV_TASKS, NAV_DRAFTS, NAV_AUTOMATIONS = 2, 3, 4
HOTKEY_NOTE = 1
HOTKEY_LABEL = "Ctrl+Shift+Space"


def dot_icon(color: str) -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(8, 8, 48, 48)
    painter.end()
    return QIcon(pixmap)


def heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 18px; font-weight: 600;")
    return label


class ProcessWorker(QObject):
    """Transcribes the recording if needed, runs the pipeline, then drafts follow-ups, off the UI thread."""

    progress = Signal(str)
    finished = Signal(object, object)  # MeetingReport, list[SuggestedEmail]
    failed = Signal(str)

    def __init__(self, settings: Settings, anchors_text: str, title: str, meeting_type: str,
                 session: MeetingSession, attendees: list[str], prefs: Prefs, contacts: dict[str, str]):
        super().__init__()
        self.settings = settings
        self.anchors_text = anchors_text
        self.title = title
        self.meeting_type = meeting_type
        self.session = session
        self.attendees = attendees
        self.prefs = prefs
        self.contacts = contacts

    def run(self) -> None:
        try:
            transcript = self._transcript()
            if not transcript.segments:
                self.failed.emit("No speech was found in the recording.")
                return
            provider = OllamaProvider(self.settings)
            report = process_meeting(
                provider,
                transcript,
                parse_anchors(self.anchors_text),
                title=self.title,
                meeting_type=self.meeting_type,
                on_progress=self.progress.emit,
            )
        except (LLMError, OSError, RuntimeError) as e:
            self.failed.emit(str(e))
            return
        self.progress.emit("Drafting follow-up emails")
        try:
            emails = suggest_followups(provider, report, self.attendees, self.prefs, self.contacts,
                                       meeting_date=self.session.load_info().started_at)
        except LLMError:
            emails = []  # the summary is still useful without drafts
        self.finished.emit(report, emails)

    def _transcript(self):
        if self.session.transcript_file.exists():
            return parse_transcript(self.session.transcript_file.read_text(encoding="utf-8"))

        from ..transcribe import transcribe_tracks  # heavy import, only when needed

        self.progress.emit("Loading speech-to-text model")
        transcript = transcribe_tracks(
            self.session.audio_tracks(),
            self.settings,
            on_progress=lambda speaker, s: self.progress.emit(f"Transcribing {speaker}: {format_ts(s)}"),
        )
        self.session.save_transcript(transcript)
        return transcript


class MeetingPage(QWidget):
    recording_changed = Signal(bool)
    processed = Signal(object, str, list, object, object)  # report, date, attendees, folder, follow-up emails

    def __init__(self, settings: Settings, store: Store):
        super().__init__()
        self.settings = settings
        self.store = store
        self.recorder = Recorder()
        self.session: MeetingSession | None = None
        self.transcript_path: str | None = None
        self.thread: QThread | None = None

        self.title_edit = QLineEdit("Meeting")
        self.type_combo = QComboBox()
        self.type_combo.addItems(MEETING_TYPES)
        self.attendees_edit = QLineEdit()
        self.attendees_edit.setPlaceholderText("Emails, comma separated (used by automations)")
        form = QFormLayout()
        form.addRow("Title", self.title_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("Attendees", self.attendees_edit)

        self.record_button = QPushButton("Start recording")
        self.record_button.setMinimumHeight(36)
        self.record_button.clicked.connect(self.toggle_recording)
        self.clock = QLabel("00:00:00")
        self.clock.setStyleSheet("font-size: 20px; font-weight: 600; font-family: Consolas, monospace;")
        self.mic_level = self._meter()
        self.system_level = self._meter()
        meters = QGridLayout()
        meters.addWidget(QLabel("Your mic"), 0, 0)
        meters.addWidget(self.mic_level, 0, 1)
        meters.addWidget(QLabel("Call audio"), 1, 0)
        meters.addWidget(self.system_level, 1, 1)
        record_row = QHBoxLayout()
        record_row.addWidget(self.record_button, 1)
        record_row.addWidget(self.clock)

        self.anchor_input = QLineEdit()
        self.anchor_input.setPlaceholderText(f"Add a note and press Enter  ({HOTKEY_LABEL} works from any app)")
        self.anchor_input.returnPressed.connect(self._add_from_input)
        self.internal_check = QCheckBox("Internal")
        self.internal_check.setToolTip("Only your team sees this note. It's used in your summary and internal "
                                       "emails, never in emails to people outside your company.")
        note_row = QHBoxLayout()
        note_row.addWidget(self.anchor_input, 1)
        note_row.addWidget(self.internal_check)
        self.anchors_edit = QPlainTextEdit()
        self.anchors_edit.setPlaceholderText(
            "! must include      ctx: background only\ninternal: never client-facing      fr: feature request"
        )

        self.process_button = QPushButton("Process meeting")
        self.process_button.setMinimumHeight(36)
        self.process_button.setEnabled(False)
        self.process_button.clicked.connect(self.process)
        open_transcript = QPushButton("Use a transcript file…")
        open_transcript.clicked.connect(self.pick_transcript)
        self.open_folder_button = QPushButton("Open meeting folder")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self.open_folder)
        actions = QHBoxLayout()
        actions.addWidget(open_transcript)
        actions.addWidget(self.open_folder_button)
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(heading("Meeting"))
        left.addLayout(form)
        left.addLayout(record_row)
        left.addLayout(meters)
        left.addWidget(QLabel("Your notes (anchors)"))
        left.addLayout(note_row)
        left.addWidget(self.anchors_edit, 1)
        left.addWidget(self.process_button)
        left.addLayout(actions)
        left.addWidget(self.status)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(True)
        self.report_view.setPlaceholderText("The report appears here after processing.")

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(self.report_view)
        splitter.setSizes([440, 640])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self.ticker = QTimer(self)
        self.ticker.setInterval(100)
        self.ticker.timeout.connect(self._tick)

    @staticmethod
    def _meter() -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setMaximumHeight(10)
        return bar

    # recording
    @property
    def is_recording(self) -> bool:
        return self.recorder.is_recording

    def toggle_recording(self) -> None:
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def attendees(self) -> list[str]:
        return [a.strip() for a in self.attendees_edit.text().replace(";", ",").split(",") if a.strip()]

    def start_recording(self) -> None:
        try:
            self.session = MeetingSession.create(
                self.settings.meetings_dir, self.title_edit.text(), self.type_combo.currentText(), self.attendees()
            )
            self.recorder.start(self.session.folder)
        except Exception as e:  # device errors come from PortAudio as plain exceptions
            self.status.setText(f"Could not start recording: {e}")
            return
        self.transcript_path = None
        self.anchors_edit.clear()
        self.report_view.clear()
        self.record_button.setText("Stop recording")
        self.record_button.setStyleSheet("background: #c62828; color: white; font-weight: 600;")
        self.process_button.setEnabled(False)
        self.open_folder_button.setEnabled(True)
        self.status.setText(f"Recording to {self.session.folder}")
        self.ticker.start()
        self.recording_changed.emit(True)

    def stop_recording(self) -> None:
        duration = self.recorder.stop()
        self.ticker.stop()
        info = self.session.load_info()
        info.duration_seconds = round(duration, 1)
        self.session.save_info(info)
        self.session.save_anchors(self.anchors_edit.toPlainText())
        self.record_button.setText("Start recording")
        self.record_button.setStyleSheet("")
        self.mic_level.setValue(0)
        self.system_level.setValue(0)
        self.process_button.setEnabled(True)
        self.status.setText(f"Saved {format_ts(duration)} of audio. Review your notes, then process.")
        self.recording_changed.emit(False)

    def _tick(self) -> None:
        self.clock.setText(format_ts(self.recorder.elapsed))
        self.mic_level.setValue(int(self.recorder.level("mic") * 100))
        self.system_level.setValue(int(self.recorder.level("system") * 100))

    # notes
    def clock_text(self) -> str:
        return format_ts(self.recorder.elapsed) if self.is_recording else "--:--:--"

    def add_anchor(self, text: str) -> None:
        if self.is_recording:
            text = f"[{format_ts(self.recorder.elapsed)}] {text}"
        self.anchors_edit.appendPlainText(text)

    def _add_from_input(self) -> None:
        text = self.anchor_input.text().strip()
        if text:
            self.add_anchor(mark_internal(text) if self.internal_check.isChecked() else text)
            self.anchor_input.clear()

    def load_session(self, session: MeetingSession) -> None:
        """Reopen a saved meeting to change its notes or details and process it again."""
        if self.is_recording:
            self.status.setText("Stop the current recording first.")
            return
        info = session.load_info()
        self.session, self.transcript_path = session, None
        self.title_edit.setText(info.title)
        self.type_combo.setCurrentText(info.meeting_type)
        self.attendees_edit.setText(", ".join(info.attendees))
        self.anchors_edit.setPlainText(session.load_anchors())
        self.report_view.setMarkdown(session.report_file.read_text(encoding="utf-8")
                                     if session.report_file.exists() else "")
        self.process_button.setEnabled(True)
        self.open_folder_button.setEnabled(True)
        self.status.setText(f"Opened “{info.title}”. Edit your notes, then process.")

    # processing
    def pick_transcript(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a transcript", "", "Text files (*.txt)")
        if path:
            self.transcript_path = path
            self.session = None
            self.open_folder_button.setEnabled(False)
            self.process_button.setEnabled(True)
            self.status.setText(f"Transcript: {Path(path).name}")

    def process(self) -> None:
        if self.transcript_path:
            # a transcript file becomes a meeting folder too, so it shows up under Meetings
            self.session = MeetingSession.create(self.settings.meetings_dir, self.title_edit.text(),
                                                 self.type_combo.currentText(), self.attendees())
            self.session.transcript_file.write_text(Path(self.transcript_path).read_text(encoding="utf-8"),
                                                    encoding="utf-8")
            self.transcript_path = None
            self.open_folder_button.setEnabled(True)
        self.session.save_anchors(self.anchors_edit.toPlainText())
        info: MeetingInfo = self.session.load_info()
        info.title, info.meeting_type = self.title_edit.text(), self.type_combo.currentText()
        info.attendees = self.attendees()
        self.session.save_info(info)
        self.process_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self.thread = QThread()
        self.worker = ProcessWorker(
            self.settings, self.anchors_edit.toPlainText(), self.title_edit.text(),
            self.type_combo.currentText(), self.session, self.attendees(),
            self.store.prefs(), self.store.contacts(),
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self._show_report)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _show_report(self, report, emails) -> None:
        markdown = self.session.save_report(report)
        self.report_view.setMarkdown(markdown)
        self.status.setText("Done.")
        self._processing_done()
        self.processed.emit(report, self.session.load_info().started_at, self.attendees(),
                            self.session.folder, emails)

    def _show_error(self, message: str) -> None:
        self.status.setText(f"Error: {message}")
        self._processing_done()

    def _processing_done(self) -> None:
        self.process_button.setEnabled(True)
        self.record_button.setEnabled(True)

    def open_folder(self) -> None:
        if self.session:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.folder)))


class SettingsPage(QWidget):
    def __init__(self, settings: Settings, store: Store, notion: NotionConnection, gmail: GmailConnection):
        super().__init__()
        self.settings = settings
        self.store = store
        self.notion = NotionSettings(notion)
        self.gmail = GmailSettings(gmail)
        prefs = store.prefs()
        self.my_email = QLineEdit(prefs.my_email)
        self.my_email.setPlaceholderText("you@yourcompany.com")
        self.company_domain = QLineEdit(prefs.company_domain)
        self.company_domain.setPlaceholderText("yourcompany.com")
        save = QPushButton("Save")
        save.clicked.connect(self.save_prefs)
        self.prefs_status = QLabel("")
        you = QFormLayout()
        you.addRow("Your email", self.my_email)
        you.addRow("Company email domain", self.company_domain)
        you.addRow("", save)
        you.addRow("", self.prefs_status)
        explain = QLabel("Attendees whose email ends with your company domain count as internal. "
                         "Everyone else is external, so internal notes never reach them.")
        explain.setWordWrap(True)
        explain.setStyleSheet("color: palette(placeholder-text);")

        form = QFormLayout()
        form.addRow("AI model", QLabel(f"{settings.ollama_model}  (Ollama at {settings.ollama_host})"))
        form.addRow("Speech to text", QLabel(f"faster-whisper {settings.whisper_model}, device: {settings.whisper_device}"))
        form.addRow("Meetings folder", QLabel(str(settings.meetings_dir)))
        form.addRow("Add-note shortcut", QLabel(HOTKEY_LABEL))
        self.model_status = QLabel("")
        check = QPushButton("Check AI model")
        check.clicked.connect(self.check_model)
        open_folder = QPushButton("Open meetings folder")
        open_folder.clicked.connect(self.open_meetings)
        buttons = QHBoxLayout()
        buttons.addWidget(check)
        buttons.addWidget(open_folder)
        buttons.addStretch()
        note = QLabel("Change these in a .env file next to the app (see .env.example).")
        note.setStyleSheet("color: palette(placeholder-text);")

        layout = QVBoxLayout(self)
        layout.addWidget(heading("Settings"))
        layout.addWidget(QLabel("<b>You</b>"))
        layout.addLayout(you)
        layout.addWidget(explain)
        layout.addSpacing(16)
        layout.addWidget(QLabel("<b>Gmail</b> (optional: send follow-ups from the app)"))
        layout.addWidget(self.gmail)
        layout.addSpacing(16)
        layout.addWidget(QLabel("<b>Notion</b> (optional: also keep meetings and tasks in Notion)"))
        layout.addWidget(self.notion)
        layout.addSpacing(16)
        layout.addWidget(QLabel("<b>Models and storage</b>"))
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.model_status)
        layout.addWidget(note)
        layout.addStretch()

    def save_prefs(self) -> None:
        self.store.save_prefs(Prefs(my_email=self.my_email.text().strip(),
                                    company_domain=self.company_domain.text().strip().lstrip("@")))
        self.prefs_status.setText("Saved.")

    def check_model(self) -> None:
        ok = OllamaProvider(self.settings).is_available()
        self.model_status.setText(
            "AI model is ready." if ok
            else f"Ollama isn't running or {self.settings.ollama_model} isn't installed."
        )

    def open_meetings(self) -> None:
        self.settings.meetings_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.meetings_dir)))


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.setWindowTitle("Meeting Assistant")
        self.resize(1180, 740)
        self.idle_icon, self.recording_icon = dot_icon("#9e9e9e"), dot_icon("#e53935")
        self.setWindowIcon(self.idle_icon)

        self.store = Store(settings.meetings_dir / "assistant.db")
        self.notion = NotionConnection(self.store)
        self.engine = Engine(self.store, settings.meetings_dir / "outputs", notifier=self.notify,
                             notion=self.notion.sync)

        self.gmail = GmailConnection(self.store)

        self.meeting = MeetingPage(settings, self.store)
        self.meeting.recording_changed.connect(self._on_recording_changed)
        self.meeting.processed.connect(self._after_meeting)
        self.library = LibraryPage(settings.meetings_dir)
        self.library.reopen.connect(self._reopen_meeting)
        self.tasks = TasksPage(self.store, self.notion)
        self.tasks.counts_changed.connect(lambda n: self._badge(NAV_TASKS, n))
        self.drafts = DraftsPage(self.store, self.gmail)
        self.drafts.count_changed.connect(lambda n: self._badge(NAV_DRAFTS, n))
        self.automations = AutomationsPage(self.store, self.engine, settings.meetings_dir)
        self.automations.approvals_changed.connect(lambda n: self._badge(NAV_AUTOMATIONS, n))
        self.settings_page = SettingsPage(settings, self.store, self.notion, self.gmail)
        self.settings_page.gmail.changed.connect(self.drafts.refresh)
        self.pages = QStackedWidget()
        for page in (self.meeting, self.library, self.tasks, self.drafts, self.automations, self.settings_page):
            self.pages.addWidget(page)

        self.nav = QListWidget()
        self.nav.addItems(NAV)
        self.nav.setFixedWidth(160)
        self.nav.setStyleSheet("QListWidget { font-size: 14px; } QListWidget::item { padding: 10px; }")
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body = QWidget()
        row = QHBoxLayout(body)
        row.addWidget(self.nav)
        row.addWidget(self.pages, 1)
        self.setCentralWidget(body)

        self.quick = QuickAnchor()
        self.quick.submitted.connect(self.meeting.add_anchor)
        self.hotkeys = HotkeyThread({HOTKEY_NOTE: (MOD_CONTROL | MOD_SHIFT, VK_SPACE)})
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.failed.connect(self.meeting.status.setText)
        self.hotkeys.start()

        self.tray = self._make_tray() if QSystemTrayIcon.isSystemTrayAvailable() else None
        for page in (self.automations, self.tasks, self.drafts):
            page.refresh()

        self.reminders = QTimer(self)
        self.reminders.setInterval(15 * 60 * 1000)
        self.reminders.timeout.connect(self.remind_due_tasks)
        self.reminders.start()
        QTimer.singleShot(3000, self.remind_due_tasks)

    def notify(self, title: str, message: str) -> None:
        if self.tray:
            self.tray.showMessage(title, message)

    def _after_meeting(self, report, date: str, attendees: list, folder, emails) -> None:
        tasks, drafts = save_suggestions(self.store, report, date, folder, emails)
        parts = [f"{tasks} task{'s' if tasks != 1 else ''} to schedule in Tasks",
                 f"{drafts} follow-up draft{'s' if drafts != 1 else ''} in Drafts"]
        outcomes = self.engine.handle(events_from_report(report, date, attendees, folder))
        if outcomes:
            counts: dict[str, int] = {}
            for o in outcomes:
                counts[o.status] = counts.get(o.status, 0) + 1
            parts.append("automations: " + ", ".join(f"{n} {status}" for status, n in counts.items()))
        self.meeting.status.setText("Done. " + "; ".join(parts) + ".")
        for page in (self.automations, self.tasks, self.drafts):
            page.refresh()
        self.library.refresh(select=folder)

    def _reopen_meeting(self, session) -> None:
        self.meeting.load_session(session)
        self.nav.setCurrentRow(NAV.index("Meeting"))

    def _badge(self, row: int, count: int) -> None:
        self.nav.item(row).setText(f"{NAV[row]} ({count})" if count else NAV[row])

    def remind_due_tasks(self) -> None:
        today = date.today().isoformat()
        due = self.store.due_for_reminder(today)
        if not due:
            return
        names = "; ".join(r["text"] for r in due[:3]) + ("…" if len(due) > 3 else "")
        self.notify(f"{len(due)} task{'s' if len(due) > 1 else ''} due", names)
        for r in due:
            self.store.update_task(r["id"], reminded_on=today)
        self.tasks.refresh()

    def _make_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(self.idle_icon, self)
        tray.setToolTip("Meeting Assistant")
        menu = QMenu(self)
        menu.addAction(QAction("Show window", self, triggered=self.show_window))
        self.tray_record = QAction("Start recording", self, triggered=self.meeting.toggle_recording)
        menu.addAction(self.tray_record)
        menu.addAction(QAction(f"Add note ({HOTKEY_LABEL})", self, triggered=lambda: self._on_hotkey(HOTKEY_NOTE)))
        menu.addSeparator()
        menu.addAction(QAction("Quit", self, triggered=self.quit))
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None
        )
        tray.show()
        return tray

    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_NOTE:
            self.quick.popup(self.meeting.clock_text())

    def _on_recording_changed(self, recording: bool) -> None:
        icon = self.recording_icon if recording else self.idle_icon
        self.setWindowIcon(icon)
        if self.tray:
            self.tray.setIcon(icon)
            self.tray.setToolTip("Meeting Assistant: recording" if recording else "Meeting Assistant")
            self.tray_record.setText("Stop recording" if recording else "Start recording")

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if self.meeting.is_recording and self.tray:
            # keep recording in the background; the tray icon stays red
            event.ignore()
            self.hide()
            self.tray.showMessage("Still recording", "Meeting Assistant keeps recording from the tray.")
            return
        self.quit()

    def quit(self) -> None:
        if self.meeting.is_recording:
            self.meeting.stop_recording()
        self.hotkeys.stop()
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(Settings.from_env())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
