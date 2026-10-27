"""Local speech-to-text with faster-whisper (free, runs on the GPU when available)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .config import Settings
from .models import Segment, Transcript


def load_model(settings: Settings):
    from faster_whisper import WhisperModel  # optional dependency

    if settings.whisper_device in ("auto", "cuda"):
        try:
            return WhisperModel(settings.whisper_model, device="cuda", compute_type="float16")
        except Exception:
            if settings.whisper_device == "cuda":
                raise
    return WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")


def transcribe_file(
    audio_path: str | Path,
    settings: Settings,
    speaker: str | None = None,
    on_progress: Callable[[float], None] = lambda seconds: None,
) -> Transcript:
    """Transcribe one audio file. `speaker` labels every segment (e.g. "Me" for the mic track)."""
    model = load_model(settings)
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    out: list[Segment] = []
    for seg in segments:
        out.append(Segment(start=seg.start, end=seg.end, speaker=speaker, text=seg.text.strip()))
        on_progress(seg.end)
    return Transcript(segments=out)
