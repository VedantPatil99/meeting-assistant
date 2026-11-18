"""What the app needs on a new computer, and getting it: Ollama running, the AI model pulled,
and the speech-to-text model downloaded. Used by the first-run setup screen.

Ollama is a separate free program (ollama.com), so it can't ship inside the app. Everything
else is downloaded through it or from Hugging Face, once.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

OLLAMA_DOWNLOAD = "https://ollama.com/download/windows"
MODEL_CHOICES = {
    "qwen2.5:7b": "Best results. 4.7 GB download; needs about 8 GB of free memory.",
    "qwen2.5:3b": "For lighter laptops. 1.9 GB download; summaries are less accurate.",
}


@dataclass
class OllamaState:
    running: bool
    installed: bool
    models: list[str]

    def has(self, model: str) -> bool:
        return any(m == model or m.startswith(model + "-") for m in self.models)


def find_ollama() -> Path | None:
    """The ollama.exe the official installer puts in the user's AppData, or one on PATH."""
    found = shutil.which("ollama")
    if found:
        return Path(found)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return local if local.exists() else None


def ollama_state(host: str, client: httpx.Client | None = None) -> OllamaState:
    client = client or httpx.Client(timeout=3)
    try:
        tags = client.get(f"{host.rstrip('/')}/api/tags").json()
    except (httpx.HTTPError, ValueError):
        return OllamaState(running=False, installed=find_ollama() is not None, models=[])
    return OllamaState(running=True, installed=True, models=[m.get("name", "") for m in tags.get("models", [])])


def start_ollama(exe: Path) -> None:
    """Starts the Ollama server in the background, with no console window."""
    subprocess.Popen([str(exe), "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pull_model(host: str, model: str, report: Callable[[tuple[str, int, int]], None],
               client: httpx.Client | None = None) -> None:
    """Downloads `model` through Ollama, reporting (status, bytes done, bytes total) as it goes.
    Ollama streams one JSON object per line; a download resumes if it was interrupted."""
    client = client or httpx.Client(timeout=None)
    with client.stream("POST", f"{host.rstrip('/')}/api/pull", json={"model": model, "stream": True}) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            if "error" in event:
                raise RuntimeError(f"Ollama couldn't download {model}: {event['error']}")
            report((event.get("status", ""), int(event.get("completed") or 0), int(event.get("total") or 0)))


def whisper_ready(name: str) -> bool:
    from faster_whisper.utils import download_model

    try:
        download_model(name, local_files_only=True)
        return True
    except Exception:  # not in the local cache yet
        return False


def download_whisper(name: str) -> None:
    from faster_whisper.utils import download_model

    download_model(name)
