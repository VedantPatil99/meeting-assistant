"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_num_ctx: int = 8192
    whisper_model: str = "small"
    whisper_device: str = "auto"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            ollama_host=os.getenv("OLLAMA_HOST", cls.ollama_host),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", cls.ollama_num_ctx)),
            whisper_model=os.getenv("WHISPER_MODEL", cls.whisper_model),
            whisper_device=os.getenv("WHISPER_DEVICE", cls.whisper_device),
        )
