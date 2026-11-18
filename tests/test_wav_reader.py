"""The WAV reader the packaged app uses instead of PyAV gives Whisper the same samples."""

import wave

import numpy as np
import pytest

from meeting_assistant.transcribe import read_wav


def write_wav(path, samples: np.ndarray, rate: int, channels: int) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((samples * 32767).astype(np.int16).tobytes())


def tone(rate: int, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


@pytest.mark.parametrize("rate,channels", [(16000, 1), (48000, 2)])
def test_matches_faster_whisper_decoder(tmp_path, rate, channels):
    decode_audio = pytest.importorskip("faster_whisper").decode_audio  # needs PyAV, so dev installs only
    samples = tone(rate)
    if channels == 2:
        samples = np.column_stack([samples, samples]).ravel()
    path = tmp_path / "a.wav"
    write_wav(path, samples, rate, channels)

    ours, theirs = read_wav(path), decode_audio(str(path))
    assert abs(len(ours) - len(theirs)) <= 2
    n = min(len(ours), len(theirs))
    # resampling methods differ slightly; the signal must be the same
    assert np.corrcoef(ours[100:n - 100], theirs[100:n - 100])[0, 1] > 0.99


def test_rejects_other_sample_widths(tmp_path):
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(1)
        f.setframerate(16000)
        f.writeframes(b"\x80" * 100)
    with pytest.raises(ValueError, match="16-bit"):
        read_wav(path)
