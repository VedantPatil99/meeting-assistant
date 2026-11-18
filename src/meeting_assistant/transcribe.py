"""Local speech-to-text with faster-whisper (free, runs on the GPU when available)."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

from .config import Settings
from .models import Segment, Transcript
from .transcript import merge_transcripts


SPEECH_RATE = 16000  # what the speech model expects


def read_wav(path: Path):
    """Reads a 16-bit WAV (what the recorder writes) into the float32 mono 16 kHz samples
    Whisper wants. faster-whisper could decode it with PyAV (FFmpeg), but that library is
    67 MB, so the packaged app leaves it out and uses this instead."""
    import numpy as np  # installed with faster-whisper

    with wave.open(str(path), "rb") as f:
        if f.getsampwidth() != 2:
            raise ValueError(f"{path.name}: only 16-bit WAV is supported")
        channels, rate = f.getnchannels(), f.getframerate()
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SPEECH_RATE and len(samples):
        duration = len(samples) / rate
        samples = np.interp(np.arange(0, duration, 1 / SPEECH_RATE), np.arange(len(samples)) / rate,
                            samples).astype(np.float32)
    return samples


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
        path = Path(audio_path)
        audio = read_wav(path) if path.suffix.lower() == ".wav" else str(path)  # other formats need PyAV
        segments, _info = self.model.transcribe(audio, vad_filter=True)
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
