"""Small Notion API client: only the calls this app needs, with rate limiting and retries.

Notion allows about 3 requests a second per integration, returns 429 with Retry-After when
exceeded, and accepts at most 100 child blocks per request.
"""

from __future__ import annotations

import time

import httpx

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_BLOCKS = 100


class NotionError(RuntimeError):
    pass


class NotionClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None,
                 min_interval: float = 0.34, retries: int = 3):
        self.client = httpx.Client(
            base_url=API,
            transport=transport,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        self.min_interval = min_interval
        self.retries = retries
        self._last = 0.0

    def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.client.request(method, path, json=json)
            except httpx.HTTPError as e:
                raise NotionError(f"Couldn't reach Notion: {e}") from e
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(float(resp.headers.get("Retry-After", 1 + attempt)))
                    continue
            if resp.status_code >= 400:
                try:
                    message = resp.json().get("message", resp.text)
                except ValueError:
                    message = resp.text
                if resp.status_code == 401:
                    message = "Notion rejected the token. Check it in Settings."
                elif resp.status_code == 404:
                    message += " (share the page or database with your integration in Notion: ••• > Connections)"
                raise NotionError(message)
            return resp.json()
        raise NotionError("Notion is busy; try again in a minute.")

    # calls
    def me(self) -> dict:
        return self._request("GET", "/users/me")

    def search_pages(self, query: str = "") -> list[tuple[str, str]]:
        """(id, title) of pages shared with the integration."""
        body = {"query": query, "filter": {"property": "object", "value": "page"}, "page_size": 50}
        results = self._request("POST", "/search", body)["results"]
        pages = []
        for page in results:
            title = ""
            for prop in page.get("properties", {}).values():
                if prop.get("type") == "title":
                    title = "".join(t.get("plain_text", "") for t in prop["title"])
            pages.append((page["id"], title or "Untitled"))
        return pages

    def create_database(self, parent_page_id: str, title: str, properties: dict) -> str:
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        }
        return self._request("POST", "/databases", body)["id"]

    def get_database(self, database_id: str) -> dict:
        return self._request("GET", f"/databases/{database_id}")

    def query(self, database_id: str, filter: dict | None = None) -> list[dict]:
        body = {"page_size": 100}
        if filter:
            body["filter"] = filter
        return self._request("POST", f"/databases/{database_id}/query", body)["results"]

    def create_page(self, database_id: str, properties: dict, children: list[dict] | None = None) -> dict:
        children = children or []
        body = {"parent": {"database_id": database_id}, "properties": properties,
                "children": children[:MAX_BLOCKS]}
        page = self._request("POST", "/pages", body)
        self.append_blocks(page["id"], children[MAX_BLOCKS:])
        return page

    def append_blocks(self, block_id: str, children: list[dict]) -> None:
        for i in range(0, len(children), MAX_BLOCKS):
            self._request("PATCH", f"/blocks/{block_id}/children", {"children": children[i:i + MAX_BLOCKS]})
