"""Connecting the app to a Gmail account. The OAuth client and the refresh token live in
Credential Manager; the connected address is a preference so the UI can show it offline."""

from __future__ import annotations

from typing import Callable

import httpx

from .. import credentials
from . import oauth
from .client import GmailClient, GmailError

CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN = "gmail_client_id", "gmail_client_secret", "gmail_refresh_token"
ADDRESS = "gmail_address"


class GmailConnection:
    def __init__(self, store, secrets=credentials, http: httpx.Client | None = None,
                 sign_in: Callable[..., oauth.Tokens] = oauth.sign_in,
                 transport: httpx.BaseTransport | None = None):
        self.store = store
        self.secrets = secrets
        self.http = http or httpx.Client(timeout=30)
        self._sign_in = sign_in
        self.transport = transport
        self._client: GmailClient | None = None

    @property
    def has_client(self) -> bool:
        return bool(self.secrets.get_secret(CLIENT_ID) and self.secrets.get_secret(CLIENT_SECRET))

    @property
    def is_connected(self) -> bool:
        return bool(self.secrets.get_secret(REFRESH_TOKEN))

    @property
    def address(self) -> str:
        return self.store.get_pref(ADDRESS)

    def save_client(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise GmailError("That doesn't look like a Google OAuth client ID "
                             "(it ends with .apps.googleusercontent.com).")
        if not client_secret:
            raise GmailError("Paste the client secret too.")
        self.secrets.set_secret(CLIENT_ID, client_id)
        self.secrets.set_secret(CLIENT_SECRET, client_secret)

    def connect(self) -> str:
        """Opens Google sign-in in the browser and waits for it. Returns the connected address."""
        if not self.has_client:
            raise GmailError("Save the OAuth client ID and secret first.")
        try:
            tokens = self._sign_in(self.secrets.get_secret(CLIENT_ID), self.secrets.get_secret(CLIENT_SECRET),
                                   http=self.http)
        except oauth.OAuthError as e:
            raise GmailError(str(e)) from e
        self.secrets.set_secret(REFRESH_TOKEN, tokens.refresh_token)
        self._client = GmailClient(self._fresh_tokens, tokens=tokens, transport=self.transport)
        address = self._client.email_address()
        self.store.set_pref(ADDRESS, address)
        return address

    def _fresh_tokens(self) -> oauth.Tokens:
        refresh_token = self.secrets.get_secret(REFRESH_TOKEN)
        if not refresh_token:
            raise oauth.OAuthError("Gmail isn't connected. Connect it in Settings.")
        return oauth.refresh(self.http, self.secrets.get_secret(CLIENT_ID), self.secrets.get_secret(CLIENT_SECRET),
                             refresh_token)

    def client(self) -> GmailClient:
        if not self.is_connected:
            raise GmailError("Gmail isn't connected. Connect it in Settings.")
        if self._client is None:
            self._client = GmailClient(self._fresh_tokens, transport=self.transport)
        return self._client

    def disconnect(self) -> None:
        token = self.secrets.get_secret(REFRESH_TOKEN)
        if token:
            oauth.revoke(self.http, token)
        self.secrets.delete_secret(REFRESH_TOKEN)
        self.store.set_pref(ADDRESS, "")
        self._client = None

    def forget_client(self) -> None:
        self.disconnect()
        self.secrets.delete_secret(CLIENT_ID)
        self.secrets.delete_secret(CLIENT_SECRET)

    def status(self) -> str:
        if self.is_connected:
            return f"Connected as {self.address or 'your Gmail account'}. Drafts go to your Gmail Drafts folder."
        if self.has_client:
            return "OAuth client saved. Press “Connect Gmail” and sign in with Google in your browser."
        return "Not connected."
