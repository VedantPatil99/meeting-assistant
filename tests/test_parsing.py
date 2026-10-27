from meeting_assistant.anchors import parse_anchors
from meeting_assistant.models import AnchorKind
from meeting_assistant.transcript import chunk_segments, nearby_segments, parse_transcript


def test_anchor_prefixes_and_timestamps():
    anchors = parse_anchors(
        "# comment\n"
        "[00:12:30] ! Launch on 15 Dec\n"
        "ctx: Rahul decides\n"
        "[05:10] internal: pricing worry\n"
        "fr: bulk upload\n"
        "plain note\n"
        "\n"
    )
    assert [a.kind for a in anchors] == [
        AnchorKind.MUST, AnchorKind.CONTEXT, AnchorKind.INTERNAL, AnchorKind.FEATURE, AnchorKind.MUST,
    ]
    assert anchors[0].timestamp == 12 * 60 + 30
    assert anchors[0].text == "Launch on 15 Dec"
    assert anchors[2].timestamp == 5 * 60 + 10
    assert [a.id for a in anchors] == [1, 2, 3, 4, 5]
    assert not anchors[1].must_include


def test_transcript_parsing(sample_dir):
    transcript = parse_transcript((sample_dir / "transcript.txt").read_text(encoding="utf-8"))
    first = transcript.segments[0]
    assert first.start == 5 and first.speaker == "Vedant"
    assert len(transcript.segments) == 21


def test_continuation_lines_join_previous_segment():
    t = parse_transcript("[00:00:01] A: hello\nworld\n[00:00:05] B: hi")
    assert t.segments[0].text == "hello world"


def test_chunking_respects_size(sample_dir):
    transcript = parse_transcript((sample_dir / "transcript.txt").read_text(encoding="utf-8"))
    chunks = chunk_segments(transcript, max_chars=500)
    assert len(chunks) > 1
    assert sum(len(c) for c in chunks) == len(transcript.segments)


def test_nearby_segments_finds_go_live(sample_dir):
    transcript = parse_transcript((sample_dir / "transcript.txt").read_text(encoding="utf-8"))
    hits = nearby_segments(transcript, "Go-live confirmed for 15 December", timestamp=105)
    assert any("20 December" in seg.text for seg in hits)
