"""Small Gmail API client: create, update and send drafts. Refreshes the access token itself."""

from __future__ import annotations

import base64
import time
from email.message import EmailMessage
from typing import Callable

import httpx

from .oauth import OAuthError, Tokens

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailError(RuntimeError):
    pass


def build_message(to: list[str], subject: str, body: str, sender: str = "") -> str:
    """The base64url-encoded RFC 2822 message Gmail expects in `raw`."""
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    if to:
        msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(bytes(msg)).decode()


class GmailClient:
    def __init__(self, get_tokens: Callable[[], Tokens], tokens: Tokens | None = None,
                 transport: httpx.BaseTransport | None = None, retries: int = 2):
        """`get_tokens` gets a new access token when the current one (`tokens`) has expired."""
        self.get_tokens = get_tokens
        self.client = httpx.Client(base_url=API, transport=transport, timeout=30)
        self.retries = retries
        self._tokens = tokens

    def _token(self, force: bool = False) -> str:
        if force or self._tokens is None or self._tokens.expires_at <= time.time():
            try:
                self._tokens = self.get_tokens()
            except OAuthError as e:
                raise GmailError(str(e)) from e
        return self._tokens.access_token

    def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        refreshed = False
        for attempt in range(self.retries + 1):
            headers = {"Authorization": f"Bearer {self._token()}"}
            try:
                resp = self.client.request(method, path, json=json, headers=headers)
            except httpx.HTTPError as e:
                raise GmailError(f"Couldn't reach Gmail: {e}") from e
            if resp.status_code == 401 and not refreshed:
                self._token(force=True)  # access token expired early or was revoked
                refreshed = True
                continue
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.retries:
                time.sleep(float(resp.headers.get("Retry-After", 1 + attempt)))
                continue
            if resp.status_code >= 400:
                try:
                    message = resp.json()["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    message = resp.text
                if resp.status_code in (401, 403):
                    message = f"Gmail refused the request ({message}). Try connecting Gmail again in Settings."
                raise GmailError(message)
            return resp.json() if resp.content else {}
        raise GmailError("Gmail is busy. Try again in a minute.")

    def email_address(self) -> str:
        return self._request("GET", "/profile")["emailAddress"]

    def create_draft(self, to: list[str], subject: str, body: str) -> str:
        """Saves the email in the user's Gmail Drafts. Returns the draft id."""
        return self._request("POST", "/drafts", {"message": {"raw": build_message(to, subject, body)}})["id"]

    def update_draft(self, draft_id: str, to: list[str], subject: str, body: str) -> str:
        return self._request("PUT", f"/drafts/{draft_id}",
                             {"id": draft_id, "message": {"raw": build_message(to, subject, body)}})["id"]

    def send_draft(self, draft_id: str) -> str:
        return self._request("POST", "/drafts/send", {"id": draft_id})["id"]

    def send(self, to: list[str], subject: str, body: str) -> str:
        if not to:
            raise GmailError("Add at least one recipient before sending.")
        return self._request("POST", "/messages/send", {"raw": build_message(to, subject, body)})["id"]
