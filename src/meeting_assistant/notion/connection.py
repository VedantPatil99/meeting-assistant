"""Connecting the app to a Notion workspace: token in Credential Manager, database ids in prefs."""

from __future__ import annotations

from typing import Callable

from .. import credentials
from .client import NotionClient
from .sync import NotionSync

TOKEN = "notion"
MEETINGS_DB, TASKS_DB, PARENT_TITLE = "notion_meetings_db", "notion_tasks_db", "notion_parent_title"


class NotionConnection:
    def __init__(self, store, client_factory: Callable[[str], NotionClient] = NotionClient,
                 secrets=credentials):
        self.store = store
        self.client_factory = client_factory
        self.secrets = secrets
        self._client: NotionClient | None = None
        self._client_token: str | None = None

    def _client_for(self, token: str) -> NotionClient:
        if self._client is None or self._client_token != token:
            self._client, self._client_token = self.client_factory(token), token
        return self._client

    def client(self) -> NotionClient | None:
        token = self.secrets.get_secret(TOKEN)
        return self._client_for(token) if token else None

    @property
    def has_token(self) -> bool:
        return bool(self.secrets.get_secret(TOKEN))

    @property
    def is_ready(self) -> bool:
        return self.has_token and bool(self.store.get_pref(MEETINGS_DB)) and bool(self.store.get_pref(TASKS_DB))

    def sync(self) -> NotionSync | None:
        """What automations use. None until a token and both databases are set up."""
        if not self.is_ready:
            return None
        return NotionSync(self.client(), self.store.get_pref(MEETINGS_DB), self.store.get_pref(TASKS_DB))

    def connect(self, token: str) -> str:
        """Check the token with Notion before saving it. Returns the integration's name."""
        me = self._client_for(token.strip()).me()
        self.secrets.set_secret(TOKEN, token.strip())
        return me.get("name") or "your integration"

    def pages(self) -> list[tuple[str, str]]:
        return self.client().search_pages()

    def create_databases(self, parent_id: str, parent_title: str) -> None:
        meetings, tasks = NotionSync.setup(self.client(), parent_id)
        self.store.set_pref(MEETINGS_DB, meetings)
        self.store.set_pref(TASKS_DB, tasks)
        self.store.set_pref(PARENT_TITLE, parent_title)

    def disconnect(self) -> None:
        self.secrets.delete_secret(TOKEN)
        for key in (MEETINGS_DB, TASKS_DB, PARENT_TITLE):
            self.store.set_pref(key, "")
        self._client = self._client_token = None

    def status(self) -> str:
        if not self.has_token:
            return "Not connected."
        if not self.is_ready:
            return "Token saved. Choose a page below and create the databases."
        return f"Connected. Meetings and Tasks databases are in “{self.store.get_pref(PARENT_TITLE)}”."
