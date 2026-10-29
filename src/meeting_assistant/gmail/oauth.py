"""Google sign-in for a desktop app: OAuth 2.0 with PKCE and a loopback redirect.

The app opens Google's consent page in the browser. Google redirects to a one-shot server on
127.0.0.1, which catches the code; the app swaps it for tokens. The user's password never
passes through this app, and only the refresh token is kept (in Credential Manager).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# create drafts and send them; no permission to read the inbox
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

DONE_PAGE = (b"<html><body style='font-family:sans-serif;padding:40px'>"
             b"<h2>Gmail is connected.</h2><p>You can close this tab and go back to Meeting Assistant.</p>"
             b"</body></html>")


class OAuthError(RuntimeError):
    pass


@dataclass
class Tokens:
    access_token: str
    expires_at: float
    refresh_token: str | None = None


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",  # ask for a refresh token
        "prompt": "consent",
    })


def _token_request(http: httpx.Client, data: dict) -> Tokens:
    try:
        resp = http.post(TOKEN_URL, data=data)
    except httpx.HTTPError as e:
        raise OAuthError(f"Couldn't reach Google: {e}") from e
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code >= 400:
        error = body.get("error", resp.status_code)
        if error == "invalid_grant":
            raise OAuthError("Google sign-in expired or was revoked. Connect Gmail again in Settings.")
        if error == "invalid_client":
            raise OAuthError("Google rejected the client ID or secret. Check them in Settings.")
        raise OAuthError(f"Google sign-in failed: {body.get('error_description') or error}")
    return Tokens(body["access_token"], time.time() + int(body.get("expires_in", 3600)) - 60,
                  body.get("refresh_token"))


def refresh(http: httpx.Client, client_id: str, client_secret: str, refresh_token: str) -> Tokens:
    return _token_request(http, {"client_id": client_id, "client_secret": client_secret,
                                 "refresh_token": refresh_token, "grant_type": "refresh_token"})


def revoke(http: httpx.Client, token: str) -> None:
    try:
        http.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        pass  # disconnecting locally still works when offline


def sign_in(client_id: str, client_secret: str, http: httpx.Client | None = None,
            open_browser: Callable[[str], object] = webbrowser.open, timeout: float = 300) -> Tokens:
    """Run the whole browser sign-in. Blocks until Google redirects back or `timeout` passes."""
    http = http or httpx.Client(timeout=30)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if "code" not in query and "error" not in query:
                self.send_response(404)  # e.g. the browser asking for favicon.ico
                self.end_headers()
                return
            result.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DONE_PAGE if "code" in query else b"Sign-in was cancelled. You can close this tab.")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    try:
        open_browser(auth_url(client_id, redirect_uri, challenge, state))
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if not result:
        raise OAuthError("Timed out waiting for Google sign-in.")
    if "error" in result:
        raise OAuthError("Sign-in was cancelled." if result["error"] == "access_denied"
                         else f"Google sign-in failed: {result['error']}")
    if result.get("state") != state:
        raise OAuthError("Sign-in response didn't match this request. Try again.")
    tokens = _token_request(http, {
        "client_id": client_id, "client_secret": client_secret, "code": result["code"],
        "code_verifier": verifier, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if not tokens.refresh_token:
        raise OAuthError("Google didn't return a refresh token. Remove the app's access at "
                         "myaccount.google.com/permissions and connect again.")
    return tokens
