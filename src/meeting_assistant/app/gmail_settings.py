"""Settings section for connecting Gmail: save your own OAuth client once, then sign in with Google."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ..gmail import GmailConnection, GmailError
from .background import run_in_background

STEPS = (
    "Free, one-time setup in Google Cloud (about 5 minutes):\n"
    "1. console.cloud.google.com: create a project, then APIs & Services > Library > enable the Gmail API.\n"
    "2. OAuth consent screen: User type External, add yourself under Test users.\n"
    "3. Credentials > Create credentials > OAuth client ID > Desktop app. Copy the client ID and secret here.\n"
    "Both are stored in Windows Credential Manager. The app can create drafts and send email, "
    "but can't read your inbox."
)


class GmailSettings(QWidget):
    changed = Signal()

    def __init__(self, connection: GmailConnection):
        super().__init__()
        self.connection = connection
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        steps.setStyleSheet("color: palette(placeholder-text);")
        steps.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText("Client ID (….apps.googleusercontent.com)")
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.Password)
        self.client_secret.setPlaceholderText("Client secret")
        self.save_button = QPushButton("Save client")
        self.save_button.clicked.connect(self.save_client)
        client_row = QHBoxLayout()
        client_row.addWidget(self.client_id, 2)
        client_row.addWidget(self.client_secret, 1)
        client_row.addWidget(self.save_button)
        self.client_widget = QWidget()
        self.client_widget.setLayout(client_row)
        client_row.setContentsMargins(0, 0, 0, 0)

        self.connect_button = QPushButton("Connect Gmail")
        self.connect_button.clicked.connect(self.connect_gmail)
        self.disconnect_button = QPushButton("Disconnect Gmail")
        self.disconnect_button.clicked.connect(self.disconnect)
        self.forget_button = QPushButton("Remove OAuth client")
        self.forget_button.clicked.connect(self.forget)
        buttons = QHBoxLayout()
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.disconnect_button)
        buttons.addWidget(self.forget_button)
        buttons.addStretch()
        self.status = QLabel()
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.steps = steps
        layout.addWidget(steps)
        layout.addWidget(self.client_widget)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        self.refresh()

    def refresh(self) -> None:
        has_client, connected = self.connection.has_client, self.connection.is_connected
        self.steps.setVisible(not has_client)
        self.client_widget.setVisible(not has_client)
        self.connect_button.setVisible(has_client and not connected)
        self.connect_button.setEnabled(True)
        self.disconnect_button.setVisible(connected)
        self.forget_button.setVisible(has_client)
        self.status.setText(self.connection.status())

    def save_client(self) -> None:
        try:
            self.connection.save_client(self.client_id.text(), self.client_secret.text())
        except GmailError as e:
            self.status.setText(str(e))
            return
        self.client_id.clear()
        self.client_secret.clear()
        self.refresh()

    def connect_gmail(self) -> None:
        self.connect_button.setEnabled(False)
        self.status.setText("Your browser opened Google sign-in. Choose your account and allow access. "
                            "Google warns that the app isn't verified because it's your own; "
                            "choose Continue.")

        def done(address):
            self.refresh()
            self.status.setText(f"Connected as {address}.")
            self.changed.emit()

        def failed(message):
            self.refresh()
            self.status.setText(f"Couldn't connect Gmail: {message}")

        run_in_background(self, self.connection.connect, done, failed)

    def disconnect(self) -> None:
        self.connection.disconnect()
        self.refresh()
        self.changed.emit()

    def forget(self) -> None:
        self.connection.forget_client()
        self.refresh()
        self.changed.emit()
