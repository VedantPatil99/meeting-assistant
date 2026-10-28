"""Record the microphone and the computer's sound (other people in the call) as two WAV tracks.

System sound comes from WASAPI loopback. Loopback delivers no audio at all while
nothing is playing, so each track is padded with silence against the wall clock:
both files stay exactly as long as the meeting and timestamps line up.
"""

from __future__ import annotations

import array
import math
import threading
import time
import wave
from pathlib import Path

SAMPLE_WIDTH = 2  # 16-bit PCM
SPEECH_RATE = 16000  # what the speech model works at


class TrackWriter:
    """Writes one mono track, filling gaps so the file length matches elapsed time.

    Devices are opened in their native format (WASAPI shared mode requires it; this
    laptop's mic array has 4 channels at 48 kHz). Audio is averaged down to mono and,
    when the rate is a multiple of 16 kHz, to 16 kHz: about 115 MB an hour instead of 1.4 GB.
    """

    def __init__(self, path: Path, rate: int, channels: int, start: float, tolerance: float = 0.1):
        self.in_channels = channels
        self.factor = rate // SPEECH_RATE if rate % SPEECH_RATE == 0 else 1
        self.rate = rate // self.factor
        self.start = start
        self.tolerance_frames = int(self.rate * tolerance)
        self.frames_written = 0
        self.level = 0.0
        self._group = self.factor * channels  # input samples per output sample
        self._carry = array.array("h")
        self._lock = threading.Lock()
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(self.rate)

    def _convert(self, data: bytes) -> array.array:
        samples = self._carry + array.array("h", data)
        usable = len(samples) - len(samples) % self._group
        self._carry = samples[usable:]
        g = self._group
        if g == 1:
            return samples[:usable]
        return array.array("h", (sum(samples[i:i + g]) // g for i in range(0, usable, g)))

    def write(self, data: bytes, now: float) -> None:
        with self._lock:
            mono = self._convert(data)
            frames = len(mono)
            expected_end = int((now - self.start) * self.rate)
            gap = expected_end - frames - self.frames_written
            if gap > self.tolerance_frames:
                self._wav.writeframes(b"\x00" * gap * SAMPLE_WIDTH)
                self.frames_written += gap
            self._wav.writeframes(mono.tobytes())
            self.frames_written += frames
        if mono:
            self.level = min(1.0, math.sqrt(sum(s * s for s in mono) / len(mono)) / 8000)

    def close(self, now: float) -> None:
        with self._lock:
            missing = int((now - self.start) * self.rate) - self.frames_written
            if missing > 0:
                self._wav.writeframes(b"\x00" * missing * SAMPLE_WIDTH)
                self.frames_written += missing
            self._wav.close()

    @property
    def seconds(self) -> float:
        return self.frames_written / self.rate


class Recorder:
    def __init__(self) -> None:
        self._pa = None
        self._streams: list = []
        self.tracks: dict[str, TrackWriter] = {}
        self.started_at: float | None = None

    @property
    def is_recording(self) -> bool:
        return self.started_at is not None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.started_at else 0.0

    def level(self, name: str) -> float:
        track = self.tracks.get(name)
        return track.level if track else 0.0

    def start(self, folder: Path) -> None:
        import pyaudiowpatch as pa  # optional dependency, Windows only

        self._pa = pa.PyAudio()
        wasapi = self._pa.get_host_api_info_by_type(pa.paWASAPI)
        mic = self._pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        loopback = self._pa.get_default_wasapi_loopback()

        self.started_at = time.monotonic()
        try:
            for name, device in (("mic", mic), ("system", loopback)):
                rate = int(device["defaultSampleRate"])
                channels = int(device["maxInputChannels"])
                writer = TrackWriter(folder / f"{name}.wav", rate, channels, self.started_at)
                self.tracks[name] = writer
                stream = self._pa.open(
                    format=pa.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=device["index"],
                    frames_per_buffer=int(rate * 0.05),
                    stream_callback=self._callback(writer, pa.paContinue),
                )
                self._streams.append(stream)
        except Exception:
            self.stop()
            raise

    @staticmethod
    def _callback(writer: TrackWriter, keep_going):
        def callback(in_data, frame_count, time_info, status):
            writer.write(in_data, time.monotonic())
            return (None, keep_going)

        return callback

    def stop(self) -> float:
        """Stop recording and return the meeting length in seconds."""
        now = time.monotonic()
        for stream in self._streams:
            stream.stop_stream()
            stream.close()
        for writer in self.tracks.values():
            writer.close(now)
        if self._pa:
            self._pa.terminate()
        duration = now - (self.started_at or now)
        self._pa, self._streams, self.tracks, self.started_at = None, [], {}, None
        return duration
