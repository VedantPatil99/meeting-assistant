"""Settings section for connecting Notion: paste the token, pick a page, create the databases."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..notion import NotionError
from ..notion.connection import NotionConnection

STEPS = (
    "1. Go to notion.so/my-integrations, create an internal integration and copy its secret.\n"
    "2. In Notion, open the page that should hold your meetings, then ••• > Connections > add the integration.\n"
    "3. Paste the secret here and press Connect. It's stored in Windows Credential Manager, not in a file."
)


class NotionSettings(QWidget):
    def __init__(self, connection: NotionConnection):
        super().__init__()
        self.connection = connection
        self.status = QLabel()
        self.status.setWordWrap(True)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        steps.setStyleSheet("color: palette(placeholder-text);")
        steps.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Paste the integration secret")
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_token)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token, 1)
        token_row.addWidget(self.connect_button)

        self.pages = QComboBox()
        self.refresh_pages = QPushButton("Find pages")
        self.refresh_pages.clicked.connect(self.load_pages)
        self.create_button = QPushButton("Create Meetings and Tasks databases here")
        self.create_button.clicked.connect(self.create_databases)
        page_row = QHBoxLayout()
        page_row.addWidget(self.pages, 1)
        page_row.addWidget(self.refresh_pages)
        page_row.addWidget(self.create_button)

        self.disconnect_button = QPushButton("Disconnect Notion")
        self.disconnect_button.clicked.connect(self.disconnect)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(steps)
        layout.addLayout(token_row)
        layout.addLayout(page_row)
        layout.addWidget(self.status)
        layout.addWidget(self.disconnect_button, 0, Qt.AlignLeft)
        self.refresh()

    def refresh(self) -> None:
        has_token = self.connection.has_token
        self.status.setText(self.connection.status())
        for w in (self.pages, self.refresh_pages, self.create_button):
            w.setEnabled(has_token)
        self.disconnect_button.setVisible(has_token)

    def _busy(self, message: str) -> None:
        self.status.setText(message)
        QApplication.processEvents()

    def connect_token(self) -> None:
        token = self.token.text().strip()
        if not token:
            return
        self._busy("Checking the token with Notion…")
        try:
            name = self.connection.connect(token)
        except NotionError as e:
            self.status.setText(f"Couldn't connect: {e}")
            return
        self.token.clear()
        self.refresh()
        self.status.setText(f"Connected as “{name}”. Now pick the page you shared with it.")
        self.load_pages()

    def load_pages(self) -> None:
        self._busy("Looking for pages shared with the integration…")
        try:
            pages = self.connection.pages()
        except NotionError as e:
            self.status.setText(f"Couldn't list pages: {e}")
            return
        self.pages.clear()
        for page_id, title in pages:
            self.pages.addItem(title, page_id)
        if not pages:
            self.status.setText("No pages found. In Notion, share a page with the integration (••• > Connections).")
        else:
            self.refresh()

    def create_databases(self) -> None:
        if self.pages.currentData() is None:
            self.status.setText("Pick a page first. Press “Find pages” if the list is empty.")
            return
        self._busy("Creating the Meetings and Tasks databases…")
        try:
            self.connection.create_databases(self.pages.currentData(), self.pages.currentText())
        except NotionError as e:
            self.status.setText(f"Couldn't create the databases: {e}")
            return
        self.refresh()

    def disconnect(self) -> None:
        self.connection.disconnect()
        self.pages.clear()
        self.refresh()
