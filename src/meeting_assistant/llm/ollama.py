"""Local LLM via Ollama's HTTP API, using JSON-schema constrained output."""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from ..config import Settings
from .base import LLMError, T


class OllamaProvider:
    def __init__(self, settings: Settings, timeout: float = 600.0, retries: int = 1):
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self.num_ctx = settings.ollama_num_ctx
        self.retries = retries
        self.client = httpx.Client(timeout=timeout)
        self.name = f"ollama:{self.model}"

    def complete_json(self, system: str, prompt: str, schema: type[T]) -> T:
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                resp = self.client.post(f"{self.host}/api/chat", json=payload)
                resp.raise_for_status()
            except httpx.ConnectError as e:
                raise LLMError(
                    f"Cannot reach Ollama at {self.host}. Is the Ollama app running?"
                ) from e
            except httpx.HTTPStatusError as e:
                raise LLMError(f"Ollama error {e.response.status_code}: {e.response.text}") from e
            content = resp.json()["message"]["content"]
            try:
                return schema.model_validate_json(content)
            except ValidationError as e:
                last_error = e
        raise LLMError(f"Model output did not match {schema.__name__}: {last_error}")

    def is_available(self) -> bool:
        try:
            tags = self.client.get(f"{self.host}/api/tags").json()
        except httpx.HTTPError:
            return False
        return any(m.get("name", "").startswith(self.model) for m in tags.get("models", []))
