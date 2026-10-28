"""Load plain-text transcripts and split them into chunks a small local model can handle.

Expected line format (speaker optional):

    [00:01:05] Rahul: We can live with the new timeline.
"""

from __future__ import annotations

import re

from .anchors import TIMESTAMP_RE, parse_timestamp
from .models import Segment, Transcript

SPEAKER_RE = re.compile(r"^([A-Za-z][\w .'-]{0,40}):\s+")


def parse_transcript(text: str) -> Transcript:
    segments: list[Segment] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = TIMESTAMP_RE.match(line)
        if not m:
            # continuation of the previous line
            if segments:
                segments[-1].text += " " + line
            continue
        start = parse_timestamp(m)
        line = line[m.end():]
        speaker = None
        if sm := SPEAKER_RE.match(line):
            speaker = sm.group(1).strip()
            line = line[sm.end():]
        segments.append(Segment(start=start, speaker=speaker, text=line.strip()))
    return Transcript(segments=segments)


def merge_transcripts(parts: list[Transcript]) -> Transcript:
    """Interleave transcripts of aligned tracks by start time."""
    segments = [seg for part in parts for seg in part.segments]
    return Transcript(segments=sorted(segments, key=lambda seg: seg.start))


def chunk_segments(transcript: Transcript, max_chars: int = 6000) -> list[list[Segment]]:
    """Group consecutive segments into chunks of roughly max_chars rendered characters."""
    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    size = 0
    for seg in transcript.segments:
        length = len(seg.render()) + 1
        if current and size + length > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(seg)
        size += length
    if current:
        chunks.append(current)
    return chunks


def nearby_segments(
    transcript: Transcript, query: str, timestamp: float | None, k: int = 8, window: float = 180
) -> list[Segment]:
    """Cheap retrieval: segments sharing words with the query, boosted near the anchor's timestamp."""
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}

    def score(seg: Segment) -> float:
        seg_words = set(re.findall(r"[a-z0-9]+", seg.text.lower()))
        s = len(words & seg_words)
        if timestamp is not None and abs(seg.start - timestamp) <= window:
            s += 2
        return s

    ranked = sorted(transcript.segments, key=score, reverse=True)
    top = [seg for seg in ranked[:k] if score(seg) > 0]
    return sorted(top, key=lambda seg: seg.start)
