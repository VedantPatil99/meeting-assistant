"""Play a meeting's recording from any moment, so a summary line can be checked against what
was actually said.

A meeting has two tracks (your mic and the call audio), both padded with silence against the
wall clock, so the same second in each file is the same moment. TrackMixer reads both from a
start time and adds them together; Player streams that to the speakers with PortAudio, the
library already used for recording (Qt's media module would add FFmpeg back to the build).
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np


class TrackMixer:
    """Reads 16-bit mono WAV tracks from `start` seconds and mixes them into one stream at the
    highest of their sample rates."""

    def __init__(self, paths: list[Path], start: float = 0.0):
        self.files = [wave.open(str(p), "rb") for p in paths if p.exists()]
        for f in self.files:
            if f.getsampwidth() != 2 or f.getnchannels() != 1:
                self.close()
                raise ValueError("only 16-bit mono recordings can be played")
        self.rate = max((f.getframerate() for f in self.files), default=16000)
        self.duration = max((f.getnframes() / f.getframerate() for f in self.files), default=0.0)
        self.position = min(max(start, 0.0), self.duration)
        for f in self.files:
            f.setpos(min(int(self.position * f.getframerate()), f.getnframes()))

    def read(self, frames: int) -> np.ndarray:
        """The next `frames` samples as int16; shorter only at the end of the recording."""
        frames = min(frames, max(int(round((self.duration - self.position) * self.rate)), 0))
        mix = np.zeros(frames, dtype=np.float32)
        for f in self.files:
            rate = f.getframerate()
            want = int(round(frames * rate / self.rate))
            samples = np.frombuffer(f.readframes(want), dtype=np.int16).astype(np.float32)
            if rate != self.rate and len(samples):  # resample to the output rate
                samples = np.interp(np.linspace(0, len(samples), frames, endpoint=False),
                                    np.arange(len(samples)), samples)
            mix[:min(len(samples), frames)] += samples[:frames]
        self.position += frames / self.rate
        return np.clip(mix, -32768, 32767).astype(np.int16)

    def close(self) -> None:
        for f in self.files:
            f.close()
        self.files = []


class Player:
    """Plays mixed tracks on the default speakers. One recording at a time."""

    def __init__(self):
        self._pa = None
        self._stream = None
        self._mixer: TrackMixer | None = None
        self._lock = threading.Lock()

    @property
    def playing(self) -> bool:
        return self._stream is not None and self._stream.is_active()

    @property
    def position(self) -> float:
        return self._mixer.position if self._mixer else 0.0

    def play(self, paths: list[Path], start: float) -> None:
        import pyaudiowpatch as pyaudio  # Windows-only; imported when first used

        self.stop()
        mixer = TrackMixer(paths, start)
        if not mixer.files:
            raise FileNotFoundError("This meeting has no recording to play.")
        if self._pa is None:
            self._pa = pyaudio.PyAudio()

        def callback(in_data, frame_count, time_info, status):
            with self._lock:
                data = mixer.read(frame_count)
            done = len(data) < frame_count
            if done:
                data = np.concatenate([data, np.zeros(frame_count - len(data), dtype=np.int16)])
            return data.tobytes(), pyaudio.paComplete if done else pyaudio.paContinue

        self._mixer = mixer
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=mixer.rate, output=True,
                                     frames_per_buffer=int(mixer.rate * 0.05), stream_callback=callback)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._mixer is not None:
            with self._lock:
                self._mixer.close()
        # keep the last position so the caller can show where playback stopped

    def shutdown(self) -> None:
        self.stop()
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
