"""Playing a meeting from a clicked timestamp: mixing the two tracks, and the links that seek."""


import numpy as np
import pytest

from meeting_assistant.playback import TrackMixer

from .test_wav_reader import write_wav


def constant(path, value: float, rate: int, seconds: float):
    write_wav(path, np.full(int(rate * seconds), value, dtype=np.float32), rate, 1)


def test_mixes_both_tracks_from_the_start_time(tmp_path):
    constant(tmp_path / "mic.wav", 0.25, 16000, 4)
    constant(tmp_path / "system.wav", 0.25, 48000, 4)  # a different rate is resampled
    mixer = TrackMixer([tmp_path / "mic.wav", tmp_path / "system.wav"], start=1.0)
    assert mixer.rate == 48000
    out = mixer.read(4800)
    assert len(out) == 4800
    assert abs(int(out[100]) - int(0.5 * 32767)) < 20      # the two tracks added together
    assert mixer.position == pytest.approx(1.1)


def test_stops_at_the_end_and_skips_missing_tracks(tmp_path):
    constant(tmp_path / "mic.wav", 0.1, 16000, 2)
    mixer = TrackMixer([tmp_path / "mic.wav", tmp_path / "missing.wav"], start=1.5)
    assert len(mixer.read(16000)) == 8000  # half a second left
    assert len(mixer.read(16000)) == 0


def test_start_past_the_end_plays_nothing(tmp_path):
    constant(tmp_path / "mic.wav", 0.1, 16000, 1)
    assert len(TrackMixer([tmp_path / "mic.wav"], start=99).read(1000)) == 0


def test_rejects_stereo(tmp_path):
    path = tmp_path / "stereo.wav"
    write_wav(path, np.zeros(200, dtype=np.float32), 16000, 2)
    with pytest.raises(ValueError, match="mono"):
        TrackMixer([path])


def test_report_timestamps_become_seek_links():
    pytest.importorskip("PySide6")
    from meeting_assistant.app.library_page import link_timestamps

    assert link_timestamps("- Go-live 20 Dec `[00:02:15]`") == "- Go-live 20 Dec [▶ 00:02:15](seek:135)"


def test_transcript_lines_get_anchors_and_start_times():
    pytest.importorskip("PySide6")
    from meeting_assistant.app.library_page import transcript_html

    body, starts, blocks = transcript_html("[00:00:05] Rahul: Hello <team>\nwrapped line\n[00:01:10] Back in a sec")
    assert starts == [5, 70] and blocks == [0, 2]
    assert "<a name='t5' href='seek:5'>00:00:05</a>" in body
    assert "<b>Rahul:</b> Hello &lt;team&gt;" in body
