import wave
from datetime import datetime

from meeting_assistant.models import Segment, Transcript
from meeting_assistant.recorder import TrackWriter
from meeting_assistant.session import MeetingSession
from meeting_assistant.transcript import merge_transcripts, parse_transcript

RATE = 1000  # small rate keeps the arithmetic readable


def frames(path):
    with wave.open(str(path)) as w:
        return w.getnframes()


def chunk(n_frames, value=1000):
    return value.to_bytes(2, "little", signed=True) * n_frames


def test_track_writer_pads_silence_when_loopback_goes_quiet(tmp_path):
    writer = TrackWriter(tmp_path / "system.wav", RATE, channels=1, start=0.0)
    writer.write(chunk(100), now=0.1)   # 0.0-0.1 s of audio
    writer.write(chunk(100), now=2.1)   # nothing for 1.9 s, then 0.1 s more
    writer.close(now=3.0)               # silence until the meeting ends
    assert frames(tmp_path / "system.wav") == 3 * RATE


def test_track_writer_ignores_small_jitter(tmp_path):
    writer = TrackWriter(tmp_path / "mic.wav", RATE, channels=1, start=0.0)
    for i in range(1, 11):
        writer.write(chunk(100), now=i * 0.1 + 0.05)  # callbacks arrive 50 ms late
    assert writer.frames_written == 1000


def test_track_writer_reports_a_level(tmp_path):
    writer = TrackWriter(tmp_path / "mic.wav", RATE, channels=1, start=0.0)
    writer.write(chunk(100, value=0), now=0.1)
    assert writer.level == 0
    writer.write(chunk(100, value=4000), now=0.2)
    assert 0.4 < writer.level <= 1.0


def test_track_writer_downmixes_to_16k_mono(tmp_path):
    # 4-channel 48 kHz mic, like this laptop's: 0.1 s in, 1600 mono 16 kHz frames out
    writer = TrackWriter(tmp_path / "mic.wav", 48000, channels=4, start=0.0)
    writer.write(chunk(4800 * 4, value=300), now=0.1)
    writer.close(now=0.1)
    with wave.open(str(tmp_path / "mic.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 1600)
        assert int.from_bytes(w.readframes(1), "little", signed=True) == 300


def test_track_writer_carries_partial_groups_between_callbacks(tmp_path):
    writer = TrackWriter(tmp_path / "system.wav", 48000, channels=2, start=0.0)
    writer.write(chunk(5), now=0.0)   # 5 samples: two output samples need 6 each
    assert writer.frames_written == 0
    writer.write(chunk(7), now=0.0)   # 12 samples in total
    assert writer.frames_written == 2


def test_session_folder_and_round_trip(tmp_path):
    session = MeetingSession.create(tmp_path, "Acme Q4 review!", "client feedback", now=datetime(2026, 10, 28, 15, 30, 5))
    assert session.folder.name == "2026-10-28_153005_acme-q4-review"
    info = session.load_info()
    assert info.title == "Acme Q4 review!" and info.meeting_type == "client feedback"

    session.save_anchors("[00:00:05] ! budget approved\n")
    assert session.load_anchors().strip() == "[00:00:05] ! budget approved"

    transcript = Transcript(segments=[Segment(start=5, speaker="Me", text="Budget approved.")])
    session.save_transcript(transcript)
    assert parse_transcript(session.transcript_file.read_text(encoding="utf-8")).segments[0].speaker == "Me"


def test_merge_interleaves_tracks_by_time():
    me = Transcript(segments=[Segment(start=0, speaker="Me", text="Hi"), Segment(start=10, speaker="Me", text="Agreed")])
    others = Transcript(segments=[Segment(start=4, speaker="Others", text="Can we move the date?")])
    merged = merge_transcripts([me, others])
    assert [s.speaker for s in merged.segments] == ["Me", "Others", "Me"]
