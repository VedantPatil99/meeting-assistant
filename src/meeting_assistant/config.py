"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def default_meetings_dir() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "MeetingAssistant"


@dataclass(frozen=True)
class Settings:
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    # optional lighter model for the bulk per-chunk extraction calls; empty = use ollama_model
    extract_model: str = ""
    embed_model: str = "nomic-embed-text"  # for searching across meetings
    ollama_num_ctx: int = 8192
    whisper_model: str = "small"
    whisper_device: str = "auto"
    meetings_dir: Path = field(default_factory=default_meetings_dir)

    @classmethod
    def from_env(cls) -> "Settings":
        if getattr(sys, "frozen", False):  # the packaged app: a .env next to the .exe
            load_dotenv(Path(sys.executable).parent / ".env")
        else:
            load_dotenv()
        return cls(
            ollama_host=os.getenv("OLLAMA_HOST", cls.ollama_host),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            extract_model=os.getenv("OLLAMA_EXTRACT_MODEL", cls.extract_model),
            embed_model=os.getenv("OLLAMA_EMBED_MODEL", cls.embed_model),
            ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", cls.ollama_num_ctx)),
            whisper_model=os.getenv("WHISPER_MODEL", cls.whisper_model),
            whisper_device=os.getenv("WHISPER_DEVICE", cls.whisper_device),
            meetings_dir=Path(os.getenv("MEETINGS_DIR") or default_meetings_dir()),
        )
