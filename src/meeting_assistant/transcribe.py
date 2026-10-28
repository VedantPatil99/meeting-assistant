"""Local speech-to-text with faster-whisper (free, runs on the GPU when available)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .config import Settings
from .models import Segment, Transcript
from .transcript import merge_transcripts


def load_model(settings: Settings, device: str | None = None):
    from faster_whisper import WhisperModel  # optional dependency

    device = device or settings.whisper_device
    if device in ("auto", "cuda"):
        try:
            return WhisperModel(settings.whisper_model, device="cuda", compute_type="float16")
        except Exception:
            if device == "cuda":
                raise
    return WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")


class Transcriber:
    """Loads the model once and falls back to the CPU if the GPU libraries are missing.

    faster-whisper only touches CUDA when audio is decoded, so a missing cuBLAS/cuDNN
    shows up as an error on the first transcription, not when the model loads.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = load_model(settings)

    def transcribe(
        self,
        audio_path: str | Path,
        speaker: str | None = None,
        on_progress: Callable[[float], None] = lambda seconds: None,
    ) -> Transcript:
        try:
            return self._run(audio_path, speaker, on_progress)
        except RuntimeError:
            if self.settings.whisper_device == "cuda":
                raise
            self.model = load_model(self.settings, device="cpu")
            return self._run(audio_path, speaker, on_progress)

    def _run(self, audio_path, speaker, on_progress) -> Transcript:
        segments, _info = self.model.transcribe(str(audio_path), vad_filter=True)
        out: list[Segment] = []
        for seg in segments:
            out.append(Segment(start=seg.start, end=seg.end, speaker=speaker, text=seg.text.strip()))
            on_progress(seg.end)
        return Transcript(segments=out)


def transcribe_file(
    audio_path: str | Path,
    settings: Settings,
    speaker: str | None = None,
    on_progress: Callable[[float], None] = lambda seconds: None,
) -> Transcript:
    """Transcribe one audio file. `speaker` labels every segment (e.g. "Me" for the mic track)."""
    return Transcriber(settings).transcribe(audio_path, speaker, on_progress)


def transcribe_tracks(
    tracks: list[tuple[Path, str]],
    settings: Settings,
    on_progress: Callable[[str, float], None] = lambda speaker, seconds: None,
) -> Transcript:
    """Transcribe several aligned tracks (e.g. mic as "Me", system audio as "Others") into one transcript."""
    transcriber = Transcriber(settings)
    parts = [
        transcriber.transcribe(path, speaker, lambda s, sp=speaker: on_progress(sp, s))
        for path, speaker in tracks
        if path.exists()
    ]
    return merge_transcripts(parts)
